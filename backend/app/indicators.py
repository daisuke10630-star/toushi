"""
テクニカル指標の計算。分析セッションで一貫して使ってきた指標のみを実装。
架空の指標や、根拠の薄い独自指標は追加しない。

期間などのパラメータは timeframes.TimeframeParams から受け取るため、
日足でも分足でも同じコードで計算できます。
"""
import numpy as np
import pandas as pd

from .timeframes import TimeframeParams


def add_moving_averages(df: pd.DataFrame, tf: TimeframeParams) -> pd.DataFrame:
    df = df.copy()
    for name, period in tf.ma_periods.items():
        df[name] = df["close"].rolling(window=period, min_periods=period).mean()
    return df


def add_bollinger_bands(df: pd.DataFrame, tf: TimeframeParams) -> pd.DataFrame:
    df = df.copy()
    period = tf.bb_period
    mid = df["close"].rolling(window=period, min_periods=period).mean()
    std = df["close"].rolling(window=period, min_periods=period).std(ddof=0)
    df["BB_MID"] = mid
    for k in (1, 2, 3):
        df[f"BB_PLUS_{k}"] = mid + k * std
        df[f"BB_MINUS_{k}"] = mid - k * std
    return df


def _rsi(series: pd.Series, period: int) -> pd.Series:
    delta = series.diff()
    gain = delta.clip(lower=0)
    loss = -delta.clip(upper=0)
    # Wilderの平滑化（一般的なRSI計算式）
    avg_gain = gain.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    avg_loss = loss.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    rs = avg_gain / avg_loss.replace(0, np.nan)
    rsi = 100 - (100 / (1 + rs))
    rsi = rsi.fillna(50)  # avg_lossが0（一方的な上昇）の場合は100に近づくが、初期は中立扱い
    return rsi


def add_rsi(df: pd.DataFrame, tf: TimeframeParams) -> pd.DataFrame:
    df = df.copy()
    df["RSI_SHORT"] = _rsi(df["close"], tf.rsi_short)
    df["RSI_LONG"] = _rsi(df["close"], tf.rsi_long)
    return df


def add_atr(df: pd.DataFrame, tf: TimeframeParams) -> pd.DataFrame:
    """ATR（平均真の値幅）。銘柄ごとのボラティリティに応じた損切り幅に使う。

    真の値幅 = max(高値-安値, |高値-前日終値|, |安値-前日終値|)
    ギャップ（窓）を含めて値幅を測れるのが、単純な高値-安値との違い。
    """
    df = df.copy()
    prev_close = df["close"].shift(1)
    true_range = pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    # RSIと同じくWilderの平滑化
    period = tf.atr_period
    df["ATR"] = true_range.ewm(alpha=1 / period, min_periods=period, adjust=False).mean()
    return df


def compute_all(df: pd.DataFrame, tf: TimeframeParams) -> pd.DataFrame:
    df = add_moving_averages(df, tf)
    df = add_bollinger_bands(df, tf)
    df = add_rsi(df, tf)
    df = add_atr(df, tf)
    return df
