"""
静的サイトを生成する。サーバー不要・PCの起動も不要（GitHub Actions で動く）。

出力: docs/index.html（単体で開ける自己完結HTML）と docs/report.json

使い方:
    python scripts/build_site.py                 # 市場分析のみ（個人情報を含まない）
    python scripts/build_site.py --with-positions  # 保有ポジションも含める

--with-positions は**公開先では使わないでください。** 保有銘柄・取得単価・
株数がページに載り、URLを知った人に見られます。
"""
import argparse
import html
import json
import pathlib
import sys

ROOT = pathlib.Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "backend"))

from app import config, indicators, positions, report, timeframes  # noqa: E402

OUT_DIR = ROOT / "docs"
CSS_PATH = ROOT / "frontend" / "src" / "styles.css"


def esc(v) -> str:
    return html.escape(str(v))


def yen(v) -> str:
    return "—" if v is None else f"{v:,.1f}円"


def pct(v, plus=True) -> str:
    if v is None:
        return "—"
    sign = "+" if plus and v > 0 else ""
    return f"{sign}{v}%"


def stars(n: int) -> str:
    return "".join(
        f'<span class="star{" star--on" if i <= (n or 0) else ""}">★</span>' for i in range(1, 6)
    )


def candidate_card(c, kind: str) -> str:
    edge_html = ""
    if c.edge is not None:
        bad = "" if c.edge > 0 else " conf__edge--bad"
        verdict = "単純保有より有利" if c.edge > 0 else "単純保有より不利"
        edge_html = (
            f'<div class="conf__edge{bad}"><p class="conf__edge-head">'
            f'対照実験：{verdict}（{c.edge:+.2f}%）</p></div>'
        )

    rows = "".join(
        f'<div class="pos__row"><span>{label}</span>'
        f'<span class="mono">{value}</span></div>'
        for label, value in (
            ("現在値", f"{yen(c.price)}（{pct(c.change_pct)}）"),
            ("エントリー目安", f"{yen(c.entry_price)}"
                              f"{f'（現在値{pct(c.entry_gap_pct)}）' if c.entry_gap_pct is not None else ''}"),
            ("損切りライン", yen(c.stop_loss)),
            ("利確目標①", yen(c.take_profit_1)),
        )
    )
    reasons = "".join(f"<li>{esc(r)}</li>" for r in c.reasons[:4])
    warns = "".join(f"<li>{esc(w)}</li>" for w in c.warnings[:3])

    return f"""
    <div class="pos pos--{kind}">
      <div class="pos__head">
        <span class="pos__name">{esc(c.name)}</span>
        <span class="mono pos__code">{esc(c.code)}｜{esc(c.sector)}</span>
      </div>
      <div class="star-badge star-badge--compact">
        <span class="badge {'badge--buy' if kind == 'buy' else 'badge--sell'}">{esc(c.judgement)}</span>
        <span class="stars">{stars(c.stars)}</span>
      </div>
      {rows}
      {edge_html}
      {f'<ul class="conf__caveats">{reasons}</ul>' if reasons else ''}
      {f'<ul class="conf__caveats" style="color:var(--warn)">{warns}</ul>' if warns else ''}
    </div>"""


def positions_section() -> str:
    """保有ポジション。--with-positions のときだけ呼ばれる。"""
    import asyncio

    from app import datasource

    tf = timeframes.get(timeframes.DEFAULT_KEY)
    held = positions.load()
    if not held:
        return ""

    async def gather():
        out = []
        for p in held:
            try:
                df = indicators.compute_all(await datasource.fetch_bars(p["code"], tf), tf)
                out.append(positions.evaluate(p, df, tf))
            except Exception:
                pass
        return out

    rows = asyncio.run(gather())
    if not rows:
        return ""
    summary = positions.summarize(rows)

    cards = []
    for p in rows:
        if not p.get("has_cost"):
            continue
        color = "var(--bull)" if p["unrealized_yen"] > 0 else "var(--bear)"
        cards.append(f"""
        <div class="pos pos--{p.get('status', 'none')}">
          <div class="pos__head"><span class="pos__name">{esc(p['name'])}</span>
            <span class="mono pos__code">{esc(p['code'])}</span></div>
          <div class="pos__row"><span>平均取得</span>
            <span class="mono">{p['avg_cost']:,.1f}円 × {p['shares']}株</span></div>
          <div class="pos__row"><span>現在値</span>
            <span class="mono">{p['price']:,.1f}円</span></div>
          <div class="pos__row pos__row--pnl"><span>含み損益</span>
            <span class="mono" style="color:{color}">{p['unrealized_yen']:+,.0f}円
            （{p['unrealized_pct']:+}%）</span></div>
          <div class="pos__lines">
            <div class="pos__row pos__row--trail"><span>トレーリングストップ</span>
              <span class="mono">{p.get('trailing_stop', 0):,.1f}円</span></div>
            <p class="pos__note">{esc(p.get('trailing_note', ''))}</p>
          </div>
          <p class="pos__status pos__status--{p.get('status')}">{esc(p.get('status_label', ''))}</p>
        </div>""")

    total = ""
    if summary.get("unrealized_pct") is not None:
        col = "var(--bull)" if summary["unrealized_yen"] > 0 else "var(--bear)"
        total = (f'<span class="mono positions__total" style="color:{col}">'
                 f'含み損益 {summary["unrealized_yen"]:+,.0f}円'
                 f'（{summary["unrealized_pct"]:+}%）</span>')

    return f"""
    <section class="positions">
      <div class="positions__head"><h3>保有ポジション</h3>{total}</div>
      <div class="positions__grid">{''.join(cards)}</div>
      <p class="positions__disclaimer">⚠ 損切り・利確ラインの根拠は、検証で
        買い持ちに勝てるとは限らないルールです。利益が最大になる水準ではありません。</p>
    </section>"""


