import os
from dotenv import load_dotenv

load_dotenv()

# データ取得元: "yfinance"（既定・APIキー不要）または "jquants"
DATA_SOURCE = os.getenv("DATA_SOURCE", "yfinance")

# J-Quants API V2 はAPIキー方式。V1のリフレッシュトークンは廃止されました。
# DATA_SOURCE=jquants のときのみ使用します。
JQUANTS_API_KEY = os.getenv("JQUANTS_API_KEY", "")
# 旧設定が残っている場合に分かりやすいエラーを出すためだけに読み込む（認証には使いません）
JQUANTS_REFRESH_TOKEN = os.getenv("JQUANTS_REFRESH_TOKEN", "")
# CORS許可オリジン。カンマ区切りで複数指定できる（デプロイ時にフロントのURLを追加する）
ALLOWED_ORIGIN = os.getenv("ALLOWED_ORIGIN", "http://localhost:5173")
ALLOWED_ORIGINS = [o.strip() for o in ALLOWED_ORIGIN.split(",") if o.strip()]

# 保有ポジションの書き換えに必要なトークン。
# 未設定ならチェックしない（ローカル利用を想定）。
# 公開先にデプロイする場合は必ず設定してください。URLを知った第三者に
# 保有情報を書き換えられてしまいます。
WRITE_TOKEN = os.getenv("APP_WRITE_TOKEN", "")

# ウォッチリスト：証券コードと表示名
# ここに銘柄を追加/削除すればダッシュボードに反映されます
# 2026-07-26 のスクリーニング結果（246銘柄をスキャン）。
# ★は「強気条件の揃い具合」であり上昇確率ではありません。推奨銘柄ではありません。
# /api/screen で随時更新できます。
WATCHLIST = [
    # === 保有銘柄（スクリーニング結果とは無関係に、常に監視する枠） ===
    {"code": "8005", "name": "スクロール"},
    {"code": "8136", "name": "サンリオ"},
    {"code": "6480", "name": "日本トムソン"},
    # === A群：単純保有を上回った15銘柄（上回り幅の大きい順） ===
    # この足切りを通過したのは246銘柄中15銘柄だけでした。
    {"code": "7013", "name": "IHI"},
    {"code": "4661", "name": "オリエンタルランド"},
    {"code": "6861", "name": "キーエンス"},
    {"code": "6326", "name": "クボタ"},
    {"code": "4519", "name": "中外製薬"},
    {"code": "6302", "name": "住友重機械工業"},
    {"code": "8031", "name": "三井物産"},
    {"code": "7012", "name": "川崎重工業"},
    {"code": "6367", "name": "ダイキン工業"},
    {"code": "9434", "name": "ソフトバンク"},
    {"code": "9602", "name": "東宝"},
    {"code": "1802", "name": "大林組"},
    {"code": "6305", "name": "日立建機"},
    {"code": "7733", "name": "オリンパス"},
    {"code": "9432", "name": "NTT"},
    # === B群：★は高いが単純保有には負けている銘柄（★順） ===
    # 監視対象を30銘柄に広げるための補充枠です。ダッシュボードの
    # 「対照実験」欄が赤枠になるので、A群との違いは一目で分かります。
    {"code": "6532", "name": "ベイカレント"},
    {"code": "4578", "name": "大塚ホールディングス"},
    {"code": "8766", "name": "東京海上ホールディングス"},
    {"code": "1721", "name": "コムシスホールディングス"},
    {"code": "2871", "name": "ニチレイ"},
    {"code": "2502", "name": "アサヒグループホールディングス"},
    {"code": "9301", "name": "三菱倉庫"},
    {"code": "9064", "name": "ヤマトホールディングス"},
    {"code": "6841", "name": "横河電機"},
    {"code": "8331", "name": "千葉銀行"},
    {"code": "8306", "name": "三菱UFJフィナンシャルG"},
    {"code": "4751", "name": "サイバーエージェント"},
    {"code": "8316", "name": "三井住友フィナンシャルグループ"},
    {"code": "6971", "name": "京セラ"},
    {"code": "5108", "name": "ブリヂストン"},
]

