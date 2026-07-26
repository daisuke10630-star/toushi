"""
データ取得元の切り替え。

`.env` の DATA_SOURCE で取得元を選びます。

- yfinance（既定）: Yahoo!ファイナンス。APIキー不要、日足・分足に対応。非公式。
- jquants        : JPX公式のJ-Quants API V2。APIキー必須。日足のみ。無料プランは約12週間の遅延あり。

新しい取得元（証券会社のAPI等）を追加する場合は、
`async def fetch_bars(code: str, tf: TimeframeParams) -> pd.DataFrame`
（列: date, open, high, low, close, volume）を持つモジュールを作り、
下の _get_provider() に分岐を足すだけで済みます。
"""
import pandas as pd

from . import config
from .errors import DataSourceAuthError, DataSourceFetchError
from .timeframes import TimeframeParams

_PROVIDERS = ("yfinance", "jquants")


def _get_provider():
    name = current_source()
    if name == "yfinance":
        from . import yfinance_client

        return yfinance_client
    if name == "jquants":
        from . import jquants_client

        return jquants_client
    raise DataSourceFetchError(
        f"DATA_SOURCE の値 '{config.DATA_SOURCE}' は未対応です。"
        f"次のいずれかを .env に設定してください: {', '.join(_PROVIDERS)}"
    )


def current_source() -> str:
    return config.DATA_SOURCE.strip().lower()


async def fetch_bars(code: str, tf: TimeframeParams) -> pd.DataFrame:
    provider = _get_provider()
    return await provider.fetch_bars(code, tf)


# 市場指数（TOPIX ETF）は全銘柄で共通なので、時間軸ごとに使い回す
_market_cache: dict[str, pd.DataFrame] = {}


async def fetch_market(tf: TimeframeParams) -> pd.DataFrame | None:
    """相対強弱の基準となる市場指数を取得する。

    取得できなくても本体の判定は続行したいので、失敗時は None を返す。
    """
    from . import features

    if tf.key in _market_cache:
        return _market_cache[tf.key]
    try:
        from . import yfinance_client

        df = await yfinance_client.fetch_bars(features.MARKET_SYMBOL, tf)
    except Exception:
        return None
    _market_cache[tf.key] = df
    return df


# 業種インデックスは構成銘柄を一括取得して作るため重い。時間軸×業種で使い回す
_sector_cache: dict[tuple[str, str], pd.DataFrame] = {}


def _build_sector_index_sync(tf: TimeframeParams, sector: str) -> pd.DataFrame:
    import yfinance as yf

    from . import features, sectors, universe
    from .screener import _extract
    from .yfinance_client import normalize, to_symbol

    codes = [c for c in universe.codes() if sectors.get(c) == sector]
    frames: dict[str, pd.DataFrame] = {}
    symbols = [to_symbol(c) for c in codes]
    for i in range(0, len(symbols), config.SCREEN_BATCH_SIZE):
        chunk = symbols[i : i + config.SCREEN_BATCH_SIZE]
        try:
            batch = yf.download(
                chunk, period=tf.yf_period, interval=tf.yf_interval,
                group_by="ticker", auto_adjust=True, threads=True, progress=False,
            )
        except Exception:
            continue
        for sym in chunk:
            sub = _extract(batch, sym)
            if sub is None:
                continue
            try:
                frames[sym] = normalize(sub, tf, sym)
            except Exception:
                continue
    return features.build_sector_average(frames)


async def fetch_sector_index(tf: TimeframeParams, sector: str) -> pd.DataFrame | None:
    """同業種の等ウェイト指数を返す。取得できなければ None（判定は続行）。"""
    if sector == "unknown":
        return None
    key = (tf.key, sector)
    if key in _sector_cache:
        return _sector_cache[key]
    try:
        import asyncio

        df = await asyncio.to_thread(_build_sector_index_sync, tf, sector)
    except Exception:
        return None
    if df is None or df.empty:
        return None
    _sector_cache[key] = df
    return df


__all__ = [
    "fetch_bars",
    "current_source",
    "DataSourceAuthError",
    "DataSourceFetchError",
]
