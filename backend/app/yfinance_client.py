"""
Yahoo!ファイナンス（yfinance）から日本株の足データを取得するクライアント。

J-Quantsの無料プランが約12週間の配信遅延を持つのに対し、こちらは前営業日の終値まで
取得できるため、既定の取得元にしています。APIキーの登録は不要です。

制約（正直な限界点）：
- 非公式ライブラリです。Yahoo側の仕様変更で動かなくなる可能性があります。
- ザラ場中の値はおおむね15〜20分遅延です。秒単位の板情報は取得できません。
- 分足の取得可能期間は Yahoo 側の制限で決まります（1分足=直近7日、5分足=直近60日）。

戻り値の形（pandas.DataFrame、列: date, open, high, low, close, volume）は
どの時間軸でも同一です。indicators.py 以降は時間軸を意識せず処理できます。
"""
import asyncio

import pandas as pd
import yfinance as yf

from .errors import DataSourceFetchError
from .timeframes import TimeframeParams

_REQUIRED = ["date", "open", "high", "low", "close", "volume"]


def to_symbol(code: str) -> str:
    """証券コード（例: "8136"）を Yahoo のティッカー（例: "8136.T"）に変換する。"""
    code = code.strip()
    if "." in code:  # 既に "8136.T" 形式で渡された場合はそのまま使う
        return code
    return f"{code}.T"


def normalize(raw: pd.DataFrame, tf: TimeframeParams, label: str) -> pd.DataFrame:
    """yfinanceの生DataFrameを共通フォーマットに整える。"""
    df = raw.reset_index().rename(
        columns={
            # 日足は "Date"、分足は "Datetime" が索引名になる
            "Date": "date",
            "Datetime": "date",
            "index": "date",
            "Open": "open",
            "High": "high",
            "Low": "low",
            "Close": "close",
            "Volume": "volume",
        }
    )

    missing = [c for c in _REQUIRED if c not in df.columns]
    if missing:
        raise DataSourceFetchError(
            f"yfinanceのレスポンスに必要な列がありません（{label}, 不足={missing}）。"
            f"取得できた列: {list(df.columns)}"
        )

    df = df[_REQUIRED].copy()
    df["date"] = pd.to_datetime(df["date"])
    # 以降の処理（strftime等）を単純にするため、JSTの壁時計時刻にしてtzを落とす
    if getattr(df["date"].dtype, "tz", None) is not None:
        df["date"] = df["date"].dt.tz_convert("Asia/Tokyo").dt.tz_localize(None)

    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")

    df = df.dropna().sort_values("date").reset_index(drop=True)
    return df.tail(tf.bars).reset_index(drop=True)


def _fetch_sync(symbol: str, tf: TimeframeParams) -> pd.DataFrame:
    raw = yf.Ticker(symbol).history(
        period=tf.yf_period, interval=tf.yf_interval, auto_adjust=True
    )
    if raw.empty:
        raise DataSourceFetchError(
            f"銘柄 {symbol} の{tf.label}データが取得できませんでした。"
            "証券コードが正しいか、上場廃止・市場変更がないか確認してください。"
        )
    return normalize(raw, tf, symbol)


async def fetch_bars(code: str, tf: TimeframeParams) -> pd.DataFrame:
    """指定銘柄・指定時間軸の足データを取得する。

    戻り値の列: date, open, high, low, close, volume
    """
    symbol = to_symbol(code)
    try:
        # yfinanceは同期APIなので、イベントループを塞がないよう別スレッドで実行する
        return await asyncio.to_thread(_fetch_sync, symbol, tf)
    except DataSourceFetchError:
        raise
    except Exception as e:
        raise DataSourceFetchError(
            f"yfinanceからの取得に失敗しました（{symbol}）: {type(e).__name__}: {e}"
        ) from e
