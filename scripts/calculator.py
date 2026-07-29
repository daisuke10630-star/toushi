"""
スマホ用の保有ポジション計算機（静的ページに埋め込むHTML/CSS/JS）。

■ 設計方針
GitHub Pages はサーバーを持てないので、計算はすべてブラウザ内で行う。
そのために「現在値・ATR・直近の高値系列」だけをページに同梱し、
**取得単価と株数は端末の localStorage にのみ保存する。**
入力した保有情報がGitHubや外部に送られることは一切ない。

■ 計算内容（backend/app/positions.py と同じ式）
  損切り（初期）   = 取得単価 − ATR × STOP_ATR_MULTIPLE
  利確①／②       = 取得単価 + 損切り幅 × 1.5／2.0
  トレーリング     = max(取得後の高値 − 損切り幅, 初期の損切り)
"""


def build(config_json: str) -> str:
    """計算機セクションのHTMLを返す。config_json は JS に渡す設定値。"""
    return """
<section class="report-section" id="calc">
  <h2>保有ポジション計算機</h2>
  <p class="report-lead">
    取得単価と株数を入れると、損益・損切り・利確・トレーリングストップを計算します。
    <strong>入力内容はこの端末の中だけに保存され、外部には送信されません。</strong>
    株価は下記「データ基準日」時点のものです（リアルタイムではありません）。
  </p>

  <p class="price-status" id="price-status">株価を確認中…</p>

  <form class="calc__form" id="calc-form" autocomplete="off">
    <div class="calc__row">
      <label>証券コード
        <input id="calc-code" list="calc-codes" placeholder="8136" inputmode="numeric" required>
        <datalist id="calc-codes"></datalist>
      </label>
      <label>取得単価（円）
        <input id="calc-cost" placeholder="1185" inputmode="decimal" required>
      </label>
      <label>株数
        <input id="calc-shares" placeholder="300" inputmode="numeric" required>
      </label>
      <label>取得日（任意）
        <input id="calc-date" type="date">
      </label>
    </div>
    <div class="calc__actions">
      <button type="submit">追加・更新する</button>
      <span id="calc-hint" class="calc__hint"></span>
    </div>
  </form>

  <div id="calc-total" class="calc__total"></div>
  <div id="calc-list" class="positions__grid"></div>
</section>

<script>
(function () {
  var CFG = """ + config_json + """;
  var KEY = 'stock-analyzer-positions-v1';
  var $ = function (id) { return document.getElementById(id); };

  function load() {
    try { return JSON.parse(localStorage.getItem(KEY)) || []; } catch (e) { return []; }
  }
  function save(rows) { localStorage.setItem(KEY, JSON.stringify(rows)); }
  function yen(v) { return v == null ? '—' : v.toLocaleString('ja-JP',
      { minimumFractionDigits: 1, maximumFractionDigits: 1 }) + '円'; }
  function signed(v, unit) {
    if (v == null) return '—';
    var s = v > 0 ? '+' : '';
    return s + v.toLocaleString('ja-JP', { maximumFractionDigits: 2 }) + (unit || '');
  }
  function color(v) {
    return v > 0 ? 'var(--bull)' : v < 0 ? 'var(--bear)' : 'var(--text-muted)';
  }

  // 取得日以降（未指定なら直近 forward_bars 本）の高値を拾う
  function peakSince(stock, since) {
    var hi = null;
    for (var i = 0; i < stock.highs.length; i++) {
      if (since && stock.dates[i] < since) continue;
      if (hi === null || stock.highs[i] > hi) hi = stock.highs[i];
    }
    if (hi === null) {
      var tail = stock.highs.slice(-CFG.forward_bars);
      hi = Math.max.apply(null, tail);
    }
    return hi;
  }

  function evaluate(pos) {
    var s = CFG.stocks[pos.code];
    if (!s) return { code: pos.code, missing: true };

    var cost = pos.cost, shares = pos.shares, price = s.price;
    var risk = s.atr * CFG.stop_atr_multiple;
    var stop = cost - risk;
    var peak = peakSince(s, pos.date);
    var trail = Math.max(peak - risk, stop);

    var status, label;
    if (CFG.is_trailing) {
      if (price <= trail) { status = 'stop_hit'; label = 'トレーリングストップを下回っています'; }
      else { status = 'holding';
             label = 'トレーリングまで ' + ((price / trail - 1) * 100).toFixed(1) + '% の余裕'; }
    } else if (price <= stop) { status = 'stop_hit'; label = '損切りラインを下回っています'; }
    else { status = 'holding'; label = '損切りと利確の間にあります'; }

    return {
      code: pos.code, name: s.name, price: price, cost: cost, shares: shares,
      pnlYen: Math.round((price - cost) * shares),
      pnlPct: Math.round((price / cost - 1) * 10000) / 100,
      stop: Math.round(stop * 10) / 10,
      tp1: Math.round((cost + risk * CFG.tp1) * 10) / 10,
      tp2: Math.round((cost + risk * CFG.tp2) * 10) / 10,
      peak: peak, trail: Math.round(trail * 10) / 10,
      lockedPct: Math.round((trail / cost - 1) * 10000) / 100,
      atr: s.atr, risk: Math.round(risk * 10) / 10,
      status: status, label: label, date: pos.date, proj: s.proj
    };
  }

  // 4項目のうちいくつ満たしているか。推奨ではなく条件の充足状況を示す
  function scoreBlock(code) {
    var s = CFG.stocks[code];
    if (!s || !s.checks) return '';
    var n = s.score, total = s.checks.length;
    var cls = n === total ? 'score--full' : n >= total - 1 ? 'score--ok' : 'score--low';
    var items = s.checks.map(function (c) {
      return '<div class="pos__row"><span>' + (c.ok ? '✓ ' : '✕ ') + c.label +
        '</span><span class="mono">' + c.detail + '</span></div>';
    }).join('');
    return '<div class="score ' + cls + '">' +
      '<p class="score__head">エントリー条件 ' + n + ' / ' + total + ' 充足　★' + s.stars +
      '（' + s.judgement + '）</p>' + items +
      '<div class="pos__row score__lines"><span>エントリー目安</span><span class="mono">' +
      (s.entry ? yen(s.entry) : '—') + '</span></div>' +
      '<div class="pos__row score__lines"><span>損切り／利確①／利確②</span><span class="mono">' +
      (s.stop ? yen(s.stop) : '—') + ' / ' + (s.tp1 ? yen(s.tp1) : '—') + ' / ' +
      (s.tp2 ? yen(s.tp2) : '—') + '</span></div>' +
      '<p class="proj__warn">条件を満たす＝上がる、ではありません。' +
      '検証では買い持ちに負ける銘柄も多くあります。</p></div>';
  }

  // 1日の値動きを、現在値からの円建ての上下幅で示す
  function dayRange(p, current) {
    if (!current || p.day50 == null) return '';
    var row = function (label, pct) {
      if (pct == null) return '';
      var w = current * pct / 100;
      return '<div class="pos__row"><span>' + label + '</span><span class="mono">' +
        Math.round(current - w).toLocaleString('ja-JP') + ' 〜 ' +
        Math.round(current + w).toLocaleString('ja-JP') + '円' +
        '<span class="proj__pct">（±' + Math.round(w).toLocaleString('ja-JP') +
        '円）</span></span></div>';
    };
    return '<div class="proj__day"><p class="proj__sub">1日の値動き（現在値 ' +
      Math.round(current).toLocaleString('ja-JP') + '円 を基準・過去500日）</p>' +
      row('普通の日（中央値）', p.day50) + row('荒い日（上位20%）', p.day80) + '</div>';
  }

  // 過去の実測分布。予測ではないので幅（中央値・上位25%など）で示す
  function projBlock(r) {
    var p = r.proj;
    if (!p || !p.n || p.up50 == null) return '';
    var line = function (label, v, cls) {
      if (v == null) return '';
      var target = Math.round(r.price * (1 + v / 100));
      return '<div class="pos__row ' + cls + '"><span>' + label +
        '</span><span class="mono">' + target.toLocaleString('ja-JP') + '円' +
        '<span class="proj__pct">（' + (v > 0 ? '+' : '') + v.toFixed(1) + '%）</span>' +
        '</span></div>';
    };
    return '<div class="proj"><p class="proj__head">現在値から先の過去分布（' +
      p.days + '日以内・' + p.n.toLocaleString('ja-JP') + '営業日分）</p>' +
      line('半数はここまで上昇', p.up50, 'proj__up') +
      line('上位25%はここまで', p.up75, 'proj__up') +
      line('上位10%はここまで', p.up90, 'proj__up') +
      line('半数はここまで下落', p.dn50, 'proj__dn') +
      line('下位10%はここまで', p.dn90, 'proj__dn') +
      dayRange(p, r.price) +
      '<p class="proj__warn">これは予測ではなく過去の分布です。' +
      '最大上昇に到達しても、そこで売らなければ利益は確定しません。</p></div>';
  }

  function card(r) {
    if (r.missing) {
      return '<div class="pos"><div class="pos__head"><span class="pos__name">' + r.code +
        '</span></div><p class="pos__note pos__note--warn">この銘柄のデータがありません。' +
        '対象は日経225中心の' + Object.keys(CFG.stocks).length + '銘柄です。</p>' +
        '<button class="posform__remove" data-del="' + r.code + '">削除</button></div>';
    }
    var row = function (a, b, cls) {
      return '<div class="pos__row' + (cls ? ' ' + cls : '') + '"><span>' + a +
        '</span><span class="mono">' + b + '</span></div>';
    };
    return '<div class="pos pos--' + r.status + '">' +
      '<div class="pos__head"><span class="pos__name">' + r.name +
      '</span><span class="mono pos__code">' + r.code + '</span></div>' +
      row('取得単価', yen(r.cost) + ' × ' + r.shares + '株') +
      row('現在値', yen(r.price)) +
      '<div class="pos__row pos__row--pnl"><span>含み損益</span><span class="mono" style="color:' +
      color(r.pnlYen) + '">' + signed(r.pnlYen, '円') + '（' + signed(r.pnlPct, '%') + '）</span></div>' +
      '<div class="pos__lines">' +
      '<div class="pos__row pos__row--trail"><span>トレーリングストップ</span><span class="mono">' +
      yen(r.trail) + ' <span style="color:' + color(r.lockedPct) + '">（' +
      signed(r.lockedPct, '%') + 'で確定）</span></span></div>' +
      row('初期の損切り', yen(r.stop)) +
      row('利確①／②', yen(r.tp1) + ' / ' + yen(r.tp2)) +
      '<p class="pos__note">取得後の高値 ' + yen(r.peak) + ' − ATR(' + r.atr + '円)×' +
      CFG.stop_atr_multiple + ' = ' + yen(r.trail) + '。高値更新で切り上がります' +
      (r.date ? '（' + r.date + '以降で計算）' : '（取得日未入力のため直近' +
        CFG.forward_bars + '営業日で計算）') + '</p></div>' +
      scoreBlock(r.code) +
      projBlock(r) +
      '<p class="pos__status pos__status--' + r.status + '">' + r.label + '</p>' +
      '<button class="posform__remove" data-del="' + r.code + '">削除</button></div>';
  }

  function render() {
    var rows = load().map(evaluate);
    $('calc-list').innerHTML = rows.map(card).join('');

    var priced = rows.filter(function (r) { return !r.missing; });
    if (priced.length) {
      var cost = priced.reduce(function (s, r) { return s + r.cost * r.shares; }, 0);
      var val = priced.reduce(function (s, r) { return s + r.price * r.shares; }, 0);
      var diff = Math.round(val - cost);
      var pct = Math.round((val / cost - 1) * 10000) / 100;
      $('calc-total').innerHTML = '<span class="mono" style="color:' + color(diff) +
        '">合計 含み損益 ' + signed(diff, '円') + '（' + signed(pct, '%') + '）</span>' +
        '<span class="calc__hint">評価額 ' + Math.round(val).toLocaleString('ja-JP') + '円</span>';
    } else {
      $('calc-total').innerHTML = '';
    }

    Array.prototype.forEach.call(document.querySelectorAll('[data-del]'), function (b) {
      b.onclick = function () {
        save(load().filter(function (p) { return p.code !== b.getAttribute('data-del'); }));
        render();
      };
    });
  }

  $('calc-form').onsubmit = function (e) {
    e.preventDefault();
    var code = $('calc-code').value.trim();
    var cost = parseFloat($('calc-cost').value);
    var shares = parseInt($('calc-shares').value, 10);
    if (!code || !(cost > 0) || !(shares > 0)) {
      $('calc-hint').textContent = '証券コード・取得単価・株数を入力してください';
      return;
    }
    var rows = load().filter(function (p) { return p.code !== code; });
    rows.push({ code: code, cost: cost, shares: shares, date: $('calc-date').value || null });
    save(rows);
    $('calc-hint').textContent = code + ' を保存しました（この端末内のみ）';
    $('calc-code').value = ''; $('calc-cost').value = '';
    $('calc-shares').value = ''; $('calc-date').value = '';
    render();
  };

  // 銘柄コードの候補を用意する
  var dl = $('calc-codes');
  Object.keys(CFG.stocks).sort().forEach(function (c) {
    var o = document.createElement('option');
    o.value = c; o.label = CFG.stocks[c].name;
    dl.appendChild(o);
  });

  render();

  // 相場中に更新される prices.json を読み、株価だけ新しいものに差し替える。
  // 分析（★・エントリー目安など）は夜間バッチの結果のまま。
  // キャッシュを避けるため毎回クエリを変える。
  fetch('prices.json?t=' + Date.now(), { cache: 'no-store' })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) {
      if (!d || !d.prices) return;
      var n = 0;
      Object.keys(d.prices).forEach(function (code) {
        if (CFG.stocks[code]) {
          CFG.stocks[code].price = d.prices[code].price;
          CFG.stocks[code].change_pct = d.prices[code].change_pct;
          n++;
        }
      });
      var el = $('price-status');
      if (el) {
        el.innerHTML = '株価は <strong>' + d.updated_at + '</strong> 時点（' + n +
          '銘柄）。' + d.note;
        el.classList.add('price-status--live');
      }
      render();
    })
    .catch(function () { /* 取得できなくても夜間バッチの価格で表示を続ける */ });

  // 表示中のHTMLが古い（ブラウザのキャッシュ）場合に気づけるようにする。
  // GitHub Pages は index.html をしばらくキャッシュするため、
  // 分析が更新されても古いTOP5が表示され続けることがある。
  fetch('report.json?t=' + Date.now(), { cache: 'no-store' })
    .then(function (r) { return r.ok ? r.json() : null; })
    .then(function (d) {
      if (!d || !d.generated_at || d.generated_at === CFG.generated_at) return;
      var b = document.createElement('div');
      b.className = 'stale-banner';
      b.innerHTML = '⚠ 新しい分析（' + d.generated_at +
        ' 時点）が公開されています。表示中はブラウザに保存された古い内容です。' +
        '<button id="stale-reload">最新に更新する</button>';
      document.querySelector('.app').prepend(b);
      document.getElementById('stale-reload').onclick = function () {
        location.replace(location.pathname + '?r=' + Date.now());
      };
    })
    .catch(function () {});
})();
</script>"""


