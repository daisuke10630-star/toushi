"""
「AI信頼度」＝ 過去データで同じルールを実際に売買したときの成功率。

■ これが何であって、何でないか
- **AI・機械学習は一切使っていません。** 実体は素朴なバックテストの勝率です。
  項目名はご要望に合わせていますが、中身を偽らないようこのファイルに明記します。
- 出しているのは「この銘柄の直近データで、同じ★水準のシグナルが出たとき、
  提示した損切り・利確ルール通りに売買していたら何%成功したか」という実測値です。
- **将来の上昇確率ではありません。** 母数が小さく、期間も限られています。

■ 検証手順（先読みバイアスを避けるため、各時点で「その時点までのデータ」だけを使う）
1. 過去の各バー i について、df[:i+1] だけを渡してシグナルを再計算する
   （移動平均・RSI・BBはすべて後ろ向きのrolling計算なので、未来の値は混入しない）
2. ★が現在と同じ水準以上だったバーを「シグナル発生」とみなす
3. その後 forward_bars 本以内に、エントリー目安（MA1）まで押したら約定とみなす
4. 約定後、損切りライン（-8%）と利確目標①（BB+2σ）のどちらに先に触れたかで勝敗を決める
5. どちらにも触れずに期間終了した場合は、最終終値がエントリーより上なら成功

■ 意図的に厳しめ（保守的）にしている点
- 同じバーの中で損切りと利確の両方に触れた場合は「損切りが先」とみなす
- エントリー目安まで押さずに上昇していった分は「未約定」として勝ちに数えない

■ 反映していない現実の要素
- 手数料・スプレッド・スリッページ・約定滑り
- 板の厚さ（提示価格で必ず約定する前提）
- 同一銘柄の直近データのみでの検証（他銘柄・他期間への一般化は保証されない）
"""
# 型注釈を文字列として遅延評価する。
# Python 3.14 では既定でこの挙動だが、3.12 以下では定義順に依存してしまうため明示する
# （_find_fill が SignalEvent より前に定義されており、これがないと NameError になる）。
from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from . import config, patterns, signals
from .timeframes import TimeframeParams

# 計算量が膨らみすぎないよう、検証する時点の最大数
MAX_EVALUATIONS = 400


@dataclass
class BacktestResult:
    available: bool
    label: str  # 表示用の一言（"62%（20回中12回成功）" など）
    win_rate: float | None = None
    samples: int = 0
    wins: int = 0
    losses: int = 0
    not_filled: int = 0
    avg_return_pct: float | None = None
    star_threshold: int = 0
    horizon: str = ""
    method: str = ""
    caveats: list[str] = field(default_factory=list)
    # --- 対照実験：シグナルを使わず「毎バー買って持つだけ」の成績 ---
    benchmark_expectancy: float | None = None
    benchmark_samples: int = 0
    edge: float | None = None  # シグナルの期待値 − 単純保有の期待値
    edge_label: str = ""


_CAVEATS = [
    "AI・機械学習ではなく、過去データでの売買シミュレーション結果です",
    f"往復の売買コスト{config.TRADING_COST_PCT:.2%}を差し引いた後の数値です",
    "板の厚さは考慮していません（提示価格で必ず約定する前提）",
    "同じ足で損切りと利確の両方に触れた場合は損切り扱い（保守的）",
    "この銘柄・この期間のみの実測値で、将来を予測するものではありません",
]


def _find_fill(
    future: pd.DataFrame, ev: SignalEvent, entry_spec: tuple
) -> tuple[int, float] | None:
    """エントリー方式に応じて、いつ・いくらで約定したかを返す。

    entry_spec:
      ("pullback",) … エントリー目安（MA1）まで押したら指値で約定（従来方式）
      ("market",)   … 待たずに翌足の始値で成行約定
      ("breakout",) … 直近高値を上抜けたら逆指値で約定（順張り）
    戻り値: (何本目で約定したか, 約定価格) / 期間内に約定しなければ None
    """
    kind = entry_spec[0]

    if kind == "market":
        return 0, float(future["open"].iloc[0])

    if kind == "pullback":
        for j, row in enumerate(future.itertuples(index=False)):
            if row.low <= ev.entry:
                return j, ev.entry
        return None

    if kind == "breakout":
        level = ev.breakout_level
        if level is None:
            return None
        for j, row in enumerate(future.itertuples(index=False)):
            if row.high >= level:
                return j, level
        return None

    raise ValueError(f"未対応のエントリー方式: {entry_spec}")


