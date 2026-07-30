"""
母集団（対象銘柄リスト）を広げる。一度だけ実行してJSONに保存する。

■ やっていること
東証の4桁コードを総当たりで問い合わせ、実際にデータが返るものだけを残す。
そのうえで「売買代金が一定以上」の銘柄に絞る。

■ なぜ流動性で絞るのか
バックテストは「提示した価格で必ず約定する」前提で計算している。
出来高の薄い銘柄ではこの前提が崩れ、実際には約定しない値段で
「儲かったこと」にしてしまう。**銘柄数を増やすほどこの罠が増える**ため、
数を追うのではなく、成立し得る銘柄だけを残す。
"""
import argparse
import json
import pathlib
import sys
import time

import pandas as pd
import yfinance as yf

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

OUT = ROOT / "backend" / "app" / "universe_codes.json"
BATCH = 50

# 東証の銘柄コードが実際に割り当てられている範囲
CODE_RANGES = [(1300, 1999), (2000, 2999), (3000, 3999), (4000, 4999),
               (5000, 5999), (6000, 6999), (7000, 7999), (8000, 8999), (9000, 9999)]

# 1日あたりの平均売買代金の下限（円）。これ未満は約定を前提にできない。
# 1000銘柄規模にするため2000万円まで下げる。それでも1日2000万円は動く銘柄。
MIN_TURNOVER = 20_000_000
# 最低株価（円）。極端な低位株は値動きの刻みが粗く、検証が成立しにくい
MIN_PRICE = 100


def candidates() -> list[str]:
    out = []
    for lo, hi in CODE_RANGES:
        out.extend(str(c) for c in range(lo, hi + 1))
    return out


PROGRESS = ROOT / "backend" / "app" / "universe_probe_progress.json"


def _download_with_retry(chunk: list[str], pause: float) -> pd.DataFrame | None:
    """レート制限に当たったら待って再試行する。

    Yahooは短時間に大量のリクエストを送ると 429 (Too Many Requests) を返す。
    ここを握りつぶすと「データが無い銘柄」と誤判定して母集団が壊れるため、
    必ず待ってやり直す。
    """
    delay = pause
    for attempt in range(5):
        try:
            df = yf.download(chunk, period="3mo", interval="1d", group_by="ticker",
                             auto_adjust=True, threads=False, progress=False)
            if df is not None and not df.empty:
                return df
        except Exception as e:
            if "rate" not in str(e).lower() and "429" not in str(e):
                return None
        time.sleep(delay)
        delay *= 2
    return None


