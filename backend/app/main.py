import asyncio
from dataclasses import asdict

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

from . import (
    backtest,
    config,
    datasource,
    earnings,
    features,
    indicators,
    positions,
    screener,
    sectors,
    signals,
    timeframes,
)
from .errors import DataSourceAuthError

app = FastAPI(title="日本株テクニカル分析ダッシュボード")

app.add_middleware(
    CORSMiddleware,
    allow_origins=config.ALLOWED_ORIGINS,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 同一銘柄・同一時間軸への短時間の重複リクエストを避けるための簡易キャッシュ
_cache: dict[tuple[str, str], tuple[float, dict]] = {}
# 分足は更新が速いのでキャッシュを短く、日足は長めにする
_CACHE_TTL_SEC = {"1m": 30, "5m": 60, "1d": 300}


def _resolve_timeframe(key: str | None):
    try:
        return timeframes.get(key)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))


@app.get("/api/watchlist")
async def get_watchlist():
    return config.WATCHLIST


@app.get("/api/timeframes")
async def get_timeframes():
    """UIの時間軸切り替え用。各時間軸のパラメータ根拠も返す。"""
    return [
        {
            "key": tf.key,
            "label": tf.label,
            "ma_labels": tf.ma_labels,
            "ma_periods": tf.ma_periods,
            "rsi": {
                "short": tf.rsi_short,
                "long": tf.rsi_long,
                "overheat": tf.rsi_overheat,
                "oversold": tf.rsi_oversold,
            },
            "bb_period": tf.bb_period,
            "forward_label": tf.forward_label,
            "notes": tf.notes,
        }
        for tf in timeframes.ALL.values()
    ]


async def _build_stock_payload(code: str, name: str, tf, with_backtest: bool) -> dict:
    df = await datasource.fetch_bars(code, tf)
    df = indicators.compute_all(df, tf)
    # 価格以外の情報を付ける。基準データが取れなくても判定は続行する
    market = await datasource.fetch_market(tf)
    sector = sectors.get(code)
    peers = await datasource.fetch_sector_index(tf, sector)
    df = features.compute_all(df, market=market, peers=peers)
    result = signals.generate_signal(df, tf)

    # 決算はバックテストできないため★には反映せず、警告としてのみ添える
    earnings_warning = await earnings.warning_for(code)
    if earnings_warning:
        result.warnings.append(earnings_warning)

    latest = df.iloc[-1]
    prev_close = df.iloc[-2]["close"] if len(df) >= 2 else latest["close"]
    change = latest["close"] - prev_close
    change_pct = (change / prev_close * 100) if prev_close else 0

    confidence = None
    if with_backtest:
        # バックテストはCPUを使うので、イベントループを塞がないよう別スレッドで回す
        confidence = asdict(await asyncio.to_thread(backtest.run, df, tf, result.stars))

    # チャート描画用に直近120本のみ返す（軽量化）
    chart_df = df.tail(120).copy()
    chart_df["date"] = chart_df["date"].dt.strftime(tf.date_format)
    chart_records = chart_df.replace({float("nan"): None}).to_dict(orient="records")

    return {
        "code": code,
        "name": name,
        "sector": sector,
        "timeframe": tf.key,
        "timeframe_label": tf.label,
        "updated_at": latest["date"].strftime(tf.date_format),
        "price": round(float(latest["close"]), 1),
        "change": round(float(change), 1),
        "change_pct": round(float(change_pct), 2),
        "signal": {
            "judgement": result.judgement,
            "stars": result.stars,
            "stars_note": result.stars_note,
            "perfect_order": result.perfect_order,
            "reasons": result.reasons,
            "warnings": result.warnings,
            "entry_price": result.entry_price,
            "entry_note": result.entry_note,
            "stop_loss": result.stop_loss,
            "stop_loss_note": result.stop_loss_note,
            "take_profit_1": result.take_profit_1,
            "take_profit_1_note": result.take_profit_1_note,
            "take_profit_2": result.take_profit_2,
            "take_profit_2_note": result.take_profit_2_note,
            "patterns_detected": result.patterns_detected,
        },
        "confidence": confidence,
        "chart": chart_records,
    }