# 移動平均・RSI・ボリンジャーバンドの期間は時間軸ごとに異なるため
# timeframes.py に移動しました（日足=DAILY, 5分足=FIVE_MIN, 1分足=ONE_MIN）。

# 損切りライン：購入価格（エントリー目安）から何%下に置くか。
#
# 以下の値は 2026-07-26 に optimize.py で実測した結果（25銘柄・★5以上・
# 前半70%と後半30%に分けて検証）から、期待値が最大の水準を採用したものです。
# 元の8%固定は全時間軸で最悪水準でした（日足★5で期待値 -1.35%）。
#
# 2026-07-26 の2次元スイープ（40銘柄・損切り10通り×利確9通り・コスト込み）の結果、
# 8%が最良でした。当初の8%は損切り幅としては妥当で、問題は利確目標の側にありました。
STOP_LOSS_PCT_BY_TIMEFRAME = {
    "1d": 0.08,
}
# 未知の時間軸に対するフォールバック
STOP_LOSS_PCT_DEFAULT = 0.08

# --- 価格以外の情報を使う特徴量 ---
# 効果を検証できるよう個別にON/OFFできます（optimize時にA/B比較する）。
# 10年検証の結果（30銘柄・★4/★5 × 前半/後半 の4通りで判定）：
#   出来高      … 0/4 通過。効果なしのため OFF
#   市場相対強弱 … 1/4 通過。ほぼ効かないため OFF
#   業種相対強弱 … 4/4 通過。唯一一貫して効いたため ON
# 計算自体は常に行うので、フラグを True にすればいつでも復活できます。
USE_VOLUME_FEATURE = False
# 出来高が平常時（20日平均）の何倍で「急増」とみなすか
VOLUME_SURGE_RATIO = 2.0
# 出来高が平常時の何倍を下回ったら「閑散」として減点するか
VOLUME_QUIET_RATIO = 0.5

USE_RELATIVE_STRENGTH = False
# 市場（TOPIX）に対する20日超過リターンが何%を超えたら加点/減点するか
RS_MARKET_STRONG = 3.0
RS_MARKET_WEAK = -3.0

USE_SECTOR_STRENGTH = True
# 同業種平均に対する20日超過リターンのしきい値
RS_SECTOR_STRONG = 3.0
RS_SECTOR_WEAK = -3.0

# 決算発表が何営業日以内なら警告を出すか（スコアには influence させない）
EARNINGS_WARN_DAYS = 10

# --- エントリー方式 ---
# "pullback" … MA1（5日線）への押し目を指値で待つ（旧版の方式）
# "market"   … 待たずに翌営業日の始値で成行
# "breakout" … 直近高値を上抜けたら逆指値で買う（順張り）
#
# 2026-07-26 の検証で「押し目を待つ」方式が価値を破壊していることが判明。
# 高値ブレイクに変更したところ、初めて単純保有（ベンチマーク）を上回りました。
ENTRY_MODE = "breakout"

# ブレイクアウト方式で「直近高値」を何本さかのぼって取るか
BREAKOUT_LOOKBACK = 20

# --- 利確目標 ---
# 損切り幅の何倍の値幅を利確目標に置くか（リスクリワード比）。
# 従来は BB+2σ を利確目標①にしていたが、スイープの結果こちらが明確に優った。
# BB+2σ は銘柄・局面によって現値との距離がバラバラで、損切り幅と釣り合わないため。
# 短期・近い利確に絞った140通りのスイープ（10年・30銘柄・1日あたりのリターンで判定）で
# リスクリワード1:1.5 が最良でした。従来の1:3は利確が遠すぎて取りこぼしていました。
TAKE_PROFIT_R_MULTIPLE_1 = 1.5
TAKE_PROFIT_R_MULTIPLE_2 = 2.0