def _simulate_trade(
    future: pd.DataFrame,
    ev: SignalEvent,
    entry_spec: tuple,
    stop_spec: tuple,
    target_spec: tuple,
) -> tuple[str, float | None, int]:
    """1回の売買をシミュレートする。

    損切り・利確は「実際の約定価格」を基準に置き直す（成行やブレイクアウトでは
    約定価格が事前にわからないため）。
    戻り値: (結果, リターン%, 保有バー数) 結果は "win" / "loss" / "not_filled" / "skip"

    保有バー数を返すのは「1日あたりのリターン」を計算するため。
    短期で回転させれば同じ期間により多くの取引ができるので、
    1トレードあたりの期待値だけで比べると保有期間の長い設定が不当に有利に見える。
    """
    fill = _find_fill(future, ev, entry_spec)
    if fill is None:
        return "not_filled", None, 0
    j, price = fill
    if price <= 0:
        return "skip", None, 0

    stop = resolve_stop(price, ev, stop_spec)
    if stop is None or stop >= price:
        return "skip", None, 0
    target = resolve_target(price, stop, ev, target_spec)
    if target is None or target <= price:
        return "skip", None, 0

    # トレーリングストップ：高値を更新するたびに損切りラインを切り上げる。
    # 伸びる銘柄を伸ばしつつ、反落したら利益を確定できる方式。
    trailing = stop_spec[0].startswith("trail")
    trail_value = stop_spec[1] if trailing else None
    peak = price

    # 約定した足も含めて、損切りと利確のどちらに先に触れたかを見る。
    # 同じ足で両方に触れた場合は損切りを優先（保守的な評価）。
    segment = future.iloc[j:]
    for held, row in enumerate(segment.itertuples(index=False), start=1):
        if row.low <= stop:
            return "loss", (stop / price - 1) * 100, held
        if row.high >= target:
            return "win", (target / price - 1) * 100, held
        if trailing and row.high > peak:
            peak = row.high
            if stop_spec[0] == "trail_atr" and ev.atr:
                lifted = peak - trail_value * ev.atr
            else:
                lifted = peak * (1 - trail_value)
            # 引き上げるだけで、下げることはしない
            stop = max(stop, lifted)

    # 期間内にどちらにも触れなかった場合は最終終値で評価する
    last_close = float(segment["close"].iloc[-1])
    return (
        "win" if last_close > price else "loss",
        (last_close / price - 1) * 100,
        len(segment),
    )


@dataclass
class SignalEvent:
    """過去のある時点で発生したシグナル。

    ★・エントリー目安・利確目標は損切り幅に依存しないため、一度集めておけば
    損切り幅を振って再評価するときに再計算しなくて済む（optimize.py で使用）。
    """

    index: int
    stars: int
    entry: float
    target: float  # 現行ルールの利確目標①
    bb2: float | None = None  # ボリンジャーバンド+2σ（スイープ比較用に生の値を保持）
    bb3: float | None = None  # 同 +3σ
    atr: float | None = None  # その時点のATR。ボラ連動の損切り幅に使う
    breakout_level: float | None = None  # 直近高値。ブレイクアウト・エントリーの発注価格
    close: float | None = None  # シグナル発生時の終値