def build_html(data: dict, include_positions: bool) -> str:
    css = CSS_PATH.read_text(encoding="utf-8")
    tf = data["timeframe"]
    date_str = data["data_date"].strftime("%Y-%m-%d") if data["data_date"] is not None else "—"

    buys = "".join(candidate_card(c, "buy") for c in data["top_buys"])
    sells = "".join(candidate_card(c, "sell") for c in data["top_sells"])

    return f"""<!doctype html>
<html lang="ja"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>日本株テクニカル分析 日次レポート</title>
<style>{css}
.app__body{{display:block;padding:0 16px 32px}}
.report-section{{margin-top:20px}}
.report-section h2{{font-size:15px;margin:0 0 4px}}
.report-lead{{margin:0 0 12px;font-size:11px;color:var(--text-muted);line-height:1.6}}
.positions__grid{{grid-template-columns:repeat(auto-fit,minmax(260px,1fr))}}
.app__header{{padding:20px 16px 14px}}
.tf-info,.snap-warn{{padding-left:16px;padding-right:16px}}
.snap-warn{{margin:0;padding-top:10px;padding-bottom:10px;font-size:11px;
  color:var(--warn);background:rgba(232,163,61,.1);border-bottom:1px solid var(--border);line-height:1.6}}
</style></head><body>
<div class="app">
  <header class="app__header">
    <div>
      <h1>日本株テクニカル分析 日次レポート</h1>
      <p class="app__subtitle">{esc(data['universe_size'])}銘柄をスキャン
        （分析成功 {esc(data['analyzed'])}銘柄）</p>
    </div>
    <div class="app__status"><span class="mono">データ基準日 {esc(date_str)}／
      生成 {esc(data['generated_at'])}</span></div>
  </header>

  <p class="snap-warn">⚠ これは投資助言ではありません。「シグナルが強い」とは
    チェックリストの条件が揃っているという意味で、上がる可能性が高いという意味ではありません。
    検証では、このルールが単純な買い持ちに勝てないケースが多くありました。
    各銘柄の「対照実験」欄を必ず確認してください。</p>

  <p class="tf-info"><strong>{esc(tf.label)}の設定：</strong>
    移動平均 {esc(' / '.join(tf.ma_labels.values()))}／
    RSI {tf.rsi_short}本・{tf.rsi_long}本／BB {tf.bb_period}本／
    エントリー={esc(config.ENTRY_MODE)}／損切り={esc(config.STOP_MODE)}×{config.STOP_ATR_MULTIPLE:g}／
    保有上限 {tf.forward_bars}日</p>

  <div class="app__body">
    {positions_section() if include_positions else ''}

    <section class="report-section">
      <h2>買いシグナルが強い銘柄</h2>
      <p class="report-lead">★が高く、エントリー目安まで近い順。推奨ではありません。</p>
      <div class="positions__grid">{buys or '<p class="report-lead">該当なし</p>'}</div>
    </section>

    <section class="report-section">
      <h2>売りシグナルが出ている銘柄</h2>
      <p class="report-lead">★が低い、または下降のパーフェクトオーダーが成立している順。</p>
      <div class="positions__grid">{sells or '<p class="report-lead">該当なし</p>'}</div>
    </section>
  </div>
</div></body></html>"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--with-positions", action="store_true",
                    help="保有ポジションを含める（公開先では使わないこと）")
    ap.add_argument("--no-backtest", action="store_true", help="バックテストを省略して高速化")
    args = ap.parse_args()

    print("分析中…", flush=True)
    data = report.build(with_backtest=not args.no_backtest)

    OUT_DIR.mkdir(exist_ok=True)
    (OUT_DIR / "index.html").write_text(
        build_html(data, args.with_positions), encoding="utf-8"
    )

    payload = {
        "generated_at": data["generated_at"],
        "data_date": str(data["data_date"].date()) if data["data_date"] is not None else None,
        "universe_size": data["universe_size"],
        "analyzed": data["analyzed"],
        "top_buys": [vars(c) for c in data["top_buys"]],
        "top_sells": [vars(c) for c in data["top_sells"]],
    }
    (OUT_DIR / "report.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=1, default=str), encoding="utf-8"
    )
    # GitHub Pages が _ 始まりのパスを無視しないようにする
    (OUT_DIR / ".nojekyll").write_text("", encoding="utf-8")

    print(f"生成完了: {OUT_DIR/'index.html'}")
    print(f"  買い候補 {len(data['top_buys'])}件 / 売り候補 {len(data['top_sells'])}件")


if __name__ == "__main__":
    main()