# --- 損切りの方式 ---
# "atr" … ATR（平均真の値幅）の倍数。銘柄のボラティリティに応じて幅が変わる
# "pct" … 購入価格の固定%（STOP_LOSS_PCT_BY_TIMEFRAME）
# "trail_atr" … 高値更新に合わせて損切りを切り上げる（トレーリング）
# "atr" / "pct" … 約定価格を基準に固定
#
# 実際に資金を回したシミュレーション（同時保有3銘柄）で、トレーリングATR×2が
# 唯一、前半7年・後半3年の両方で買い持ちを上回りました。
#   トレーリングATR×2 / 90日 : 前半 +17.5%（買い持ち比 +6.6%）/ 後半 +27.9%（+2.7%）
#   固定ATR×2 / 3日          : 前半  +9.9%（-1.0%）/ 後半 +19.0%（-6.1%）
# 固定式は「利益を伸ばせない」ため買い持ちに勝てませんでした。
STOP_MODE = "trail_atr"
STOP_ATR_MULTIPLE = 2.0


def stop_spec(timeframe_key: str) -> tuple:
    """backtest / optimize が使う損切り指定を返す。

    STOP_MODE をそのまま指定に反映する。ここで取りこぼすと、検証した設定と
    実際に動く設定が食い違う（過去に trail_atr を pct 8% に落としていた不具合あり）。
    """
    if STOP_MODE in ("atr", "trail_atr"):
        return (STOP_MODE, STOP_ATR_MULTIPLE)
    if STOP_MODE == "trail_pct":
        return ("trail_pct", stop_loss_pct(timeframe_key))
    return ("pct", stop_loss_pct(timeframe_key))


def uses_atr_stop() -> bool:
    """損切り幅をATRで決める設定か（固定・トレーリングの両方を含む）。"""
    return STOP_MODE in ("atr", "trail_atr")


def is_trailing() -> bool:
    return STOP_MODE.startswith("trail")

# --- 売買コスト ---
# 往復（買い＋売り）でかかるコストの合計。手数料・スプレッド・約定滑りを含む。
# バックテストの各トレードのリターンからこの値を差し引く。
# ネット証券の国内株手数料は無料プランも多いが、スプレッドと滑りは必ず発生するため
# 0にはしない。0.1%は中位の見積もりで、感度は optimize.py で確認できる。
TRADING_COST_PCT = 0.001


def stop_loss_pct(timeframe_key: str) -> float:
    return STOP_LOSS_PCT_BY_TIMEFRAME.get(timeframe_key, STOP_LOSS_PCT_DEFAULT)

# --- AI信頼度（バックテスト勝率）の設定 ---
# 勝率を表示するために最低限必要なシグナル発生回数。これ未満は「データ不足」と表示する
BACKTEST_MIN_SAMPLES = 10

# --- 過熱時の除外 ---
# RSIが過熱圏、または終値が+2σを超えている銘柄を買い候補から外す。
# 高値ブレイクで買う設計上、放っておくと「買われすぎ」を掴みやすい。
# 10年検証（30銘柄・同時保有3銘柄）でこの除外を入れると成績が改善した：
#   除外なし 前半+24.1%（買い持ち比+13.0%）/ 後半+30.9%（+5.3%）
#   除外あり 前半+27.3%（買い持ち比+16.3%）/ 後半+32.2%（+6.6%）
EXCLUDE_OVERBOUGHT = True

# --- 日次レポートの設定 ---
# スマホ版に載せる買い候補の件数。多すぎると比較が難しくなるため絞る。
REPORT_TOP_N = 3

# --- スクリーナーの設定 ---
# 抽出する★の下限
SCREEN_MIN_STARS = 4
# 一度にyfinanceへ問い合わせる銘柄数
SCREEN_BATCH_SIZE = 40
# True にすると「その銘柄で単純保有を上回っている」ものだけを抽出する。
# ★だけでは銘柄を絞れない（10年検証で★に優位性がないと判明）ため既定でON。
# ただし過去の超過リターンで選ぶこと自体が過学習になり得る点に注意。
SCREEN_REQUIRE_POSITIVE_EDGE = True
# バックテストにかける候補の上限（limitの何倍まで計算するか）
SCREEN_CANDIDATE_MULTIPLIER = 3
