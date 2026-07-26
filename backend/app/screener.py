"""
母集団をスキャンして、★が一定以上の銘柄を抽出する。

■ 重要な前提
★は「強気条件の揃い具合」であって、上昇確率ではありません。
このスクリーナーが返すのは「いま指定した条件が揃っている銘柄」であって、
「上がる銘柄」ではありません。抽出結果はあくまで調査の出発点として扱ってください。

■ 実装メモ
- yfinance の一括ダウンロード（複数ティッカーをまとめて取得）でスキャンを高速化しています。
- 銘柄名は抽出できた銘柄についてのみ取得します（母集団全件だとリクエストが多すぎるため）。
- 抽出後、上位銘柄にはバックテスト（AI信頼度）も計算します。
"""
import asyncio

import pandas as pd
import yfinance as yf

from . import backtest, config, features, indicators, sectors, signals, universe
from .timeframes import TimeframeParams
from .yfinance_client import normalize, to_symbol

# 名前解決に失敗した銘柄が出ても全体を止めないためのフォールバック
_name_cache: dict[str, str] = {}


def universe_size() -> int:
    return len(universe.codes())


def _resolve_name(code: str) -> str:
    if code in _name_cache:
        return _name_cache[code]
    name = code
    try:
        info = yf.Ticker(to_symbol(code)).get_info()
        name = info.get("longName") or info.get("shortName") or code
    except Exception:
        # 名前が取れなくても抽出結果自体は返したいので、コードで代替する
        pass
    _name_cache[code] = name
    return name


def _extract(batch: pd.DataFrame, symbol: str) -> pd.DataFrame | None:
    """一括ダウンロード結果から1銘柄分を取り出す。"""
    if isinstance(batch.columns, pd.MultiIndex):
        if symbol not in batch.columns.get_level_values(0):
            return None
        sub = batch[symbol]
    else:
        sub = batch
    sub = sub.dropna(how="all")
    return None if sub.empty else sub


def _scan_sync(
    tf: TimeframeParams, min_stars: int, limit: int, require_positive_edge: bool
) -> list[dict]:
    codes = universe.codes()
    symbols = [to_symbol(c) for c in codes]
    sym_to_code = dict(zip(symbols, codes))

    # 相対強弱の基準になる市場指数を先に取っておく
    market = None
    try:
        market = normalize(
            yf.Ticker(features.MARKET_SYMBOL).history(
                period=tf.yf_period, interval=tf.yf_interval, auto_adjust=True
            ),
            tf,
            features.MARKET_SYMBOL,
        )
    except Exception:
        pass

    # 業種インデックスを作るため、まず全銘柄の生データを集める
    raw_frames: dict[str, pd.DataFrame] = {}
    hits: list[dict] = []
    batch_size = config.SCREEN_BATCH_SIZE

    for i in range(0, len(symbols), batch_size):
        chunk = symbols[i : i + batch_size]
        try:
            batch = yf.download(
                chunk,
                period=tf.yf_period,
                interval=tf.yf_interval,
                group_by="ticker",
                auto_adjust=True,
                threads=True,
                progress=False,
            )
        except Exception:
            # 1バッチ失敗しても全体は継続する
            continue

        if batch is None or batch.empty:
            continue

        for symbol in chunk:
            try:
                sub = _extract(batch, symbol)
                if sub is None:
                    continue
                df = normalize(sub, tf, symbol)
                # 指標が出そろわない短い履歴はスキップ
                if len(df) < max(tf.ma_periods.values()) + 5:
                    continue
                raw_frames[sym_to_code[symbol]] = df
            except Exception:
                # 個別銘柄の失敗はスキップして次へ
                continue

    # 業種ごとの等ウェイト指数を作る（同業種の平均に対する強弱を測るため）
    by_sector: dict[str, dict[str, pd.DataFrame]] = {}
    for code, df in raw_frames.items():
        sec = sectors.get(code)
        if sec != "unknown":
            by_sector.setdefault(sec, {})[code] = df
    sector_index = {
        sec: features.build_sector_average(members)
        for sec, members in by_sector.items()
        if len(members) >= 3  # 3銘柄未満の業種は平均が代表性を持たないので作らない
    }

    for code, df in raw_frames.items():
        try:
            peers = sector_index.get(sectors.get(code))
            df = indicators.compute_all(df, tf)
            df = features.compute_all(df, market=market, peers=peers)
            sig = signals.generate_signal(df, tf)
            if sig.stars < min_stars:
                continue

            latest = df.iloc[-1]
            prev_close = df.iloc[-2]["close"] if len(df) >= 2 else latest["close"]
            change_pct = (
                (latest["close"] - prev_close) / prev_close * 100 if prev_close else 0
            )

            hits.append(
                {
                    "code": code,
                    "sector": sectors.get(code),
                    "price": round(float(latest["close"]), 1),
                    "change_pct": round(float(change_pct), 2),
                    "updated_at": latest["date"].strftime(tf.date_format),
                    "stars": sig.stars,
                    "judgement": sig.judgement,
                    "perfect_order": sig.perfect_order,
                    "reasons": sig.reasons,
                    "warnings": sig.warnings,
                    "entry_price": sig.entry_price,
                    "stop_loss": sig.stop_loss,
                    "take_profit_1": sig.take_profit_1,
                    "_df": df,
                }
            )
        except Exception:
            continue

    # ★の高い順、同点なら上昇率の高い順
    hits.sort(key=lambda h: (-h["stars"], -h["change_pct"]))
    # バックテストは重いので、候補を絞ってから計算する
    hits = hits[: limit * config.SCREEN_CANDIDATE_MULTIPLIER]

    scored = []
    for h in hits:
        df = h.pop("_df")
        result = backtest.run(df, tf, h["stars"])
        h["confidence"] = {
            "available": result.available,
            "label": result.label,
            "win_rate": result.win_rate,
            "samples": result.samples,
            "avg_return_pct": result.avg_return_pct,
            "benchmark_expectancy": result.benchmark_expectancy,
            "edge": result.edge,
            "edge_label": result.edge_label,
        }
        # ★だけでは銘柄を絞れないため、「単純保有を上回っているか」で足切りする
        if require_positive_edge and not (result.edge is not None and result.edge > 0):
            continue
        scored.append(h)

    # 単純保有をどれだけ上回っているかの順に並べる（★順ではない）
    if require_positive_edge:
        scored.sort(key=lambda h: -(h["confidence"]["edge"] or 0))
    scored = scored[:limit]

    for h in scored:
        h["name"] = _resolve_name(h["code"])
    return scored


async def scan(
    tf: TimeframeParams,
    min_stars: int,
    limit: int,
    require_positive_edge: bool | None = None,
) -> list[dict]:
    if require_positive_edge is None:
        require_positive_edge = config.SCREEN_REQUIRE_POSITIVE_EDGE
    return await asyncio.to_thread(
        _scan_sync, tf, min_stars, limit, require_positive_edge
    )