def probe(
    codes: list[str], limit: int | None, min_turnover: int, pause: float
) -> dict[str, dict]:
    """データが返り、かつ流動性のある銘柄を集める。

    途中で止まっても続きから再開できるよう、逐次ファイルに保存する。
    """
    found: dict[str, dict] = {}
    done_codes: set[str] = set()
    if PROGRESS.exists():
        try:
            saved = json.loads(PROGRESS.read_text(encoding="utf-8"))
            found = saved.get("found", {})
            done_codes = set(saved.get("done", []))
            print(f"  前回の続きから再開（調査済み{len(done_codes)}件 / 発見{len(found)}件）")
        except Exception:
            pass

    codes = [c for c in codes if c not in done_codes]
    syms = [f"{c}.T" for c in codes]
    total = len(syms)

    for i in range(0, total, BATCH):
        chunk = syms[i : i + BATCH]
        df = _download_with_retry(chunk, pause)
        done_codes.update(s.replace(".T", "") for s in chunk)
        if df is None:
            print(f"  {min(i + BATCH, total)}/{total} 取得失敗（レート制限の可能性）", flush=True)
            time.sleep(pause * 4)
            continue

        for sym in chunk:
            try:
                sub = df[sym] if hasattr(df.columns, "levels") else df
                sub = sub.dropna(how="all")
                if len(sub) < 40:
                    continue
                price = float(sub["Close"].iloc[-1])
                if pd.isna(price) or price < MIN_PRICE:
                    continue
                turnover = float((sub["Close"] * sub["Volume"]).tail(60).mean())
                if pd.isna(turnover) or turnover < min_turnover:
                    continue
                found[sym.replace(".T", "")] = {
                    "price": round(price, 1),
                    "turnover": int(turnover),
                }
            except Exception:
                continue

        done = min(i + BATCH, total)
        print(f"  {done}/{total} 調査済み → {len(found)}銘柄が条件を満たす", flush=True)
        PROGRESS.write_text(
            json.dumps({"found": found, "done": sorted(done_codes)}, ensure_ascii=False),
            encoding="utf-8",
        )
        if limit and len(found) >= limit:
            break
        time.sleep(pause)
    return found


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=None, help="この件数に達したら打ち切る")
    ap.add_argument("--min-turnover", type=int, default=MIN_TURNOVER)
    ap.add_argument("--pause", type=float, default=1.5,
                    help="バッチ間の待機秒。短いとレート制限に当たる")
    args = ap.parse_args()

    min_turnover = args.min_turnover
    t0 = time.time()
    codes = candidates()
    print(f"{len(codes)}コードを調査します（売買代金 {min_turnover / 1e4:.0f}万円以上を採用）")
    print(f"バッチ間 {args.pause}秒待機。レート制限に当たったら自動で待って再試行します")
    found = probe(codes, args.limit, min_turnover, args.pause)

    # ETF・ETNを落とす。個別株の指標（業種相対強弱など）が意味を持たないため。
    # **取得できなかった銘柄は「判定不能」として残す。**
    # レート制限での失敗をETFと誤判定して普通の銘柄を捨てた事故があるため。
    from app import sectors  # noqa: E402

    print(f"\n{len(found)}銘柄の業種を確認してETF等を除外します…", flush=True)
    mapping, failed = sectors.build(list(found), progress=True, pause=args.pause / 3)
    before = len(found)
    # 明示的に "etf" と判定できたものだけを外す。
    # "unknown"（判定できなかった）は残す — 捨てると throttle 時に全滅するため。
    etf = [c for c in found if c not in failed and mapping.get(c) == "etf"]
    found = {c: v for c, v in found.items() if c not in etf}
    print(f"  {before} → {len(found)}銘柄"
          f"（ETF等として除外 {len(etf)}件 / 業種を取得できず保留 {len(failed)}件）")
    if len(failed) > before * 0.3:
        print("  ※ 未取得が多すぎます。レート制限の可能性があるため、"
              "後日 build_universe.py を再実行してください", file=sys.stderr)

    # 安全装置その1：下限を割ったら絶対に書かない。
    # 既存ファイルの有無に関係なく効く（比較対象が無いと素通りしてしまう事故があった）。
    # 内蔵リストが246銘柄なので、それを下回るなら書く価値がない。
    FLOOR = 200
    if len(found) < FLOOR:
        print(f"\n中止：{len(found)}銘柄しか取得できませんでした（下限{FLOOR}）。"
              f"\nYahooの過負荷時は例外を出さずに空の応答が返るため、"
              f"少なすぎる結果は信用できません。"
              f"\n時間を空けて再実行してください（進捗は保存済みです）。", file=sys.stderr)
        return 1

    # 安全装置その2：既存より大幅に減ったら書かない
    if OUT.exists():
        try:
            prev = len(json.loads(OUT.read_text(encoding="utf-8")).get("codes", []))
        except Exception:
            prev = 0
        if prev and len(found) < prev * 0.5:
            print(f"\n中止：既存{prev}銘柄に対し今回{len(found)}銘柄しか取得できませんでした。"
                  f"\nレート制限の可能性が高いため、既存ファイルは変更しません。"
                  f"\n時間を空けて再実行してください（進捗は保存済みなので続きから再開します）。",
                  file=sys.stderr)
            return 1

    # 売買代金の多い順に並べる（約定しやすい順）
    ordered = sorted(found.items(), key=lambda kv: -kv[1]["turnover"])
    OUT.write_text(
        json.dumps(
            {
                "built_at": time.strftime("%Y-%m-%d %H:%M"),
                "min_turnover": min_turnover,
                "min_price": MIN_PRICE,
                "count": len(ordered),
                "codes": [c for c, _ in ordered],
            },
            ensure_ascii=False, indent=1,
        ),
        encoding="utf-8",
    )
    print(f"\n{len(ordered)}銘柄を {OUT.name} に保存（{time.time() - t0:.0f}秒）")
    cheap = sum(1 for _, v in ordered if v["price"] <= 5000)
    print(f"  うち5,000円以下: {cheap}銘柄")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
