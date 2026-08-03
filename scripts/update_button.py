"""
「最新データに更新」ボタン（静的ページに埋め込むHTML/CSS/JS）。

■ 設計方針
GitHub Pages はサーバーを持てないので、ボタンを押したときの実処理は
GitHub Actions のワークフロー（daily-report.yml）を workflow_dispatch で
起動することで実現する。起動には GitHub API 呼び出しの認証が要るため、
ユーザー自身が発行した Fine-grained PAT（このリポジトリの
Actions:write 権限のみ）を、calculator.py の取得単価と同じ方式で
**この端末の localStorage にのみ保存する。GitHubやどこか別の場所には送らない**
（トークン自体は GitHub API への認証にしか使わない＝送信先は GitHub 本体のみ）。

■ 注意点（正直に書く）
- localStorage は daisuke10630-star.github.io オリジン単位で共有されるため、
  もし将来このオリジン配下の別ページにXSSがあればトークンを読まれ得る。
  ただし本アプリはユーザー生成コンテンツを扱わずXSS面は小さい。
- ワークフロー起動後の反映は数分かかる（既存のバッチと同じ処理が走るだけで、
  瞬時にはならない）。concurrency設定により連打しても新しい方が優先されるだけ。
"""

REPO = "daisuke10630-star/toushi"
WORKFLOW_FILE = "daily-report.yml"


def build() -> str:
    return """
<section class="report-section" id="update-trigger">
  <h2>最新データに更新</h2>
  <p class="report-lead">
    ボタンを押すと、GitHub上でその場で分析バッチ（スクレイピング〜再計算）を起動します。
    <strong>反映まで3〜9分ほどかかります。</strong>瞬時には切り替わりません。
    初回だけ、このリポジトリ専用の限定トークンの入力が必要です
    （<strong>この端末にのみ保存され、GitHub以外には送信されません</strong>）。
  </p>
  <div class="upd__box">
    <button id="upd-btn" type="button">更新をリクエスト</button>
    <button id="upd-token-btn" type="button" class="upd__ghost">トークンを設定／変更</button>
    <span id="upd-status" class="upd__status"></span>
  </div>
  <p class="upd__help" id="upd-help"></p>
</section>

<script>
(function () {
  var REPO = \"""" + REPO + """\";
  var WORKFLOW = \"""" + WORKFLOW_FILE + """\";
  var KEY = 'stock-analyzer-gh-pat-v1';
  var $ = function (id) { return document.getElementById(id); };
  var COOLDOWN_MS = 2 * 60 * 1000;
  var lastFireAt = 0;

  function getToken() { return localStorage.getItem(KEY) || ''; }
  function setToken(t) { if (t) localStorage.setItem(KEY, t); else localStorage.removeItem(KEY); }

  function helpText() {
    return 'トークンの作り方: GitHubの Settings > Developer settings > ' +
      'Fine-grained personal access tokens で新規作成。' +
      'Repository access は「Only select repositories」で ' + REPO + ' のみを選択、' +
      'Permissions は「Actions: Read and write」だけを付与してください。' +
      '他の権限は不要です。有効期限は短め（例:90日）を推奨します。';
  }

  function promptForToken() {
    var t = window.prompt(
      'GitHubのFine-grained PAT（Actions:write権限・このリポジトリのみ）を貼り付けてください。\\n' +
      helpText(), '');
    if (t && t.trim()) {
      setToken(t.trim());
      $('upd-status').textContent = 'トークンを保存しました（この端末のみ）';
      return t.trim();
    }
    return '';
  }

  function fire() {
    var token = getToken();
    if (!token) {
      token = promptForToken();
      if (!token) return;
    }
    var now = Date.now();
    if (now - lastFireAt < COOLDOWN_MS) {
      var waitSec = Math.ceil((COOLDOWN_MS - (now - lastFireAt)) / 1000);
      $('upd-status').textContent = '連続実行は避けてください（あと' + waitSec + '秒お待ちください）';
      return;
    }
    $('upd-status').textContent = 'リクエスト送信中…';
    fetch('https://api.github.com/repos/' + REPO + '/actions/workflows/' + WORKFLOW +
      '/dispatches', {
      method: 'POST',
      headers: {
        'Authorization': 'Bearer ' + token,
        'Accept': 'application/vnd.github+json',
        'Content-Type': 'application/json'
      },
      body: JSON.stringify({ ref: 'main' })
    }).then(function (r) {
      if (r.status === 204) {
        lastFireAt = now;
        $('upd-status').textContent =
          '更新をリクエストしました。3〜9分ほどで反映されます（このページの再読み込みが必要です）。';
      } else if (r.status === 401 || r.status === 403) {
        setToken('');
        $('upd-status').textContent =
          'トークンが無効か権限不足です（' + r.status + '）。トークンを再設定してください。';
      } else if (r.status === 404) {
        $('upd-status').textContent =
          'ワークフローが見つかりません（' + r.status + '）。トークンのリポジトリ選択を確認してください。';
      } else {
        $('upd-status').textContent = '失敗しました（HTTP ' + r.status + '）。';
      }
    }).catch(function () {
      $('upd-status').textContent = '通信に失敗しました。回線を確認して再度お試しください。';
    });
  }

  $('upd-btn').onclick = fire;
  $('upd-token-btn').onclick = function () {
    setToken('');
    promptForToken();
  };
  $('upd-help').textContent = helpText();
})();
</script>"""


CSS = """
.upd__box{display:flex;align-items:center;gap:10px;flex-wrap:wrap;margin:10px 0 6px}
.upd__box button{border:none;border-radius:6px;padding:10px 18px;font-size:13px;
  font-weight:700;cursor:pointer}
#upd-btn{background:var(--accent);color:#10131a}
.upd__ghost{background:transparent!important;color:var(--text-muted)!important;
  border:1px solid var(--border)!important;font-weight:500!important}
.upd__status{font-size:11px;color:var(--text-muted)}
.upd__help{margin:4px 0 0;font-size:10px;line-height:1.6;color:var(--text-muted)}
"""
