# スマホから見られるようにする（完全無料）

毎晩ひとりでに分析が走り、スマホのブラウザで結果を見られるようにする手順です。
**サーバー代はかかりません。自分のPCを起動しておく必要もありません。**

## 仕組み

```
GitHub Actions（毎朝6:10 JST・寄り付き前・無料）
   → 246銘柄を分析して docs/index.html を生成
   → GitHub Pages に公開（無料）
   → スマホのブラウザでURLを開くだけ
```

レンタルサーバーのような「スリープからの復帰待ち」がありません。
静的なHTMLを置くだけなので、開いた瞬間に表示されます。

## ⚠ 先に読んでください：公開範囲について

GitHub Pages を無料で使うには**リポジトリを公開（public）にする必要があります**
（非公開リポジトリでのPages公開は有料プランが必要）。

そのため、**保有ポジションは公開ページに載せません。**
`.github/workflows/daily-report.yml` は `--with-positions` を付けずに実行しており、
公開されるのは「どの銘柄にシグナルが出ているか」という市場分析だけです。
これは誰でも同じデータから計算できる公開情報なので、公開しても問題ありません。

**保有銘柄・取得単価・株数を公開したくない場合は、この設定を変えないでください。**
保有ポジションは自分のPCのアプリ（http://localhost:5173）で確認してください。

## 手順

### 1. GitHubにリポジトリを作る

GitHub にログインし、新しいリポジトリを作成します（Public を選択）。

### 2. このフォルダをアップロードする

```bash
cd C:\Users\daisuke\Desktop\stock-analyzer
git init
git add .
git commit -m "初回コミット"
git branch -M main
git remote add origin https://github.com/<あなたのユーザー名>/<リポジトリ名>.git
git push -u origin main
```

`positions.json` は `.gitignore` に入れてあるのでアップロードされません。

### 3. GitHub Pages を有効にする

リポジトリの **Settings → Pages** を開き、
**Source** を「**GitHub Actions**」に変更します。

### 4. 動作確認

**Actions** タブ → 「日次レポートの生成と公開」→ **Run workflow** で手動実行します。
5〜10分で完了し、次のURLで公開されます。

```
https://<あなたのユーザー名>.github.io/<リポジトリ名>/
```

このURLをスマホのホーム画面に追加しておくと、アプリのように使えます。

### 5. あとは自動

以降は**平日の毎朝6:10（日本時間・寄り付き前）に自動更新**されます。
実行時刻を変えたい場合は `.github/workflows/daily-report.yml` の `cron` を編集してください
（UTC表記なので、日本時間から9時間引いた値を書きます）。

## よくある詰まりどころ

| 症状 | 原因と対処 |
|---|---|
| Actions が動かない | Settings → Actions → General で「Allow all actions」になっているか確認 |
| Pages が404 | Settings → Pages の Source が「GitHub Actions」になっているか確認 |
| 60日以上放置で停止 | GitHubの仕様。Actionsタブから一度手動実行すれば再開します |
| データが古い | 祝日はデータが更新されません。データ基準日はページ上部に出ています |

## ローカル版との使い分け

| | ローカル版（PC） | 公開版（スマホ） |
|---|---|---|
| 保有ポジション | ○ 表示・編集できる | × 載せない |
| 銘柄の詳細・AI信頼度 | ○ | △ 上位10銘柄のみ |
| 価格ライン比較 | ○ | × |
| データの鮮度 | 開いた時点 | 前夜の分析時点 |
| 起動 | サーバー2つを起動 | URLを開くだけ |

ローカル版の起動：

```
cd C:\Users\daisuke\Desktop\stock-analyzer\backend; .\venv\Scripts\Activate.ps1; uvicorn app.main:app --reload --port 8000
```
```
cd C:\Users\daisuke\Desktop\stock-analyzer\frontend; npm run dev
```

## 手元でレポートを作る場合

```bash
python scripts/build_site.py --with-positions
```

`docs/index.html` が生成されます。`--with-positions` を付けると保有ポジションも
含まれるので、**このファイルは公開しないでください**。
