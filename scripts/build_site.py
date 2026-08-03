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
sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))

import calculator  # noqa: E402  （scripts/ 直下）
import update_button  # noqa: E402  （scripts/ 直下）

from app import (  # noqa: E402
    config, etf_timing, gap_momentum, indicators, momentum, positions, report, timeframes,
)

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


def daily_range_rows(p: dict, current: float | None) -> str:
    """1日の値動きを、現在値からの円建ての上下幅で示す。"""
    if not current or p.get("day_p50") is None:
        return ""

    def row(label, pct):
        if pct is None:
            return ""
        w = current * pct / 100
        return (f'<div class="pos__row"><span>{label}</span>'
                f'<span class="mono">{current - w:,.0f} 〜 {current + w:,.0f}円'
                f'<span class="proj__pct">（±{w:,.0f}円）</span></span></div>')

    return (f'<div class="proj__day">'
            f'<p class="proj__sub">1日の値動き（現在値 {current:,.0f}円 を基準・過去500日）</p>'
            f'{row("普通の日（中央値）", p.get("day_p50"))}'
            f'{row("荒い日（上位20%）", p.get("day_p80"))}</div>')


def projection_block(p: dict, price: float | None, current: float | None = None) -> str:
    """過去の実測分布を「どこまで上がったか」の形で見せる。予測ではない。"""
    if not p or not p.get("samples") or p.get("up_p50") is None:
        return ""

    def line(label, v, cls=""):
        if v is None:
            return ""
        # 金額を主、率を補助で表示する
        target = f"{price * (1 + v / 100):,.0f}円" if price else "—"
        return (f'<div class="pos__row {cls}"><span>{label}</span>'
                f'<span class="mono">{target}'
                f'<span class="proj__pct">（{v:+.1f}%）</span></span></div>')

    kind = "同型シグナル" if p.get("conditional") else "全期間"
    return f"""
    <div class="proj">
      <p class="proj__head">過去{p['samples']}回の実測（{p['horizon_days']}日以内・{kind}）</p>
      {line('半数はここまで上昇', p.get('up_p50'), 'proj__up')}
      {line('上位25%はここまで', p.get('up_p75'), 'proj__up')}
      {line('上位10%はここまで', p.get('up_p90'), 'proj__up')}
      {line('半数はここまで下落', p.get('down_p50'), 'proj__dn')}
      {line('下位10%はここまで', p.get('down_p90'), 'proj__dn')}
      {daily_range_rows(p, current)}
      <p class="proj__warn">これは予測ではなく過去の分布です。最大上昇に到達しても、
        そこで売らなければ利益は確定しません。</p>
    </div>"""


def regime_block(regime: dict) -> str:
    """市場急落の補助シグナル。モメンタムを置き換えず、強調表示するだけ。"""
    if not regime:
        return ""
    if regime.get("is_crash_day"):
        return f'''
    <div class="regime regime--crash">
      <p class="regime__head">📉 本日は市場急落シグナル</p>
      <p class="regime__body">{esc(regime.get("note", ""))}</p>
    </div>'''
    change = regime.get("change_pct")
    if change is None:
        return ""
    return f'''
    <p class="regime__quiet">{esc(regime.get("note", ""))}</p>'''


def etf_timing_block(et: dict) -> str:
    """主力①：ETF×米国前日終値タイミングの現在の状態。"""
    if not et or not et.get("available"):
        return '<p class="report-lead">ETFタイミングのデータを取得できませんでした。</p>'
    state = "買い持ち" if et.get("in_market") else "様子見（現金）"
    cls = "etf--in" if et.get("in_market") else "etf--out"
    action_cls = "etf__action--changed" if et.get("changed") else "etf__action--hold"
    return f"""
    <div class="etf {cls}">
      <p class="etf__state">現在の判定： <strong>{esc(state)}</strong>
        （{esc(et.get('signal_date', ''))}の米国市場終値時点）</p>
      <p class="etf__body">{esc(et.get('note', ''))}</p>
      <p class="etf__action {action_cls}">{esc(et.get('action_note', ''))}</p>
      <div class="pos__row"><span>{esc(etf_timing.ETF_SYMBOL)} 現在値</span>
        <span class="mono">{yen(et.get('etf_price'))}（{pct(et.get('etf_change_pct'))}）</span></div>
    </div>"""