@app.get("/api/stock/{code}")
async def get_stock(
    code: str,
    timeframe: str = Query(default=timeframes.DEFAULT_KEY),
    with_backtest: bool = Query(default=True),
):
    tf = _resolve_timeframe(timeframe)
    name = next((w["name"] for w in config.WATCHLIST if w["code"] == code), code)
    now = asyncio.get_running_loop().time()
    cache_key = (code, tf.key)

    cached = _cache.get(cache_key)
    ttl = _CACHE_TTL_SEC.get(tf.key, 60)
    if cached and now - cached[0] < ttl and cached[1].get("confidence") is not None:
        return cached[1]

    try:
        payload = await _build_stock_payload(code, name, tf, with_backtest)
    except DataSourceAuthError as e:
        raise HTTPException(status_code=401, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"データ取得エラー: {e}")

    _cache[cache_key] = (now, payload)
    return payload


@app.get("/api/watchlist/full")
async def get_watchlist_full(
    timeframe: str = Query(default=timeframes.DEFAULT_KEY),
    with_backtest: bool = Query(default=True),
):
    """ウォッチリスト全銘柄を一括取得（ダッシュボード初期表示用）

    銘柄数が増えると直列取得では待ち時間が伸びるため、並列で取得する。
    """
    tf = _resolve_timeframe(timeframe)

    async def one(w: dict):
        try:
            return await get_stock(w["code"], tf.key, with_backtest)
        except HTTPException as e:
            return {"code": w["code"], "name": w["name"], "error": e.detail}
        except Exception as e:
            return {"code": w["code"], "name": w["name"], "error": str(e)}

    return await asyncio.gather(*(one(w) for w in config.WATCHLIST))


@app.get("/api/screen")
async def screen(
    timeframe: str = Query(default=timeframes.DEFAULT_KEY),
    min_stars: int = Query(default=config.SCREEN_MIN_STARS, ge=1, le=5),
    limit: int = Query(default=30, ge=1, le=200),
    require_positive_edge: bool = Query(default=config.SCREEN_REQUIRE_POSITIVE_EDGE),
):
    """母集団をスキャンして★min_stars以上の銘柄を抽出する。

    ★は「強気条件の揃い具合」であって上昇確率ではありません。
    10年の検証で★自体には優位性がないと判明したため、既定では
    「その銘柄で単純保有を上回っている」ものだけに絞り込みます。
    """
    tf = _resolve_timeframe(timeframe)
    try:
        hits = await screener.scan(
            tf,
            min_stars=min_stars,
            limit=limit,
            require_positive_edge=require_positive_edge,
        )
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"スクリーニングに失敗しました: {e}")
    return {
        "timeframe": tf.key,
        "min_stars": min_stars,
        "require_positive_edge": require_positive_edge,
        "universe_size": screener.universe_size(),
        "hit_count": len(hits),
        "note": signals.STARS_NOTE,
        "results": hits,
    }


@app.get("/api/positions")
async def get_positions(timeframe: str = Query(default=timeframes.DEFAULT_KEY)):
    """保有ポジションの含み損益と、ルール上の損切り・利確ラインを返す。

    ここで返すラインの根拠は、検証で買い持ちに負けているルールです。
    「ここで売れば利益が最大」という意味ではありません。
    """
    tf = _resolve_timeframe(timeframe)
    held = positions.load()

    async def one(p: dict):
        try:
            df = await datasource.fetch_bars(p["code"], tf)
            df = indicators.compute_all(df, tf)
            return positions.evaluate(p, df, tf)
        except Exception as e:
            return {
                "code": p["code"],
                "name": p.get("name") or p["code"],
                "error": f"データ取得エラー: {e}",
            }

    rows = list(await asyncio.gather(*(one(p) for p in held)))
    return {
        "positions": rows,
        "summary": positions.summarize([r for r in rows if not r.get("error")]),
        "disclaimer": (
            "損切り・利確ラインの根拠は、検証で買い持ちに負けているルールです"
            "（同時保有3銘柄で年+19.1%、同じ銘柄の買い持ちは年+25.1%）。"
            "利益が最大になる水準ではありません。"
        ),
    }


