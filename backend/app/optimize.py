"""
損切り幅と利確目標を過去データから決めるための総当たり検証（日足専用）。

■ 何を最大化するか
**売買コストを引いた後の、1トレードあたりの平均損益（期待値）**です。勝率ではありません。
勝率が高くても1回の損失が大きければ資金は減るため、勝率で選ぶと判断を誤ります。

■ 2次元で探索する理由
損切り幅だけを振った先行検証では、どの値でも期待値がマイナスでした。
損切りと利確は「リスクリワード比」として対で効くため、片方だけ動かしても
最適点には届きません。ここでは損切り × 利確の組み合わせを総当たりします。

■ 過学習（カーブフィッティング）への対策
1. 単一銘柄では決めず、複数銘柄のトレードをプールして集計する
2. 期間を前半（最適化用 / in-sample）と後半（検証用 / out-of-sample）に分け、
   両方の結果を並べて確認する。前半だけ良くて後半が悪い組み合わせは採用しない
3. 組み合わせ数を増やすほど「たまたま良い1点」が出やすくなる。
   単独のピークではなく、周囲も良い"面"になっているかを見る

■ 高速化の仕組み
★・エントリー目安・BB・ATRは損切り／利確の設定に依存しません。
backtest.collect_events で一度だけ収集し、backtest.evaluate で組み合わせだけを振ります。
"""
from dataclasses import dataclass, field

import pandas as pd

from . import backtest
from .timeframes import TimeframeParams

# 損切りの候補：固定%と、ATR（ボラティリティ）連動の両方を試す
STOP_SPECS: list[tuple] = [
    ("pct", 0.010),
    ("pct", 0.015),
    ("pct", 0.020),
    ("pct", 0.030),
    ("pct", 0.050),
    ("pct", 0.080),
    ("atr", 1.0),
    ("atr", 1.5),
    ("atr", 2.0),
    ("atr", 3.0),
]

# 利確の候補：BB基準・リスクリワード比固定・固定%の3系統
TARGET_SPECS: list[tuple] = [
    ("bb", 2),
    ("bb", 3),
    ("r", 1.0),
    ("r", 1.5),
    ("r", 2.0),
    ("r", 3.0),
    ("pct", 0.03),
    ("pct", 0.05),
    ("pct", 0.10),
]

# 前半何割を最適化用（in-sample）にするか
IN_SAMPLE_RATIO = 0.7


def label_spec(spec: tuple) -> str:
    kind, value = spec
    if kind == "pct":
        return f"{value:.1%}"
    if kind == "atr":
        return f"ATR×{value:g}"
    if kind == "bb":
        return f"+{value:g}σ"
    if kind == "r":
        return f"RR1:{value:g}"
    return str(spec)


@dataclass
class Cell:
    """ある(損切り, 利確)の組み合わせでの集計結果"""

    stop_spec: tuple
    target_spec: tuple
    trades: int = 0
    wins: int = 0
    not_filled: int = 0
    returns: list[float] = field(default_factory=list)

    def merge(self, stats: dict) -> None:
        self.wins += stats["wins"]
        self.trades += stats["wins"] + stats["losses"]
        self.not_filled += stats["not_filled"]
        self.returns.extend(stats["returns"])

    @property
    def win_rate(self) -> float | None:
        return self.wins / self.trades * 100 if self.trades else None

    @property
    def expectancy(self) -> float | None:
        """コスト控除後の1トレードあたり平均損益(%)。これが採用の判断軸。"""
        return sum(self.returns) / len(self.returns) if self.returns else None

    @property
    def total_return(self) -> float:
        return sum(self.returns)

    @property
    def label(self) -> str:
        return f"損切り{label_spec(self.stop_spec)} / 利確{label_spec(self.target_spec)}"


def _split(events: list[backtest.SignalEvent], n_bars: int) -> tuple[list, list]:
    boundary = int(n_bars * IN_SAMPLE_RATIO)
    return (
        [e for e in events if e.index < boundary],
        [e for e in events if e.index >= boundary],
    )


def sweep(
    frames: dict[str, pd.DataFrame],
    tf: TimeframeParams,
    star_threshold: int,
    cost_pct: float | None = None,
    stop_specs: list[tuple] | None = None,
    target_specs: list[tuple] | None = None,
) -> dict[str, dict[tuple, Cell]]:
    """複数銘柄をまとめて2次元スイープする。

    frames: {銘柄コード: indicators.compute_all 済みのDataFrame}
    戻り値: {"in"|"out"|"all": {(stop_spec, target_spec): Cell}}
    """
    stops = stop_specs or STOP_SPECS
    targets = target_specs or TARGET_SPECS
    combos = [(s, t) for s in stops for t in targets]

    buckets: dict[str, dict[tuple, Cell]] = {
        group: {c: Cell(stop_spec=c[0], target_spec=c[1]) for c in combos}
        for group in ("in", "out", "all")
    }

    for df in frames.values():
        labeled, events = backtest.collect_events(df, tf, min_stars=star_threshold)
        if not events:
            continue
        in_events, out_events = _split(events, len(df))

        for group, evs in (("in", in_events), ("out", out_events), ("all", events)):
            if not evs:
                continue
            for combo in combos:
                stats = backtest.evaluate(
                    labeled, evs, tf, combo[0], combo[1], cost_pct=cost_pct
                )
                buckets[group][combo].merge(stats)

    return buckets


def best(cells: dict[tuple, Cell], min_trades: int) -> Cell | None:
    """十分な試行回数がある中で、期待値が最大の組み合わせを返す。"""
    eligible = [
        c for c in cells.values() if c.trades >= min_trades and c.expectancy is not None
    ]
    return max(eligible, key=lambda c: c.expectancy) if eligible else None


def robust_best(
    in_cells: dict[tuple, Cell], out_cells: dict[tuple, Cell], min_trades: int
) -> Cell | None:
    """前半・後半の**両方**で期待値がプラスの組み合わせのうち、
    後半（未知データ相当）の期待値が最大のものを返す。

    前半だけ良い組み合わせは過学習の疑いが強いので除外する。
    """
    candidates = []
    for combo, ci in in_cells.items():
        co = out_cells.get(combo)
        if co is None:
            continue
        if ci.trades < min_trades or co.trades < min_trades:
            continue
        if ci.expectancy is None or co.expectancy is None:
            continue
        if ci.expectancy > 0 and co.expectancy > 0:
            candidates.append(co)
    return max(candidates, key=lambda c: c.expectancy) if candidates else None
