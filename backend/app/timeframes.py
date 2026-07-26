"""
時間軸（タイムフレーム）ごとの分析パラメータ定義。

■ なぜ日足のパラメータをそのまま分足に流用できないか
日足の MA 5/25/80/120 は「約1週間 / 約1か月 / 約4か月 / 約半年」という
"期間の意味" を持たせた設定です。これを1分足にそのまま当てると
「5分 / 25分 / 80分 / 120分」となり、想定していた時間感覚と噛み合いません。
RSI も同様で、1分足の RSI(9) は数分間の値動きしか見ないため、
70/30 のしきい値にほぼ常時タッチしてノイズだらけになります。

■ 再設計の方針
東証の1日の立会時間は 9:00-11:30 と 12:30-15:30 の計 330分です。
これを基準に「直近の勢い / 数十分 / 半日 / 1〜2営業日」という
日足と同じ役割分担を各時間軸で再現しました。
RSI は時間軸が短いほど振れ幅が大きくなるため、期間を延ばしつつ
しきい値も外側（80/20）に広げています。

■ 正直な限界
以下の数値は「日本のデイトレードで慣習的に使われる設定」を
上記の考え方で整えたものであり、最適化も統計的検証もしていません。
実際の成績は AI信頼度（バックテスト勝率）で確認してください。
数値はすべてこのファイルで調整できます。
"""
from dataclasses import dataclass, field

# 東証の1営業日あたりの立会分数（前場150分 + 後場180分）
SESSION_MINUTES = 330


@dataclass(frozen=True)
class TimeframeParams:
    key: str
    label: str
    # --- データ取得 ---
    yf_interval: str
    yf_period: str
    bars: int  # 分析に使うバー数
    date_format: str
    # --- 移動平均 ---
    ma_periods: dict[str, int]
    ma_labels: dict[str, str]
    # --- RSI ---
    rsi_short: int
    rsi_long: int
    rsi_overheat: float
    rsi_oversold: float
    # --- ボリンジャーバンド ---
    bb_period: int
    # --- ローソク足・パターン ---
    candle_avg_window: int
    pattern_lookback: int
    extrema_order: int
    # --- バックテスト ---
    forward_bars: int
    forward_label: str
    # ATR（平均真の値幅）の期間。ボラティリティ連動の損切り幅に使う
    atr_period: int = 14
    notes: list[str] = field(default_factory=list)

    @property
    def is_intraday(self) -> bool:
        return self.yf_interval.endswith("m")


DAILY = TimeframeParams(
    key="1d",
    label="日足",
    yf_interval="1d",
    yf_period="2y",
    bars=250,
    date_format="%Y-%m-%d",
    ma_periods={"MA1": 5, "MA2": 25, "MA3": 80, "MA4": 120},
    ma_labels={"MA1": "5日", "MA2": "25日", "MA3": "80日", "MA4": "120日"},
    rsi_short=9,
    rsi_long=14,
    rsi_overheat=70,
    rsi_oversold=30,
    bb_period=25,
    candle_avg_window=20,
    pattern_lookback=60,
    extrema_order=3,
    # 保有期間。3/5/7/10日を「1日あたりのリターン」で比較し、3日を採用。
    # 上位8件すべてが3日で占められ、勝率も55〜63%と長期保有（約50%）を上回った。
    # 実際の平均保有は2.3日で、多くは損切りか利確で早く決着する。
    #
    # 1トレードあたりの期待値ではなく1日あたりで測るのが重要。
    # 資金は一度に1銘柄しか持てないため、同じ日数でより多く回転できるほうが有利。
    # トレーリングストップは「伸びる銘柄を伸ばす」方式なので、保有上限を短くすると
    # 効果が消える。20/40/60/90/120日で検証し、前半・後半とも買い持ちを上回った
    # 90日を採用（前半は保有を延ばすほど単調に改善した）。
    forward_bars=90,
    forward_label="90営業日（トレーリングで早く手仕舞う場合も多い）",
    notes=["従来からの設定。約1週間/1か月/4か月/半年の役割分担"],
)

# 分足（5分足・1分足）は 2026-07-26 に削除しました。
# 理由：バックテストの結果、どの損切り幅でも期待値が実質ゼロ（-0.00%〜-0.25%）で、
# 手数料を引くとマイナス確定だったため。原因は損切り幅ではなく、分足では
# 利確目標（BB+2σ）が現値から0.2〜1%しか離れておらず、往復コスト0.05〜0.1%が
# 利益の10〜50%を食う構造にあります。
# 復活させる場合は、上と同じ TimeframeParams を定義して ALL に足すだけです。

ALL: dict[str, TimeframeParams] = {tf.key: tf for tf in (DAILY,)}
DEFAULT_KEY = DAILY.key


def get(key: str | None) -> TimeframeParams:
    """タイムフレームキーから設定を取得する。未知のキーはValueError。"""
    if not key:
        return ALL[DEFAULT_KEY]
    tf = ALL.get(key.strip().lower())
    if tf is None:
        raise ValueError(
            f"未対応のタイムフレーム '{key}' です。次のいずれかを指定してください: {', '.join(ALL)}"
        )
    return tf