class LotIn(BaseModel):
    avg_cost: float = Field(gt=0, description="取得単価（円）")
    shares: int = Field(gt=0, description="株数")


class PositionIn(BaseModel):
    code: str = Field(min_length=1, max_length=8)
    name: str | None = None
    lots: list[LotIn] = Field(default_factory=list)
    acquired_on: str | None = None


def _check_write_token(token: str | None) -> None:
    """公開先で第三者に書き換えられないようにするための簡易チェック。

    APP_WRITE_TOKEN が未設定なら（ローカル利用を想定して）チェックしない。
    """
    if not config.WRITE_TOKEN:
        return
    if token != config.WRITE_TOKEN:
        raise HTTPException(status_code=401, detail="書き込みトークンが一致しません")


@app.put("/api/positions/{code}")
async def upsert_position(
    code: str, body: PositionIn, x_write_token: str | None = Header(default=None)
):
    """保有ポジションを追加・更新する（株数0または未指定で削除）。"""
    _check_write_token(x_write_token)
    payload = body.model_dump()
    payload["code"] = code
    try:
        positions.upsert(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except OSError as e:
        raise HTTPException(status_code=500, detail=f"保存に失敗しました: {e}")
    # Query のデフォルト値が入るのを避けるため、明示的にキーを渡す
    return await get_positions(timeframes.DEFAULT_KEY)


@app.get("/api/compare")
async def compare(
    timeframe: str = Query(default=timeframes.DEFAULT_KEY),
    limit: int = Query(default=20, ge=1, le=50),
):
    """ウォッチリスト銘柄のエントリー・損切り・利確を横並びで比較する。

    価格水準の違う銘柄を比べられるよう、現在値からの乖離率（%）も返す。
    """
    tf = _resolve_timeframe(timeframe)
    rows = await get_watchlist_full(tf.key, with_backtest=False)

    out = []
    for s in rows:
        if s.get("error"):
            continue
        sig = s.get("signal") or {}
        entry, stop = sig.get("entry_price"), sig.get("stop_loss")
        tp1, tp2 = sig.get("take_profit_1"), sig.get("take_profit_2")
        price = s.get("price")
        if not all(v is not None for v in (entry, stop, tp1, price)) or price <= 0:
            continue
        risk = entry - stop
        out.append({
            "code": s["code"],
            "name": s["name"],
            "sector": s.get("sector"),
            "price": price,
            "stars": sig.get("stars"),
            "judgement": sig.get("judgement"),
            "entry_price": entry,
            "stop_loss": stop,
            "take_profit_1": tp1,
            "take_profit_2": tp2,
            # 株価水準が違っても比べられるよう、すべて現在値からの乖離率で持つ
            "entry_gap_pct": round((entry / price - 1) * 100, 2),
            "stop_gap_pct": round((stop / price - 1) * 100, 2),
            "tp1_gap_pct": round((tp1 / price - 1) * 100, 2),
            "tp2_gap_pct": round((tp2 / price - 1) * 100, 2) if tp2 else None,
            "risk_reward": round((tp1 - entry) / risk, 2) if risk > 0 else None,
            "reachable": entry <= price,  # 現在値で既にエントリー条件を満たすか
        })

    # エントリーまでの距離が近い順（＝すぐ動く可能性がある順）
    out.sort(key=lambda r: abs(r["entry_gap_pct"]))
    return {
        "timeframe": tf.key,
        "count": len(out[:limit]),
        "note": (
            "乖離率は現在値を0%とした相対位置です。★や順位は推奨を意味しません。"
            "各銘柄の優劣は詳細画面の「対照実験」で確認してください。"
        ),
        "results": out[:limit],
    }


@app.get("/api/health")
async def health():
    return {
        "status": "ok",
        "data_source": datasource.current_source(),
        "timeframes": list(timeframes.ALL),
    }
