"""
価格だけを更新する軽量スクリプト（相場中に高頻度で回す用）。

日次レポート（build_site.py）は244銘柄の5年分を取得して分析するため3分ほどかかる。
相場中に必要なのは「いまいくらか」だけなので、こちらは直近の価格のみを取得して
docs/prices.json を書き出す。ページ側はこれを読んで表示を差し替える。

■ 鮮度の限界（正直に）
- yfinance の株価自体が15〜20分遅れ
- GitHub Actions のスケジュール実行は混雑時に5〜20分遅延する
したがって実際の鮮度は「20〜40分前」程度。リアルタイムではない。
"""
import json
import pathlib
import sys
from datetime import datetime, timedelta, timezone

import yfinance as yf

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app import config, universe  # noqa: E402
from app.yfinance_client import to_symbol  # noqa: E402

JST = timezone(timedelta(hours=9))
OUT = ROOT / "docs" / "prices.json"
BATCH = 60


def fetch_prices(codes: list[str]) -> dict[str, dict]:
    """最新の株価と前日終値をまとめて取得する。"""
    out: dict[str, dict] = {}
    symbols = [to_symbol(c) for c in codes]

    for i in range(0, len(symbols), BATCH):
        chunk = symbols[i : i + BATCH]
        try:
            # 直近5日の日足。ザラ場中は当日の行が「現在値」として入る
            df = yf.download(
                chunk, period="5d", interval="1d", group_by="ticker",
                auto_adjust=False, threads=True, progress=False,
            )
        except Exception:
            continue
        if df is None or df.empty:
            continue

        for sym in chunk:
            code = sym.replace(".T", "")
            try:
                sub = df[sym] if hasattr(df.columns, "levels") else df
                closes = sub["Close"].dropna()
                if closes.empty:
                    continue
                price = float(closes.iloc[-1])
                prev = float(closes.iloc[-2]) if len(closes) >= 2 else price
                out[code] = {
                    "price": round(price, 1),
                    "prev": round(prev, 1),
                    "change_pct": round((price / prev - 1) * 100, 2) if prev else 0.0,
                    "date": str(closes.index[-1].date()),
                }
            except Exception:
                continue
    return out


def main() -> int:
    codes = universe.codes()
    prices = fetch_prices(codes)
    if not prices:
        print("価格を取得できませんでした。既存の prices.json は残します", file=sys.stderr)
        return 1

    now = datetime.now(JST)
    OUT.parent.mkdir(exist_ok=True)
    OUT.write_text(
        json.dumps(
            {
                "updated_at": now.strftime("%Y-%m-%d %H:%M"),
                "count": len(prices),
                "note": (
                    "yfinanceの株価は15〜20分遅れ、更新自体も数分〜数十分ずれます。"
                    "リアルタイムではありません"
                ),
                "prices": prices,
            },
            ensure_ascii=False, separators=(",", ":"),
        ),
        encoding="utf-8",
    )
    print(f"{len(prices)}銘柄の価格を更新（{now:%Y-%m-%d %H:%M} JST）")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