def gap_pick_card(p) -> str:
    return f"""
    <div class="pos pos--buy">
      <div class="pos__head">
        <span class="pos__name">{esc(p.name)}</span>
        <span class="mono pos__code">{esc(p.code)}｜{esc(p.block or p.sector)}</span>
      </div>
      <div class="pos__row"><span>現在値</span>
        <span class="mono">{yen(p.current_price)}（{pct(p.change_pct)}）</span></div>
      <div class="pos__row"><span>急騰発生日</span>
        <span class="mono">{esc(p.entry_date)}（窓開け{pct(p.gap_pct)}）</span></div>
      <div class="pos__row"><span>エントリー価格</span><span class="mono">{yen(p.entry_price)}</span></div>
      <div class="pos__row pos__row--pnl"><span>エントリーからの騰落率</span>
        <span class="mono" style="color:{'var(--bull)' if p.return_pct > 0 else 'var(--bear)'}">
        {pct(p.return_pct)}</span></div>
      <div class="pos__row"><span>保有</span>
        <span class="mono">{p.days_held}営業日経過／残り{p.days_remaining}営業日目安</span></div>
    </div>"""


def gap_picks_block(picks: list) -> str:
    """主力②：決算モメンタム（窓開け急騰）近似の現在アクティブな銘柄一覧。"""
    if not picks:
        return ('<p class="report-lead">現在、該当する急騰銘柄はありません。'
                '資金はETF×先物タイミングのポジションで待機します。</p>')
    cards = "".join(gap_pick_card(p) for p in picks)
    return f'<div class="positions__grid">{cards}</div>'


