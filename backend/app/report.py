"""
日次レポートの生成。サーバーを起動せずに単体で実行できる。

GitHub Actions のような「PCが起動していない環境」でも動かせるよう、
HTTP API を経由せず app モジュールを直接呼び出します。

■ 「最もシグナルが強い銘柄」について
**「推奨銘柄」という言葉は使いません。** 検証の結果、★にも本ルールにも
「上がる銘柄を当てる力」は確認できていません。ここで出すのは
「今日いちばん条件が揃っている銘柄」という機械的なランキングです。
"""
import asyncio
import dataclasses
from dataclasses import dataclass, field
from datetime import datetime

import pandas as pd
import yfinance as yf

from . import (
    backtest,
    config,
    features,
    indicators,
    projection,
    sectors,
    signals,
    timeframes,
    universe,
)
from .screener import _extract
from .yfinance_client import normalize, to_symbol


@dataclass
class Candidate:
    code: str
    name: str
    sector: str
    price: float
    change_pct: float
    stars: int
    judgement: str
    entry_price: float | None
    stop_loss: float | None
    take_profit_1: float | None
    entry_gap_pct: float | None
    reasons: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    edge: float | None = None
    confidence_label: str = ""
    projection: dict = field(default_factory=dict)
    overbought: bool = False
    overbought_reason: str = ""


def _download(codes: list[str], tf) -> dict[str, pd.DataFrame]:
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
                df = normalize(sub, tf, sym)
                if len(df) >= max(tf.ma_periods.values()) + 20:
                    frames[sym.replace(".T", "")] = df
            except Exception:
                continue
    return frames


