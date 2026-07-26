"""
決算発表日の接近を検出する。

■ なぜスコアに入れないのか
yfinance から取れる決算日は直近の数回分しかなく、**10年分の過去検証ができません。**
検証できないものを加減点に入れると、効果が不明なまま★を歪めることになります。
そのため決算は「警告表示のみ」とし、★には一切影響させません。

■ なぜ警告する価値があるのか
決算発表はテクニカル分析の外側から来るイベントで、
チャートの形に関係なく大きく窓を開けることがあります。
損切りラインを飛び越えて約定する（＝想定より大きな損失になる）リスクがあるため、
「今エントリーすると決算をまたぐ」ことは知らせる意味があります。
"""
import asyncio
from datetime import date, datetime

import pandas as pd

from . import config

# 銘柄コード -> (取得日, 次回決算日 or None)
_cache: dict[str, tuple[date, date | None]] = {}


def _next_earnings_sync(code: str) -> date | None:
    import yfinance as yf

    from .yfinance_client import to_symbol

    ticker = yf.Ticker(to_symbol(code))
    today = date.today()

    # calendar が最も素直に「次回予定」を返す
    try:
        cal = ticker.calendar
        if isinstance(cal, dict):
            vals = cal.get("Earnings Date") or []
            if not isinstance(vals, (list, tuple)):
                vals = [vals]
            future = [v for v in (_to_date(v) for v in vals) if v and v >= today]
            if future:
                return min(future)
    except Exception:
        pass

    # calendar が空なら earnings_dates から未来分を拾う
    try:
        ed = ticker.get_earnings_dates(limit=8)
        if ed is not None and not ed.empty:
            future = [d for d in (_to_date(i) for i in ed.index) if d and d >= today]
            if future:
                return min(future)
    except Exception:
        pass

    return None


def _to_date(v) -> date | None:
    if v is None:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    if isinstance(v, datetime):
        return v.date()
    try:
        ts = pd.Timestamp(v)
        return None if pd.isna(ts) else ts.date()
    except Exception:
        return None


async def next_earnings_date(code: str) -> date | None:
    """次回の決算発表予定日。取得できなければ None。1日1回だけ問い合わせる。"""
    today = date.today()
    hit = _cache.get(code)
    if hit and hit[0] == today:
        return hit[1]
    try:
        result = await asyncio.to_thread(_next_earnings_sync, code)
    except Exception:
        result = None
    _cache[code] = (today, result)
    return result


async def warning_for(code: str) -> str | None:
    """決算が近い場合の警告文。近くなければ None。"""
    d = await next_earnings_date(code)
    if d is None:
        return None
    days = (d - date.today()).days
    if 0 <= days <= config.EARNINGS_WARN_DAYS:
        when = "本日" if days == 0 else f"{days}日後"
        return (
            f"決算発表が{when}（{d:%Y-%m-%d}）。窓を開けて損切りラインを飛び越える"
            "可能性があります。※このリスクは★の評価には含まれていません"
        )
    return None