def concentration_block(con: dict, total: int) -> str:
    """その日のTOP銘柄がどれくらい業種に偏っているかを示す。

    業種上限は設けない（検証で成績が悪化したため）。偏りを見せるだけ。
    """
    if not con or not con.get("counts"):
        return ""
    top = con.get("top_block")
    bars = "".join(
        f'<div class="conc__row"><span>{esc(name)}</span>'
        f'<span class="conc__bar"><i style="width:{n / total * 100:.0f}%"></i></span>'
        f'<span class="mono">{n}銘柄</span></div>'
        for name, n in con["counts"]
    )
    warn = ""
    if top and top[1] >= 4:
        warn = (f'<p class="conc__warn">⚠ {esc(top[0])}に{top[1]}銘柄が集中しています。'
                f'この業種が売られると、まとめて下落します。'
                f'（業種を散らす制限も検証しましたが、成績が悪化したため入れていません）</p>')
    return f'''
    <div class="conc">
      <p class="conc__head">業種の偏り（{con["distinct"]}業種に分散）</p>
      {bars}
      {warn}
    </div>'''


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

    tag = ('<span class="tag-cheap">少額枠</span>' if getattr(c, "affordable_pick", False)
           else "")
    mom = getattr(c, "momentum_pct", 0.0)
    mom_row = (
        f'<div class="pos__row pos__row--mom"><span>過去6か月の上昇率</span>'
        f'<span class="mono">{mom:+.1f}%</span></div>' if mom else ""
    )
    return f"""
    <div class="pos pos--{kind}">
      <div class="pos__head">
        <span class="pos__name">{esc(c.name)}{tag}</span>
        <span class="mono pos__code">{esc(c.code)}｜{esc(getattr(c, 'block', '') or c.sector)}</span>
      </div>
      <div class="star-badge star-badge--compact">
        <span class="badge {'badge--buy' if kind == 'buy' else 'badge--sell'}">{esc(c.judgement)}</span>
        <span class="stars">{stars(c.stars)}</span>
      </div>
      {mom_row}
      {rows}
      {projection_block(c.projection, c.entry_price or c.price, c.price)}
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
{calculator.CSS}
{update_button.CSS}
.proj{{margin:8px 0;padding:8px 10px;border-radius:6px;background:var(--bg);
  border:1px solid var(--border)}}
.proj__head{{margin:0 0 4px;font-size:11px;font-weight:700;color:var(--accent)}}
.proj__up .mono{{color:var(--bull)}}
.proj__dn .mono{{color:var(--bear)}}
.proj__warn{{margin:6px 0 0;font-size:10px;line-height:1.5;color:var(--warn)}}
.proj__pct{{color:var(--text-muted);font-size:10px}}
.proj__day{{margin-top:6px;padding-top:6px;border-top:1px solid var(--border)}}
.proj__sub{{margin:0 0 3px;font-size:10px;color:var(--text-muted)}}
.method{{margin:0 0 14px;padding:12px;border:1px solid var(--accent);border-radius:8px;
  background:rgba(110,231,196,.06)}}
.method__head{{margin:0 0 6px;font-size:13px;font-weight:700;color:var(--accent)}}
.method__body{{margin:0 0 8px;font-size:11px;line-height:1.7;color:var(--text)}}
.method__table{{width:100%;border-collapse:collapse;font-size:11px;margin-bottom:8px}}
.method__table th,.method__table td{{padding:4px 6px;border-bottom:1px solid var(--border);
  text-align:right;font-family:var(--font-mono)}}
.method__table th:first-child,.method__table td:first-child{{text-align:left;
  font-family:var(--font-ui)}}
.method__plus{{color:var(--bull);font-weight:700}}
.method__note{{margin:0 0 8px;font-size:10px;line-height:1.6;color:var(--text-muted)}}
.method__warn{{margin:0;padding:8px 10px;border-left:3px solid var(--warn);
  background:rgba(232,163,61,.1);font-size:11px;line-height:1.7;color:var(--warn)}}
.conc{{margin:0 0 14px;padding:10px 12px;border:1px solid var(--border);
  border-radius:8px;background:var(--panel)}}
.conc__head{{margin:0 0 6px;font-size:12px;font-weight:700}}
.conc__row{{display:grid;grid-template-columns:1fr 90px 55px;gap:8px;align-items:center;
  font-size:11px;padding:2px 0;color:var(--text-muted)}}
.conc__bar{{background:var(--bg);border-radius:3px;height:8px;overflow:hidden}}
.conc__bar i{{display:block;height:100%;background:var(--accent)}}
.conc__row .mono{{text-align:right}}
.conc__warn{{margin:8px 0 0;padding:8px 10px;border-left:3px solid var(--warn);
  background:rgba(232,163,61,.1);font-size:11px;line-height:1.7;color:var(--warn)}}
.regime--crash{{margin:0 0 14px;padding:12px;border-radius:8px;
  border:1px solid var(--bull);background:rgba(229,72,77,.08)}}
.regime__head{{margin:0 0 4px;font-size:13px;font-weight:700;color:var(--bull)}}
.regime__body{{margin:0;font-size:11px;line-height:1.7;color:var(--text)}}
.regime__quiet{{margin:0 0 12px;font-size:10px;color:var(--text-muted)}}
.pos__row--mom .mono{{color:var(--bull);font-weight:700;font-size:13px}}
.tag-cheap{{margin-left:6px;padding:1px 6px;border-radius:3px;font-size:10px;
  background:rgba(110,231,196,.15);color:var(--accent);font-weight:600}}
.etf{{margin:0 0 14px;padding:12px;border-radius:8px;border:1px solid var(--border)}}
.etf--in{{border-color:var(--bull);background:rgba(229,72,77,.06)}}
.etf--out{{border-color:var(--border);background:var(--panel)}}
.etf__state{{margin:0 0 4px;font-size:14px;font-weight:700}}
.etf__body{{margin:0 0 8px;font-size:11px;line-height:1.6;color:var(--text-muted)}}
.etf__action{{margin:0 0 10px;padding:8px 10px;border-radius:6px;font-size:11px;line-height:1.7}}
.etf__action--changed{{background:rgba(232,163,61,.15);border-left:3px solid var(--warn);color:var(--warn)}}
.etf__action--hold{{background:var(--bg);border-left:3px solid var(--border);color:var(--text-muted)}}
.report-section--muted{{margin-top:28px;padding-top:16px;border-top:1px solid var(--border)}}
.report-section--muted h2{{color:var(--text-muted);font-size:13px}}
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
    {update_button.build()}
    {positions_section() if include_positions else ''}

    {calculator.build(json.dumps({
        "stop_atr_multiple": config.STOP_ATR_MULTIPLE,
        "tp1": config.TAKE_PROFIT_R_MULTIPLE_1,
        "tp2": config.TAKE_PROFIT_R_MULTIPLE_2,
        "forward_bars": tf.forward_bars,
        "is_trailing": config.is_trailing(),
        "generated_at": data["generated_at"],
    }, ensure_ascii=False, separators=(",", ":")))}

    <section class="report-section">
      <h2>主力① ETF×米国前日終値タイミング</h2>
      <p class="report-lead">
        前営業日の米国市場（S&amp;P500）の騰落を見て、日経225連動ETFを
        買い持ちするか様子見（現金）するかを切り替える方式。
        ±{etf_timing.THRESHOLD:.1%}未満の小さな変動では前日の状態を維持します。
      </p>
      <div class="method">
        <p class="method__head">検証結果（{etf_timing.ETF_SYMBOL}・17年・片道0.3%のコスト＋実現益課税込み）</p>
        <table class="method__table">
          <tr><th></th><th>この方法</th><th>単純保有</th></tr>
          <tr><td>全期間</td><td>+{etf_timing.TRACK_RECORD['全期間']['strategy']:,.0f}%</td>
              <td>+{etf_timing.TRACK_RECORD['全期間']['benchmark']:,.0f}%</td></tr>
          <tr><td>前半70%</td><td>+{etf_timing.TRACK_RECORD['前半70%']['strategy']:,.0f}%</td>
              <td>+{etf_timing.TRACK_RECORD['前半70%']['benchmark']:,.0f}%</td></tr>
          <tr><td>後半30%</td><td>+{etf_timing.TRACK_RECORD['後半30%']['strategy']:,.0f}%</td>
              <td>+{etf_timing.TRACK_RECORD['後半30%']['benchmark']:,.0f}%</td></tr>
        </table>
        <p class="method__note">
          {etf_timing.TRACK_RECORD['win_years']}で単純保有を上回りました。最大ドローダウンも
          単純保有(-25〜-31%)よりずっと浅い(-8〜-13%)。
        </p>
        <p class="method__warn">
          ⚠ 単一のETFに全額を賭ける形になるため、モメンタムの10銘柄分散とは異なる
          リスク（市場全体のシステミックショック）を負います。過去の相関に基づく
          統計的な傾向であり、将来この関係が続く保証はありません。
        </p>
      </div>
      {etf_timing_block(data.get('etf_timing'))}
    </section>

    <section class="report-section">
      <h2>主力② 決算モメンタム（窓開け急騰）近似</h2>
      <p class="report-lead">
        前日終値比+{gap_momentum.GAP_THRESHOLD:.0%}以上の窓開けを急騰イベントとみなし、
        発生から{gap_momentum.HOLD_DAYS}営業日を目安に保有します。
        決算カレンダーやPTS(夜間取引)データが無いための近似指標で、
        決算以外の材料による窓開けも混ざります。
        決算集中期は該当銘柄が多くなりすぎるため、直近{gap_momentum.MAX_DISPLAY}件に絞って
        表示しています（検証はイベント単位の平均で行っており、
        「これらを全部同時に保有できるか」自体は検証していません）。
      </p>
      <div class="method">
        <p class="method__head">検証結果（241銘柄・10年・片道0.3%のコスト＋実現益課税込み・重複除去後）</p>
        <table class="method__table">
          <tr><th></th><th>イベント後平均</th><th>同銘柄ランダム日</th></tr>
          <tr><td>全期間</td><td>+{gap_momentum.TRACK_RECORD['全期間']['strategy']:.2f}%</td>
              <td>+{gap_momentum.TRACK_RECORD['全期間']['benchmark']:.2f}%</td></tr>
          <tr><td>前半70%</td><td>+{gap_momentum.TRACK_RECORD['前半70%']['strategy']:.2f}%</td>
              <td>+{gap_momentum.TRACK_RECORD['前半70%']['benchmark']:.2f}%</td></tr>
          <tr><td>後半30%</td><td>+{gap_momentum.TRACK_RECORD['後半30%']['strategy']:.2f}%</td>
              <td>+{gap_momentum.TRACK_RECORD['後半30%']['benchmark']:.2f}%</td></tr>
        </table>
        <p class="method__warn">
          ⚠ 該当銘柄がない日は資金の置き場がないため、主力①（ETF×先物タイミング）の
          ポジションで待機する設計です。
        </p>
      </div>
      {gap_picks_block(data.get('gap_picks') or [])}
    </section>

    <section class="report-section report-section--muted">
      <h2>参考：モメンタム（旧主力）</h2>
      <p class="report-lead">
        個別銘柄の値動きが大きく、最悪期-18.4%（5回に1回は6か月マイナス）という
        リスクがあるため、主力からは外しました。選定ロジック自体は今も有効な
        検証結果に基づいており、参考情報として引き続き表示します。
      </p>

      <div class="method">
        <p class="method__head">選び方：過去6か月の上昇率が高い順（{config.REPORT_TOP_N}銘柄に分散）</p>
        <p class="method__body">
          移動平均・RSI・ボリンジャーバンドを使う選び方は、10年検証で
          <strong>すべて単純保有に負けました</strong>。唯一勝ったのが、指標を使わず
          「上がっている銘柄をそのまま買って持つ」この方法です。
          ランダムに10銘柄選ぶ対照群が負けていることを確認済みで、
          上昇相場だから勝てたのではありません。
        </p>
        <table class="method__table">
          <tr><th></th><th>この方法</th><th>全銘柄を持つだけ</th><th>差</th></tr>
          <tr><td>前半7年</td><td>+{momentum.TRACK_RECORD['前半7年']['strategy']:.2f}%</td>
              <td>+{momentum.TRACK_RECORD['前半7年']['benchmark']:.2f}%</td>
              <td class="method__plus">+4.13%</td></tr>
          <tr><td>後半3年</td><td>+{momentum.TRACK_RECORD['後半3年']['strategy']:.2f}%</td>
              <td>+{momentum.TRACK_RECORD['後半3年']['benchmark']:.2f}%</td>
              <td class="method__plus">+24.71%</td></tr>
        </table>
        <p class="method__note">
          551銘柄・10年・往復コスト0.1%込み。6か月保有あたりの平均です。
          <strong>想定保有期間は{momentum.describe_hold(momentum.HOLD_DAYS)}</strong>で、
          6か月ごとに入れ替えます。株価100円未満は除外しています。
        </p>
        <p class="method__warn">
          ⚠ <strong>最悪の局面では-18.4%</strong>でした（全銘柄保有では-10.3%）。
          勝率は78%で、5回に1回は6か月間マイナスです。
          後半3年の+37%は日本株が異常に強かった時期の数字で、この水準が続く前提は危険です。
          個別銘柄では大きく外れるため、分散して初めて成立する方法です。
        </p>
      </div>

      <p class="report-lead">
        ★・RSI・ボリンジャーバンドは判断材料として表示していますが、
        <strong>この選定には一切使っていません</strong>（予測力がないと検証済みのため）。
        「少額枠」は{config.REPORT_AFFORDABLE_PRICE:,}円以下を確保するため順位を繰り上げた銘柄です。
      </p>
      {concentration_block(data.get('concentration'), len(data['top_buys']) or 1)}
      <div class="positions__grid">{buys or '<p class="report-lead">該当なし</p>'}</div>
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

    # 銘柄データはHTMLに埋め込まず別ファイルにする。
    # 母集団を1000銘柄規模に広げるとHTMLが数MBになり、スマホでの表示が重くなるため。
    # ブラウザ側でキャッシュされるので、2回目以降の読み込みは速い。
    (OUT_DIR / "stocks.json").write_text(
        json.dumps(
            {"dates": data["date_axis"], "stocks": data["stocks"]},
            ensure_ascii=False, separators=(",", ":"),
        ),
        encoding="utf-8",
    )

    payload = {
        "generated_at": data["generated_at"],
        "data_date": str(data["data_date"].date()) if data["data_date"] is not None else None,
        "universe_size": data["universe_size"],
        "concentration": data.get("concentration"),
        "market_regime": data.get("market_regime"),
        "etf_timing": data.get("etf_timing"),
        "gap_picks": [vars(p) for p in (data.get("gap_picks") or [])],
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