def build(tf=None, with_backtest: bool = True) -> dict:
    """母集団全体を分析し、レポート用のデータをまとめて返す。"""
    base = tf or timeframes.get(timeframes.DEFAULT_KEY)
    # 過去分布のサンプル数を確保するため、レポートでは長めの履歴を取る。
    # 判定自体は直近の値しか見ないので、結果は変わらず統計だけが厚くなる。
    tf = dataclasses.replace(base, yf_period="5y", bars=1300)
    codes = universe.codes()
    raw = _download(codes, tf)

    market = None
    try:
        market = normalize(
            yf.Ticker(features.MARKET_SYMBOL).history(
                period=tf.yf_period, interval=tf.yf_interval, auto_adjust=True
            ),
            tf, features.MARKET_SYMBOL,
        )
    except Exception:
        pass

    by_sector: dict[str, dict[str, pd.DataFrame]] = {}
    for code, df in raw.items():
        sec = sectors.get(code)
        if sec != "unknown":
            by_sector.setdefault(sec, {})[code] = df
    sector_index = {
        s: features.build_sector_average(m) for s, m in by_sector.items() if len(m) >= 3
    }

    names = {w["code"]: w["name"] for w in config.WATCHLIST}
    buys: list[Candidate] = []
    sells: list[Candidate] = []

    for code, df in raw.items():
        try:
            d = indicators.compute_all(df, tf)
            d = features.compute_all(d, market=market, peers=sector_index.get(sectors.get(code)))
            sig = signals.generate_signal(d, tf)
            latest = d.iloc[-1]
            price = float(latest["close"])
            prev = float(d.iloc[-2]["close"]) if len(d) >= 2 else price
            gap = ((sig.entry_price / price - 1) * 100) if sig.entry_price and price else None

            cand = Candidate(
                code=code,
                name=names.get(code, code),
                sector=sectors.get(code),
                price=round(price, 1),
                change_pct=round((price / prev - 1) * 100, 2) if prev else 0.0,
                stars=sig.stars,
                judgement=sig.judgement,
                entry_price=sig.entry_price,
                stop_loss=sig.stop_loss,
                take_profit_1=sig.take_profit_1,
                entry_gap_pct=round(gap, 2) if gap is not None else None,
                reasons=sig.reasons,
                warnings=sig.warnings,
            )

            hot, hot_reason = signals.is_overbought(latest, tf)
            cand.overbought = hot
            cand.overbought_reason = hot_reason
            # 買われすぎは候補から外す（検証で成績が改善した）
            if sig.stars >= 4 and not (config.EXCLUDE_OVERBOUGHT and hot):
                buys.append((cand, d))
            if sig.stars <= 2 or "売り" in sig.judgement:
                sells.append(cand)
        except Exception:
            continue

    # 買い候補は「条件が揃っていて、かつエントリーまでの距離が近い」順
    buys.sort(key=lambda x: (-x[0].stars, abs(x[0].entry_gap_pct or 999)))
    top_buys = buys[: config.REPORT_TOP_N]

    # 上位だけバックテストとシグナル条件付きの分布を計算（どちらも重いので）
    for cand, d in top_buys:
        # ウォッチリスト外の銘柄は名前が未解決なので、上位だけ問い合わせる
        if cand.name == cand.code:
            try:
                from .screener import _resolve_name

                cand.name = _resolve_name(cand.code)
            except Exception:
                pass
        if with_backtest:
            try:
                r = backtest.run(d, tf, cand.stars)
                cand.edge = r.edge
                cand.confidence_label = r.label
            except Exception:
                pass
        try:
            cand.projection = projection.conditional(d, tf, cand.stars).as_dict()
        except Exception:
            pass

    sells.sort(key=lambda c: (c.stars, c.change_pct))

    # スマホ側（静的ページ）で損益・損切り・トレーリングを計算するためのデータ。
    # 取得単価は端末に置いたままにしたいので、計算に必要な材料だけを配る。
    calc_days = tf.forward_bars + 30
    stocks = {}
    for code, df in raw.items():
        try:
            d = indicators.compute_all(df, tf)
            latest = d.iloc[-1]
            atr = latest.get("ATR")
            if pd.isna(atr) or float(atr) <= 0:
                continue
            tail = d.tail(calc_days)
            # 「どこまで上がる／下がるか」の過去分布。無条件版は全銘柄に高速で付けられる
            proj = projection.unconditional(d, tf)
            # 自分で選んだ銘柄を判定できるよう、条件の充足状況を全銘柄に持たせる
            d2 = features.compute_all(d, market=market, peers=sector_index.get(sectors.get(code)))
            sg = signals.generate_signal(d2, tf)
            lt = d2.iloc[-1]
            hot, hot_reason = signals.is_overbought(lt, tf)
            checks = [
                {"label": "買われすぎでない", "ok": not hot,
                 "detail": hot_reason or "RSI・ボリンジャーバンドとも過熱していません"},
                {"label": "パーフェクトオーダー", "ok": sg.perfect_order == "上昇形成",
                 "detail": f"移動平均は「{sg.perfect_order}」"},
                {"label": "同業種より強い", "ok": bool(pd.notna(lt.get("RS_SECTOR"))
                                                      and lt.get("RS_SECTOR") > 0),
                 "detail": (f"同業種比 {float(lt['RS_SECTOR']):+.1f}%"
                            if pd.notna(lt.get("RS_SECTOR")) else "業種データなし")},
                {"label": "ブレイク間近", "ok": bool(sg.entry_price
                                                  and sg.entry_price / float(lt["close"]) - 1 <= 0.03),
                 "detail": (f"エントリー目安 {sg.entry_price:,.1f}円"
                            if sg.entry_price else "算出不可")},
            ]
            stocks[code] = {
                "stars": sg.stars,
                "judgement": sg.judgement,
                "entry": sg.entry_price,
                "stop": sg.stop_loss,
                "tp1": sg.take_profit_1,
                "tp2": sg.take_profit_2,
                "checks": checks,
                "score": sum(1 for c in checks if c["ok"]),
                "name": names.get(code, code),
                "price": round(float(latest["close"]), 1),
                "atr": round(float(atr), 2),
                "high20": round(float(d["high"].tail(config.BREAKOUT_LOOKBACK).max()), 1),
                "dates": [x.strftime("%Y-%m-%d") for x in tail["date"]],
                "highs": [round(float(v), 1) for v in tail["high"]],
                "proj": {
                    "n": proj.samples,
                    "days": proj.horizon_days,
                    "up50": proj.up_p50, "up75": proj.up_p75, "up90": proj.up_p90,
                    "dn50": proj.down_p50, "dn90": proj.down_p90,
                    "end50": proj.end_p50,
                    "day50": proj.day_p50, "day80": proj.day_p80,
                },
            }
        except Exception:
            continue

    return {
        "stocks": stocks,
        "generated_at": datetime.now().strftime("%Y-%m-%d %H:%M"),
        "data_date": max(
            (df["date"].iloc[-1] for df in raw.values()), default=None
        ),
        "universe_size": len(codes),
        "analyzed": len(raw),
        "timeframe": tf,
        "top_buys": [c for c, _ in top_buys],
        "top_sells": sells[:10],
    }


async def build_async(tf=None, with_backtest: bool = True) -> dict:
    return await asyncio.to_thread(build, tf, with_backtest)
