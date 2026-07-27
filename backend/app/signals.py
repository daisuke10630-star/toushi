"""
売買シグナルの生成。

このファイルの判定基準は、分析セッションで一貫して使ってきた
以下のルールをそのままコード化したものです。ルールを変えたい場合はここを編集してください。

- パーフェクトオーダー：株価 > MA1 > MA2 > MA3 > MA4 が上昇の完成形
  （逆順であれば下降の完成形）。すべて揃っていなければ「形成途中」として減点。
- RSI：過熱・売られすぎのしきい値は時間軸ごとに timeframes.py で設定
- ボリンジャーバンド：+2σ〜+3σでのバンドウォークは強いトレンドの証拠だが、
  +2σを超えた直後は急な反落リスクがあるため「追いかけ買い非推奨」の警戒フラグを立てる
- 損切りは「エントリー目安から一定率（既定8%）下」の固定ルール
- 利確目標は BB+2σ・+3σ

■ ★（stars）が意味すること／意味しないこと
★は上記ルールのうち強気側の条件がいくつ揃ったかを加減点しただけの
「条件の揃い具合」です。**上昇確率ではありません。**
実際の値動きと突き合わせた成績は backtest.py の「AI信頼度」で別途算出します。
"""
from dataclasses import dataclass, field

import pandas as pd

from . import config, patterns
from .timeframes import TimeframeParams

# ★が表す意味の説明（フロントにそのまま表示する）
STARS_NOTE = "★は強気条件の揃い具合（最大5）。上昇確率ではありません。"


def is_overbought(latest: pd.Series, tf: TimeframeParams) -> tuple[bool, str]:
    """買われすぎかどうか。買い候補から外す判定に使う。

    高値ブレイクで買う設計は放っておくと過熱を掴むため、
    RSI過熱圏または終値が+2σ超のときは候補から除外する（10年検証で成績が改善）。
    """
    rsi = latest.get("RSI_SHORT")
    rsi_long = latest.get("RSI_LONG")
    for v, label in ((rsi, "短期"), (rsi_long, "長期")):
        if pd.notna(v) and v >= tf.rsi_overheat:
            return True, f"RSI{label} {v:.1f} が過熱圏（{tf.rsi_overheat:g}以上）"
    bb2 = latest.get("BB_PLUS_2")
    if pd.notna(bb2) and latest["close"] >= bb2:
        return True, f"終値がボリンジャーバンド+2σ（{float(bb2):,.1f}円）以上"
    return False, ""


@dataclass
class SignalResult:
    judgement: str  # "買い" / "監視" / "売り" / "様子見"
    stars: int  # 1-5
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    entry_price: float | None = None
    entry_note: str = ""
    stop_loss: float | None = None
    stop_loss_note: str = ""
    take_profit_1: float | None = None
    take_profit_1_note: str = ""
    take_profit_2: float | None = None
    take_profit_2_note: str = ""
    perfect_order: str = "未形成"  # "上昇形成" / "下降形成" / "未形成"
    patterns_detected: list[str] = field(default_factory=list)
    stars_note: str = STARS_NOTE


def _check_perfect_order(latest: pd.Series) -> str:
    ma_vals = [latest["MA1"], latest["MA2"], latest["MA3"], latest["MA4"]]
    if any(pd.isna(v) for v in ma_vals):
        return "未形成"
    price = latest["close"]
    bullish = price > ma_vals[0] > ma_vals[1] > ma_vals[2] > ma_vals[3]
    bearish = price < ma_vals[0] < ma_vals[1] < ma_vals[2] < ma_vals[3]
    if bullish:
        return "上昇形成"
    if bearish:
        return "下降形成"
    return "未形成"


