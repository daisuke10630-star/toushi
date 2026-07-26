"""
J-Quants API クライアント（V2対応）。

認証フロー：
- V2 は APIキー方式です。マイページで発行したAPIキーを `x-api-key` ヘッダーに載せるだけで、
  V1 にあった「リフレッシュトークン → idToken」の2段階認証は廃止されました。

エンドポイント：
- 日足株価は /v2/equities/bars/daily（V1の /v1/prices/daily_quotes に相当）
- レスポンスは {"data": [...], "pagination_key": "..."} 形式で、pagination_key があれば続きを取得する

注意：
- Freeプランの日足は「12週間前 〜 2年12週間前」の範囲しか取得できません（約3か月の遅延）。
  当日・前日の株価は Light プラン以上が必要です。
- 分足・ティックは別売りアドオンです。
- 将来別のリアルタイムAPIに差し替える場合は、このファイルの fetch_bars と
  同じ戻り値の形（pandas.DataFrame、列: date, open, high, low, close, volume）を
  維持すれば、indicators.py 以降のコードは変更不要です。
"""
from datetime import datetime, timedelta

import httpx
import pandas as pd

from . import config
from .errors import DataSourceAuthError, DataSourceFetchError
from .timeframes import TimeframeParams

_BASE_URL = "https://api.jquants.com/v2"

# Freeプランは約12週間の配信遅延があるため、その分も余分にさかのぼって取得する
_FREE_PLAN_DELAY_DAYS = 120


class JQuantsAuthError(DataSourceAuthError):
    pass


def _headers() -> dict[str, str]:
    if not config.JQUANTS_API_KEY:
        if config.JQUANTS_REFRESH_TOKEN:
            raise JQuantsAuthError(
                "J-Quants APIはV2に移行し、リフレッシュトークンは廃止されました。"
                "マイページでAPIキーを発行し、backend/.env に JQUANTS_API_KEY として設定してください。"
            )
        raise JQuantsAuthError(
            "JQUANTS_API_KEYが設定されていません。backend/.envを確認してください。"
        )
    return {"x-api-key": config.JQUANTS_API_KEY}


async def _get_daily_bars(client: httpx.AsyncClient, code: str, date_from: str) -> list[dict]:
    """1銘柄分の日足を pagination_key を辿って全件取得する。"""
    params = {"code": code, "from": date_from}
    rows: list[dict] = []

    while True:
        resp = await client.get(
            f"{_BASE_URL}/equities/bars/daily", params=params, headers=_headers()
        )
        if resp.status_code in (401, 403):
            raise JQuantsAuthError(
                f"J-Quants認証に失敗しました（status={resp.status_code}）。"
                f"APIキーが有効か確認してください: {resp.text}"
            )
        if resp.status_code != 200:
            raise RuntimeError(
                f"日足データの取得に失敗しました（code={code}, status={resp.status_code}）: {resp.text}"
            )

        payload = resp.json()
        rows.extend(payload.get("data", []))

        pagination_key = payload.get("pagination_key")
        if not pagination_key:
            return rows
        params["pagination_key"] = pagination_key


async def fetch_bars(code: str, tf: TimeframeParams) -> pd.DataFrame:
    """指定銘柄の日足データを取得し、DataFrameで返す。

    戻り値の列: date, open, high, low, close, volume

    J-Quantsの分足・ティックは別売りアドオンのため、ここでは日足のみ対応します。
    """
    if tf.is_intraday:
        raise DataSourceFetchError(
            f"DATA_SOURCE=jquants では{tf.label}に対応していません"
            "（J-Quantsの分足は別売りアドオンです）。"
            "分足を使う場合は .env の DATA_SOURCE を yfinance にしてください。"
        )

    days = tf.bars
    # 土日祝日と、Freeプランの配信遅延を考慮して余裕を持ってさかのぼる
    lookback_days = int(days * 1.6) + _FREE_PLAN_DELAY_DAYS
    date_from = (datetime.now() - timedelta(days=lookback_days)).strftime("%Y-%m-%d")

    async with httpx.AsyncClient(timeout=30.0) as client:
        rows = await _get_daily_bars(client, code, date_from)
        # V2は5桁コード（4桁＋末尾0）が基本。4桁で空だった場合に備えて再試行する
        if not rows and len(code) == 4:
            rows = await _get_daily_bars(client, f"{code}0", date_from)

    if not rows:
        raise RuntimeError(
            f"銘柄コード {code} のデータが見つかりませんでした。"
            "（Freeプランは12週間前より新しいデータを取得できません）"
        )

    df = pd.DataFrame(rows)

    # V2のフィールド名: Date / AdjO,AdjH,AdjL,AdjC,AdjVo（調整後）/ O,H,L,C,Vo（調整前）
    # 分割・併合の影響を除いた調整後を優先し、無ければ調整前で代替する
    adjusted = {
        "Date": "date",
        "AdjO": "open",
        "AdjH": "high",
        "AdjL": "low",
        "AdjC": "close",
        "AdjVo": "volume",
    }
    raw = {"Date": "date", "O": "open", "H": "high", "L": "low", "C": "close", "Vo": "volume"}
    rename_map = adjusted if all(c in df.columns for c in adjusted) else raw
    df = df.rename(columns=rename_map)

    required = ["date", "open", "high", "low", "close", "volume"]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise RuntimeError(
            f"J-Quantsのレスポンスに必要な列がありません（code={code}, 不足={missing}）。"
            f"取得できた列: {list(df.columns)}"
        )

    df = df[required].dropna()
    df["date"] = pd.to_datetime(df["date"])
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.dropna().sort_values("date").reset_index(drop=True)
    df = df.tail(days).reset_index(drop=True)
    return df