CSS = """
.calc__form{background:var(--panel);border:1px solid var(--border);border-radius:8px;
  padding:14px;margin-bottom:14px}
.calc__row{display:flex;flex-wrap:wrap;gap:10px}
.calc__form label{display:flex;flex-direction:column;gap:3px;font-size:11px;
  color:var(--text-muted);flex:1 1 130px}
.calc__form input{background:var(--bg);border:1px solid var(--border);border-radius:5px;
  color:var(--text);font-family:var(--font-mono);font-size:16px;padding:9px;min-width:0}
.calc__form input:focus{outline:none;border-color:var(--accent)}
.calc__actions{display:flex;align-items:center;gap:12px;margin-top:12px;flex-wrap:wrap}
.calc__actions button{background:var(--accent);border:none;color:#10131a;font-weight:700;
  border-radius:6px;padding:10px 22px;font-size:14px;cursor:pointer}
.calc__hint{font-size:11px;color:var(--text-muted)}
.calc__total{display:flex;align-items:baseline;gap:12px;flex-wrap:wrap;
  font-size:15px;font-weight:700;margin-bottom:10px}
.pos .posform__remove{margin-top:8px;width:100%}
.score{margin:8px 0;padding:8px 10px;border-radius:6px;background:var(--bg);
  border:1px solid var(--border)}
.score__head{margin:0 0 5px;font-size:12px;font-weight:700}
.score--full{border-color:var(--accent)}
.score--full .score__head{color:var(--accent)}
.score--ok .score__head{color:var(--text)}
.score--low{border-color:rgba(229,72,77,.4)}
.score--low .score__head{color:var(--bull)}
.score__lines{border-top:1px solid var(--border);margin-top:4px;padding-top:4px}
.proj__pct{color:var(--text-muted);font-size:10px}
.proj__day{margin-top:6px;padding-top:6px;border-top:1px solid var(--border)}
.proj__sub{margin:0 0 3px;font-size:10px;color:var(--text-muted)}
.price-status{margin:0 0 10px;padding:7px 10px;border-radius:6px;font-size:11px;
  line-height:1.5;background:var(--panel-alt);border:1px solid var(--border);
  color:var(--text-muted)}
.price-status--live{border-color:var(--accent);color:var(--text)}
.stale-banner{padding:10px 16px;background:rgba(232,163,61,.15);
  border-bottom:1px solid var(--warn);color:var(--warn);font-size:12px;line-height:1.6}
.stale-banner button{margin-left:10px;background:var(--warn);border:none;color:#10131a;
  font-weight:700;border-radius:5px;padding:6px 14px;font-size:12px;cursor:pointer}
"""
