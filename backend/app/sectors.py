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


def build(codes: list[str], progress: bool = False) -> dict[str, str]:
    """yfinanceから業種を取得してキャッシュを作る。失敗した銘柄はスキップする。"""
    import yfinance as yf

    from .yfinance_client import to_symbol

    mapping = dict(_load())
    for i, code in enumerate(codes, 1):
        if code in mapping:
            continue
        try:
            info = yf.Ticker(to_symbol(code)).get_info()
            mapping[code] = info.get("sector") or "unknown"
        except Exception:
            mapping[code] = "unknown"
        if progress and i % 20 == 0:
            print(f"  {i}/{len(codes)} …", flush=True)
    save(mapping)
    return mapping
