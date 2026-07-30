"""
銘柄の業種（セクター）情報。

yfinance の `.get_info()` から取得し、JSONファイルにキャッシュします。
1銘柄ずつAPIを叩くため母集団全件だと数分かかります。キャッシュがあれば即座に返します。

業種が取得できない銘柄は "unknown" 扱いとし、業種相対強弱の計算対象から外します
（推測で埋めると誤った業種比較になるため）。
"""
import json
import pathlib

CACHE_PATH = pathlib.Path(__file__).with_name("sector_cache.json")

_cache: dict[str, str] | None = None


def _load() -> dict[str, str]:
    global _cache
    if _cache is None:
        if CACHE_PATH.exists():
            try:
                _cache = json.loads(CACHE_PATH.read_text(encoding="utf-8"))
            except Exception:
                _cache = {}
        else:
            _cache = {}
    return _cache


def get(code: str) -> str:
    """証券コードの業種を返す。未知なら "unknown"。"""
    return _load().get(code, "unknown")


def all_sectors() -> dict[str, str]:
    return dict(_load())


def save(mapping: dict[str, str]) -> None:
    global _cache
    _cache = mapping
    CACHE_PATH.write_text(
        json.dumps(mapping, ensure_ascii=False, indent=1, sort_keys=True), encoding="utf-8"
    )


def build(
    codes: list[str], progress: bool = False, pause: float = 0.0, retries: int = 3
) -> tuple[dict[str, str], set[str]]:
    """yfinanceから業種を取得してキャッシュを作る。

    **取得失敗と「業種を持たない（＝ETF等）」を区別する。**
    ここを混同すると、レート制限で失敗しただけの普通の銘柄を
    ETFとみなして捨ててしまう（実際に一度その事故を起こした）。

    戻り値: (業種マップ, 取得できなかったコードの集合)
    失敗したコードはキャッシュに残さないので、次回また試せる。
    """
    import time

    import yfinance as yf

    from .yfinance_client import to_symbol

    mapping = dict(_load())
    failed: set[str] = set()

    for i, code in enumerate(codes, 1):
        if code in mapping:
            continue
        got = False
        for attempt in range(retries):
            try:
                info = yf.Ticker(to_symbol(code)).get_info()
                # Yahooは過負荷時、例外を出さずに中身の欠けた応答を返す。
                # quoteType があるかどうかで「まともな応答か」を判定する。
                # これを見ないと、throttleされただけの銘柄をETF扱いで捨ててしまう。
                qt = info.get("quoteType")
                sector = info.get("sector")
                if not qt:
                    time.sleep(2 ** attempt * max(pause, 1.0))
                    continue
                if qt == "EQUITY" and sector:
                    mapping[code] = sector
                elif qt in ("ETF", "MUTUALFUND", "INDEX"):
                    mapping[code] = "etf"
                else:
                    mapping[code] = "unknown"
                got = True
                break
            except Exception as e:
                if "rate" in str(e).lower() or "429" in str(e):
                    time.sleep(2 ** attempt * max(pause, 1.0))
                    continue
                break
        if not got:
            failed.add(code)
        if pause:
            time.sleep(pause)
        if progress and i % 25 == 0:
            print(f"  業種 {i}/{len(codes)}（未取得{len(failed)}件）", flush=True)

    save(mapping)
    return mapping, failed
