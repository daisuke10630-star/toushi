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
from datetime import date, datetime, timedelta, timezone

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


JST = timezone(timedelta(hours=9))
# 東証の大引け（15:30）から、この時間だけ待って「確定」とみなす
CLOSE_SETTLED_HOUR = 16


def _session_finished(bar_date: date) -> bool:
    """その営業日の取引が終わっているか（＝現在値を終値とみなしてよいか）。"""
    now = datetime.now(JST)
    if bar_date < now.date():
        return True
    return bar_date == now.date() and now.hour >= CLOSE_SETTLED_HOUR


def fill_last_close(
    adjusted: pd.DataFrame, unadjusted: pd.DataFrame | None, symbol: str
) -> pd.DataFrame:
    """最終行の終値が未確定なとき、確定値を取ってきて補う。

    Yahooは大引け後しばらく、日足の終値だけを空のまま返すことがある
    （始値・高値・安値・出来高は入っている）。auto_adjust=True では終値が
    欠けると全列がNaNになり、そのまま捨てると1営業日分データが古くなる。

    直近のバーには将来の分割・配当調整が効かないため、
    調整前の始値・高値・安値をそのまま使ってよい。
    """
    if adjusted is None or adjusted.empty:
        return adjusted
    if pd.notna(adjusted["Close"].iloc[-1]):
        return adjusted

    stamp = adjusted.index[-1]
    bar_date = stamp.date() if hasattr(stamp, "date") else None
    if bar_date is None or not _session_finished(bar_date):
        return adjusted

    try:
        price = yf.Ticker(symbol).fast_info.get("lastPrice")
    except Exception:
        return adjusted
    if not price or price <= 0:
        return adjusted

    out = adjusted.copy()
    out.loc[stamp, "Close"] = float(price)
    for col in ("Open", "High", "Low"):
        if pd.isna(out[col].iloc[-1]) and unadjusted is not None and not unadjusted.empty:
            try:
                v = unadjusted.loc[stamp, col]
                if pd.notna(v):
                    out.loc[stamp, col] = float(v)
            except (KeyError, TypeError):
                pass
        # それでも埋まらなければ終値で代用する（値幅0の足として扱う）
        if pd.isna(out[col].iloc[-1]):
            out.loc[stamp, col] = float(price)
    return out


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
    ticker = yf.Ticker(symbol)
    raw = ticker.history(period=tf.yf_period, interval=tf.yf_interval, auto_adjust=True)
    if not raw.empty and pd.isna(raw["Close"].iloc[-1]):
        try:
            unadj = ticker.history(period="5d", interval=tf.yf_interval, auto_adjust=False)
        except Exception:
            unadj = None
        raw = fill_last_close(raw, unadj, symbol)
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