def collect_events(
    df: pd.DataFrame, tf: TimeframeParams, min_stars: int = 1
) -> tuple[pd.DataFrame, list[SignalEvent]]:
    """過去の各時点でシグナルを再計算し、イベントとして集める。

    各時点で df[:i+1] だけを渡すため、未来の情報は混入しない。
    戻り値の DataFrame はローソク足ラベル付き（呼び出し側の再計算を避けるため）。
    """
    forward = tf.forward_bars
    warmup = max(max(tf.ma_periods.values()), tf.bb_period, tf.rsi_long) + tf.extrema_order
    start = warmup
    end = len(df) - forward - 1

    if end <= start:
        return df, []

    # ローソク足ラベルは後ろ向きのrolling計算なので、全体で先に付けても先読みにならない
    labeled = patterns.add_candle_labels(df, tf)
    stride = max(1, (end - start) // MAX_EVALUATIONS)

    events: list[SignalEvent] = []
    for i in range(start, end, stride):
        sig = signals.generate_signal(labeled.iloc[: i + 1], tf)
        if sig.stars < min_stars:
            continue
        if sig.entry_price is None or sig.take_profit_1 is None:
            continue
        # BB・ATRは指標列から直接読む。利確目標の定義を変えてもスイープ側の
        # ("bb", n) 指定が正しく BB を指すようにするため。
        row = labeled.iloc[i]

        def _num(col: str) -> float | None:
            v = row.get(col)
            return float(v) if v is not None and pd.notna(v) else None

        lookback = config.BREAKOUT_LOOKBACK
        recent_high = float(labeled["high"].iloc[max(0, i - lookback + 1) : i + 1].max())

        events.append(
            SignalEvent(
                index=i,
                stars=sig.stars,
                entry=sig.entry_price,
                target=sig.take_profit_1,
                bb2=_num("BB_PLUS_2"),
                bb3=_num("BB_PLUS_3"),
                atr=_num("ATR"),
                breakout_level=recent_high,
                close=_num("close"),
            )
        )
    return labeled, events


def resolve_stop(entry: float, ev: SignalEvent, spec: tuple) -> float | None:
    """損切りの初期価格を決める。

    spec:
      ("pct", 0.08)       … 約定価格の一定率下（固定）
      ("atr", 2.0)        … ATRの倍数だけ下（固定）
      ("trail_atr", 1.5)  … 同上だが、高値更新に合わせて切り上げる（トレーリング）
      ("trail_pct", 0.05) … 高値から一定率下を追いかける（トレーリング）
    トレーリングの切り上げは _simulate_trade 側で行う。ここでは初期値だけ返す。

    entry は実際の約定価格。エントリー方式によって約定価格が変わるため、
    イベントの想定価格ではなく約定価格を基準にする。
    """
    kind, value = spec
    if kind in ("pct", "trail_pct"):
        return entry * (1 - value)
    if kind in ("atr", "trail_atr"):
        if ev.atr is None or ev.atr <= 0:
            return None
        return entry - value * ev.atr
    raise ValueError(f"未対応の損切り指定: {spec}")


def resolve_target(entry: float, stop: float, ev: SignalEvent, spec: tuple) -> float | None:
    """利確価格を決める。

    spec:
      ("bb", 2)   … ボリンジャーバンド+2σ（旧版の利確目標①）
      ("bb", 3)   … +3σ
      ("r", 2.0)  … 損切り幅の2倍の値幅（リスクリワード比 1:2 を固定する）
      ("pct", 0.05) … エントリーから+5%
      ("rule",)   … 現行ルールがそのまま提示している利確目標（値を持たない1要素タプル）
    """
    kind = spec[0]
    if kind == "rule":
        return ev.target
    value = spec[1]
    if kind == "bb":
        return ev.bb2 if value == 2 else ev.bb3
    if kind == "r":
        risk = entry - stop
        return entry + value * risk if risk > 0 else None
    if kind == "pct":
        return entry * (1 + value)
    raise ValueError(f"未対応の利確指定: {spec}")


def evaluate(
    labeled: pd.DataFrame,
    events: list[SignalEvent],
    tf: TimeframeParams,
    stop_spec: tuple | float,
    target_spec: tuple = ("rule",),
    cost_pct: float | None = None,
    entry_spec: tuple | None = None,
) -> dict:
    """収集済みイベントを、指定したエントリー・損切り・利確ルールで評価し直す。

    stop_spec に float を渡した場合は ("pct", 値) として扱う（後方互換）。
    cost_pct は往復の売買コスト。各トレードのリターンから差し引く。
    """
    if isinstance(stop_spec, (int, float)):
        stop_spec = ("pct", float(stop_spec))
    cost = config.TRADING_COST_PCT if cost_pct is None else cost_pct
    entry_spec = entry_spec or (config.ENTRY_MODE,)

    forward = tf.forward_bars
    wins = losses = not_filled = skipped = 0
    returns: list[float] = []
    holding: list[int] = []

    for ev in events:
        future = labeled.iloc[ev.index + 1 : ev.index + 1 + forward]
        if future.empty:
            continue

        outcome, ret, held = _simulate_trade(
            future, ev, entry_spec, stop_spec, target_spec
        )
        if outcome == "not_filled":
            not_filled += 1
            continue
        if outcome == "skip":
            skipped += 1
            continue
        holding.append(held)

        # 売買コストを引いた後のリターンで勝敗を決め直す
        net = None if ret is None else ret - cost * 100
        if net is not None:
            returns.append(net)
            if net > 0:
                wins += 1
            else:
                losses += 1
        elif outcome == "win":
            wins += 1
        else:
            losses += 1

    return {
        "wins": wins,
        "losses": losses,
        "not_filled": not_filled,
        "skipped": skipped,
        "returns": returns,
        "holding": holding,
    }


def benchmark(df: pd.DataFrame, tf: TimeframeParams, cost_pct: float | None = None) -> dict:
    """対照実験：シグナルを一切使わず、毎バー終値で買って保有期間まで持つだけの成績。

    シグナルの価値は「これをどれだけ上回るか」でしか測れない。
    上昇相場では何を買っても勝つので、勝率や期待値の絶対値だけを見ると
    「良いルールを見つけた」と誤解してしまう。
    損切りだけは同じ条件（購入価格の一定率下）を適用する。
    """
    cost = config.TRADING_COST_PCT if cost_pct is None else cost_pct
    # 損切り方式は戦略側と揃える（違う条件で比べると比較にならない）
    spec_kind, spec_value = config.stop_spec(tf.key)
    forward = tf.forward_bars
    warmup = max(max(tf.ma_periods.values()), tf.bb_period, tf.rsi_long)
    end = len(df) - forward - 1
    if end <= warmup:
        return {"expectancy": None, "samples": 0}

    stride = max(1, (end - warmup) // MAX_EVALUATIONS)
    returns: list[float] = []
    for i in range(warmup, end, stride):
        entry = float(df["close"].iloc[i])
        if spec_kind == "atr":
            atr = df["ATR"].iloc[i] if "ATR" in df.columns else None
            if atr is None or pd.isna(atr) or atr <= 0:
                continue
            stop = entry - spec_value * float(atr)
        else:
            stop = entry * (1 - spec_value)
        if stop >= entry:
            continue
        future = df.iloc[i + 1 : i + 1 + forward]
        if future.empty:
            continue
        if float(future["low"].min()) <= stop:
            ret = (stop / entry - 1) * 100
        else:
            ret = (float(future["close"].iloc[-1]) / entry - 1) * 100
        returns.append(ret - cost * 100)

    if not returns:
        return {"expectancy": None, "samples": 0}
    return {"expectancy": sum(returns) / len(returns), "samples": len(returns)}


def run(df: pd.DataFrame, tf: TimeframeParams, star_threshold: int) -> BacktestResult:
    """指定★水準以上のシグナルについて、過去の成績を集計する。

    df は indicators.compute_all 済みであること。
    """
    horizon = tf.forward_label
    entry_desc = {
        "breakout": f"直近{config.BREAKOUT_LOOKBACK}本の高値を上抜けたら逆指値で約定",
        "market": "翌足の始値で成行約定",
        "pullback": "エントリー目安まで押したら指値で約定",
    }.get(config.ENTRY_MODE, config.ENTRY_MODE)
    kind, value = config.stop_spec(tf.key)
    stop_desc = {
        "atr": f"ATR×{value:g}下（固定）",
        "trail_atr": f"高値からATR×{value:g}下を追いかける（トレーリング）",
        "trail_pct": f"高値から{value:.2%}下を追いかける（トレーリング）",
    }.get(kind, f"{value:.2%}下（固定）")
    method = (
        f"★{star_threshold}以上のシグナル発生後、{horizon}以内に{entry_desc}。"
        f"損切り（{stop_desc}）と利確目標①のどちらに先に触れたかで判定し、"
        f"往復コスト{config.TRADING_COST_PCT:.2%}を差し引いて集計"
    )

    labeled, events = collect_events(df, tf, min_stars=star_threshold)
    if not events:
        return BacktestResult(
            available=False,
            label="データ不足",
            star_threshold=star_threshold,
            horizon=horizon,
            method=method,
            caveats=["検証に必要な期間のデータがありません"] + _CAVEATS,
        )

    stats = evaluate(labeled, events, tf, config.stop_spec(tf.key))
    wins, losses, not_filled = stats["wins"], stats["losses"], stats["not_filled"]
    returns = stats["returns"]
    samples = wins + losses
    if samples < config.BACKTEST_MIN_SAMPLES:
        return BacktestResult(
            available=False,
            label=f"データ不足（約定{samples}回）",
            samples=samples,
            wins=wins,
            losses=losses,
            not_filled=not_filled,
            star_threshold=star_threshold,
            horizon=horizon,
            method=method,
            caveats=[
                f"信頼度を出すには最低{config.BACKTEST_MIN_SAMPLES}回の約定が必要です"
            ]
            + _CAVEATS,
        )

    win_rate = wins / samples * 100
    avg_return = sum(returns) / len(returns) if returns else None

    # シグナルを使わない場合との比較。ここがプラスでなければシグナルに価値はない
    bench = benchmark(df, tf)
    bench_exp = bench["expectancy"]
    edge = None
    if avg_return is not None and bench_exp is not None:
        edge = avg_return - bench_exp
        if edge > 0:
            edge_label = f"単純保有より {edge:+.2f}% 有利"
        else:
            edge_label = f"単純保有より {edge:+.2f}% 不利（シグナルを使わないほうが良い結果）"
    else:
        edge_label = "比較できるデータがありません"

    return BacktestResult(
        available=True,
        label=f"{win_rate:.0f}%（{samples}回中{wins}回成功）",
        win_rate=round(win_rate, 1),
        samples=samples,
        wins=wins,
        losses=losses,
        not_filled=not_filled,
        avg_return_pct=round(avg_return, 2) if avg_return is not None else None,
        benchmark_expectancy=round(bench_exp, 2) if bench_exp is not None else None,
        benchmark_samples=bench["samples"],
        edge=round(edge, 2) if edge is not None else None,
        edge_label=edge_label,
        star_threshold=star_threshold,
        horizon=horizon,
        method=method,
        caveats=_CAVEATS,
    )
