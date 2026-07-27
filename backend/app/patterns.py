"""
ローソク足パターンの検出。

大陽線・大陰線の判定は「直近N本の値幅（High-Low）の平均に対して、
その足の実体（|Close-Open|）が1.5倍以上あるか」という相対基準を使う。
固定の円建て閾値にすると銘柄間の株価水準の違いで機能しなくなるため。

ダブルトップ・ダブルボトムは簡易のピーク検出ベース。直近Nバーの中で
近い高さの2つの山（谷）とその間の谷（山）を探す、教科書的な最小実装。
本格運用では誤検出があり得るため、あくまで「候補の提示」として扱うこと。

窓幅（N）はすべて timeframes.TimeframeParams から受け取るため、
分足では日足より広い窓を使ってノイズを抑えています。
"""
from dataclasses import dataclass

import numpy as np
import pandas as pd

from .timeframes import TimeframeParams

# 実体が平均値幅の何倍以上なら「大陽線／大陰線」とみなすか
LARGE_BODY_RATIO = 1.5


def add_candle_labels(df: pd.DataFrame, tf: TimeframeParams) -> pd.DataFrame:
    """各足に「大陽線/陽線/大陰線/陰線/同時線」のラベルを付ける。

    バックテストで何百回も呼ばれるため、行ループを使わずベクトル化している。
    """
    df = df.copy()
    window = tf.candle_avg_window
    avg_range = (
        (df["high"] - df["low"])
        .rolling(window=window, min_periods=max(2, window // 4))
        .mean()
        .fillna(0)
    )

    body = df["close"] - df["open"]
    is_large = (avg_range > 0) & (body.abs() >= LARGE_BODY_RATIO * avg_range)

    labels = np.where(
        body > 0,
        np.where(is_large, "大陽線", "陽線"),
        np.where(body < 0, np.where(is_large, "大陰線", "陰線"), "同時線"),
    )
    df["candle_label"] = labels
    return df


@dataclass
class PatternResult:
    name: str
    detected: bool
    note: str


def _find_local_extrema(series: pd.Series, order: int, kind: str) -> list[int]:
    """単純な局所極値検出（order=前後何本と比較するか）"""
    idxs = []
    vals = series.to_numpy()
    n = len(vals)
    for i in range(order, n - order):
        window = vals[i - order : i + order + 1]
        if kind == "max" and vals[i] == window.max():
            idxs.append(i)
        elif kind == "min" and vals[i] == window.min():
            idxs.append(i)
    return idxs


def detect_double_top(
    df: pd.DataFrame, tf: TimeframeParams, tolerance: float = 0.02
) -> PatternResult:
    lookback = tf.pattern_lookback
    window = df.tail(lookback).reset_index(drop=True)
    peaks = _find_local_extrema(window["high"], order=tf.extrema_order, kind="max")
    if len(peaks) < 2:
        return PatternResult("ダブルトップ", False, "直近の値動きに明確な二つの山が見られません")

    p1, p2 = peaks[-2], peaks[-1]
    h1, h2 = window["high"].iloc[p1], window["high"].iloc[p2]
    close_enough = abs(h1 - h2) / max(h1, h2) <= tolerance
    trough_between = window["low"].iloc[p1:p2].min() if p2 > p1 else None
    valid_dip = trough_between is not None and trough_between < min(h1, h2) * 0.97

    if close_enough and valid_dip:
        return PatternResult(
            "ダブルトップ", True,
            f"直近{lookback}本以内に近い高さの山が2回（{h1:.0f}円 / {h2:.0f}円）出現。天井圏の警戒サイン候補"
        )
    return PatternResult("ダブルトップ", False, "山は検出したが、高さの一致度が低く典型形とは言えません")


def detect_double_bottom(
    df: pd.DataFrame, tf: TimeframeParams, tolerance: float = 0.02
) -> PatternResult:
    lookback = tf.pattern_lookback
    window = df.tail(lookback).reset_index(drop=True)
    troughs = _find_local_extrema(window["low"], order=tf.extrema_order, kind="min")
    if len(troughs) < 2:
        return PatternResult("ダブルボトム", False, "直近の値動きに明確な二つの谷が見られません")

    t1, t2 = troughs[-2], troughs[-1]
    l1, l2 = window["low"].iloc[t1], window["low"].iloc[t2]
    close_enough = abs(l1 - l2) / max(l1, l2) <= tolerance
    peak_between = window["high"].iloc[t1:t2].max() if t2 > t1 else None
    valid_bounce = peak_between is not None and peak_between > max(l1, l2) * 1.03

    if close_enough and valid_bounce:
        return PatternResult(
            "ダブルボトム", True,
            f"直近{lookback}本以内に近い安さの谷が2回（{l1:.0f}円 / {l2:.0f}円）出現。底打ち圏の候補"
        )
    return PatternResult("ダブルボトム", False, "谷は検出したが、安さの一致度が低く典型形とは言えません")


def detect_bearish_divergence(
    df: pd.DataFrame, tf: TimeframeParams, lookback: int = 60
) -> tuple[bool, str]:
    """弱気ダイバージェンス：株価は高値を切り上げたのにRSIは切り下げている状態。

    「値段は上がっているが、上げる力は弱まっている」というサインとされる。
    高値ブレイクで買う設計と相性が悪い可能性があるため、除外の判定材料にする。
    """
    if "RSI_SHORT" not in df.columns:
        return False, ""
    window = df.tail(lookback).reset_index(drop=True)
    peaks = _find_local_extrema(window["high"], order=tf.extrema_order, kind="max")
    if len(peaks) < 2:
        return False, ""

    p1, p2 = peaks[-2], peaks[-1]
    h1, h2 = float(window["high"].iloc[p1]), float(window["high"].iloc[p2])
    r1, r2 = window["RSI_SHORT"].iloc[p1], window["RSI_SHORT"].iloc[p2]
    if pd.isna(r1) or pd.isna(r2):
        return False, ""

    if h2 > h1 and r2 < r1:
        return True, (
            f"株価は{h1:,.0f}→{h2:,.0f}円と高値を切り上げたのに、"
            f"RSIは{r1:.1f}→{r2:.1f}と切り下げ（勢いの衰え）"
        )
    return False, ""


def detect_bullish_divergence(
    df: pd.DataFrame, tf: TimeframeParams, lookback: int = 60
) -> tuple[bool, str]:
    """強気ダイバージェンス：株価は安値を切り下げたのにRSIは切り上げている状態。"""
    if "RSI_SHORT" not in df.columns:
        return False, ""
    window = df.tail(lookback).reset_index(drop=True)
    troughs = _find_local_extrema(window["low"], order=tf.extrema_order, kind="min")
    if len(troughs) < 2:
        return False, ""

    t1, t2 = troughs[-2], troughs[-1]
    l1, l2 = float(window["low"].iloc[t1]), float(window["low"].iloc[t2])
    r1, r2 = window["RSI_SHORT"].iloc[t1], window["RSI_SHORT"].iloc[t2]
    if pd.isna(r1) or pd.isna(r2):
        return False, ""

    if l2 < l1 and r2 > r1:
        return True, (
            f"株価は{l1:,.0f}→{l2:,.0f}円と安値を切り下げたのに、"
            f"RSIは{r1:.1f}→{r2:.1f}と切り上げ（下げ渋り）"
        )
    return False, ""


def analyze_patterns(df: pd.DataFrame, tf: TimeframeParams) -> list[PatternResult]:
    return [detect_double_top(df, tf), detect_double_bottom(df, tf)]