def generate_signal(
    df: pd.DataFrame, tf: TimeframeParams, stop_pct: float | None = None
) -> SignalResult:
    """stop_pct を渡すと損切り幅を上書きできる（損切り幅の最適化で使用）。"""
    # バックテストでは呼び出し元が事前にラベル付け済みのdfを渡すため、その場合は再計算しない
    if "candle_label" not in df.columns:
        df = patterns.add_candle_labels(df, tf)

    latest = df.iloc[-1]
    labels = tf.ma_labels
    recent_low_window = min(len(df), tf.candle_avg_window)
    recent_low = df["low"].tail(recent_low_window).min()

    result = SignalResult(judgement="様子見", stars=3)
    result.perfect_order = _check_perfect_order(latest)

    score = 0  # 内部の加減点（★算出用。ユーザーには数値そのものは見せず★に変換）

    # --- パーフェクトオーダー評価 ---
    order_text = "＞".join(labels[k] + "線" for k in ("MA1", "MA2", "MA3", "MA4"))
    if result.perfect_order == "上昇形成":
        score += 2
        result.reasons.append(f"パーフェクトオーダー成立（株価＞{order_text}）")
    elif result.perfect_order == "下降形成":
        score -= 2
        result.warnings.append("下降のパーフェクトオーダーが成立。トレンドは弱い状態")
    else:
        result.reasons.append("移動平均線は完全整列前。トレンドの土台は形成途中")

    # --- RSI評価 ---
    rsi_s, rsi_l = latest.get("RSI_SHORT"), latest.get("RSI_LONG")
    if pd.notna(rsi_s) and pd.notna(rsi_l):
        span = f"RSI短期{tf.rsi_short}本{rsi_s:.1f}/長期{tf.rsi_long}本{rsi_l:.1f}"
        if rsi_s >= tf.rsi_overheat or rsi_l >= tf.rsi_overheat:
            result.warnings.append(f"{span}と過熱圏（しきい値{tf.rsi_overheat:g}）。飛びつきは非推奨")
            score -= 1
        elif rsi_s <= tf.rsi_oversold or rsi_l <= tf.rsi_oversold:
            result.reasons.append(
                f"{span}と売られすぎ圏（しきい値{tf.rsi_oversold:g}）。逆張り候補（要厳密な損切り）"
            )
            score += 1
        else:
            result.reasons.append(f"{span}は中立圏")

    # --- ボリンジャーバンド評価 ---
    price = latest["close"]
    bb_plus2 = latest.get("BB_PLUS_2")
    bb_plus3 = latest.get("BB_PLUS_3")
    bb_minus2 = latest.get("BB_MINUS_2")
    if pd.notna(bb_plus2) and price >= bb_plus2:
        result.warnings.append("株価が+2σを上回るゾーン。急な反落リスクに警戒（バンドウォークの可能性も）")
        score -= 1
    elif pd.notna(bb_minus2) and price <= bb_minus2:
        result.reasons.append("株価が-2σを下回るゾーン。統計的な売られすぎ、逆張り候補（順張り優先が原則）")

    # --- 出来高（価格以外の情報） ---
    # ブレイクアウトは商いを伴っているかで信頼度が変わる、というのが一般的な見方。
    # 本当に効くかは backtest で検証すること。
    vol_ratio = latest.get("VOL_RATIO")
    if config.USE_VOLUME_FEATURE and pd.notna(vol_ratio):
        if vol_ratio >= config.VOLUME_SURGE_RATIO:
            score += 1
            result.reasons.append(
                f"出来高が平常時の{vol_ratio:.1f}倍に急増。値動きに市場の関心が伴っています"
            )
        elif vol_ratio <= config.VOLUME_QUIET_RATIO:
            score -= 1
            result.warnings.append(
                f"出来高が平常時の{vol_ratio:.1f}倍と閑散。値動きの信頼性が低い状態です"
            )

    # --- 市場に対する相対強弱 ---
    rs_m = latest.get("RS_MARKET")
    if config.USE_RELATIVE_STRENGTH and pd.notna(rs_m):
        if rs_m >= config.RS_MARKET_STRONG:
            score += 1
            result.reasons.append(f"直近20日でTOPIXを{rs_m:+.1f}%上回る強さ")
        elif rs_m <= config.RS_MARKET_WEAK:
            score -= 1
            result.warnings.append(f"直近20日でTOPIXに{rs_m:+.1f}%劣後。市場に置いていかれています")
        else:
            result.reasons.append(f"直近20日のTOPIX比は{rs_m:+.1f}%とほぼ市場並み")

    # --- 業種に対する相対強弱 ---
    rs_s = latest.get("RS_SECTOR")
    if config.USE_SECTOR_STRENGTH and pd.notna(rs_s):
        if rs_s >= config.RS_SECTOR_STRONG:
            score += 1
            result.reasons.append(f"同業種平均を{rs_s:+.1f}%上回る。セクター内で優位")
        elif rs_s <= config.RS_SECTOR_WEAK:
            score -= 1
            result.warnings.append(f"同業種平均に{rs_s:+.1f}%劣後。セクター内で見劣りします")

    # --- ローソク足の直近パターン ---
    result.reasons.append(f"直近足は「{latest['candle_label']}」")

    # --- ダブルトップ／ダブルボトム ---
    for p in patterns.analyze_patterns(df, tf):
        if p.detected:
            result.patterns_detected.append(p.name)
            if p.name == "ダブルトップ":
                result.warnings.append(f"{p.name}候補: {p.note}")
                score -= 1
            else:
                result.reasons.append(f"{p.name}候補: {p.note}")
                score += 1

    # --- 総合判断 ---
    if score >= 2:
        result.judgement = "買い"
    elif score <= -2:
        result.judgement = "売り／様子見"
    else:
        result.judgement = "監視"

    result.stars = max(1, min(5, 3 + score))

    # --- エントリー ---
    # 方式は config.ENTRY_MODE で切り替える（backtest.py の約定判定と対で動く）
    ma1 = latest.get("MA1")
    mode = config.ENTRY_MODE
    if mode == "market":
        result.entry_price = round(float(price), 1)
        result.entry_note = "翌営業日の始値で成行（押し目は待たない）。現値は目安です"
    elif mode == "breakout":
        lookback = config.BREAKOUT_LOOKBACK
        recent_high = float(df["high"].tail(lookback).max())
        result.entry_price = round(recent_high, 1)
        result.entry_note = f"直近{lookback}本の高値{recent_high:,.1f}円を上抜けたら逆指値で買う（順張り）"
    elif pd.notna(ma1):
        result.entry_price = round(float(ma1), 1)
        result.entry_note = f"{labels['MA1']}線への押し目を想定（現値飛びつきは非推奨のケースが多い）"

    # --- 損切り：購入価格（エントリー目安）から一定率下 ---
    # MA1が未算出の期間は現値を購入価格とみなす
    basis = result.entry_price if result.entry_price is not None else float(price)
    atr = latest.get("ATR")
    use_atr = (
        stop_pct is None and config.uses_atr_stop() and pd.notna(atr) and float(atr) > 0
    )
    if use_atr:
        mult = config.STOP_ATR_MULTIPLE
        result.stop_loss = round(basis - mult * float(atr), 1)
        trail_note = (
            "。高値を更新するたびに切り上がります（トレーリング）"
            if config.is_trailing()
            else "。値動きの荒い銘柄ほど損切りを広く取ります"
        )
        result.stop_loss_note = (
            f"ATR（{tf.atr_period}本の平均値幅 {float(atr):,.1f}円）の{mult:g}倍下{trail_note}"
        )
    else:
        pct = stop_pct if stop_pct is not None else config.stop_loss_pct(tf.key)
        result.stop_loss = round(basis * (1 - pct), 1)
        result.stop_loss_note = f"購入価格 {basis:,.1f}円 の{pct:.0%}下（固定ルール）"

    # 8%下が直近安値より深い場合、サポート割れを見送ることになるので注記する
    if pd.notna(recent_low) and result.stop_loss < recent_low:
        result.reasons.append(
            f"損切り{result.stop_loss:,.1f}円は直近{recent_low_window}本の安値"
            f"{recent_low:,.1f}円より下。サポート割れでも切らない設定です"
        )

    # --- 利確目標：損切り幅に対する倍率（リスクリワード比）で決める ---
    # 旧版は BB+2σ / +3σ だったが、バックテストで明確に劣ったため変更。
    # BB は銘柄・局面によって現値との距離がバラバラで、損切り幅と釣り合わない。
    risk = basis - result.stop_loss
    if risk > 0:
        r1 = config.TAKE_PROFIT_R_MULTIPLE_1
        r2 = config.TAKE_PROFIT_R_MULTIPLE_2
        result.take_profit_1 = round(basis + r1 * risk, 1)
        result.take_profit_1_note = (
            f"損切り幅（{risk:,.1f}円）の{r1:g}倍＝リスクリワード比 1:{r1:g}。"
            "バックテストで最も期待値が高かった水準"
        )
        result.take_profit_2 = round(basis + r2 * risk, 1)
        result.take_profit_2_note = f"同じく{r2:g}倍＝リスクリワード比 1:{r2:g}の伸ばし目標"

    # ボリンジャーバンドの水準は参考情報として根拠に残す
    if pd.notna(bb_plus2):
        result.reasons.append(
            f"参考：ボリンジャーバンド+2σは{float(bb_plus2):,.1f}円"
            f"（旧版はここを利確目標にしていましたが、検証で劣ったため変更）"
        )

    return result
