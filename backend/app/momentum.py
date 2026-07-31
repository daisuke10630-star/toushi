"""
モメンタム（過去の上昇率）による銘柄選定。

■ なぜこの方式なのか
移動平均・RSI・ボリンジャーバンド・ブレイクアウトといったテクニカル指標は、
10年検証でどれも単純保有（全銘柄を等ウェイトで持ち続ける）に勝てなかった。
唯一、**指標を使わず「過去に上がっている銘柄をそのまま買って持つ」** 方法だけが
一貫して上回った。

■ 検証結果（288銘柄・10年・往復コスト0.1%込み・上位10銘柄）
  保有120日 / 6か月上昇率で選択
    前半7年 +13.61%（単純保有比 +8.36%）
    後半3年 +41.72%（単純保有比 +27.96%）
  対照群（ランダムに10銘柄）
    後半3年  +9.58%（単純保有比 -4.19%）… 不合格
ランダムが負けている点が重要。上昇相場だから勝てたのではなく、
選び方そのものが効いている。

■ 効かなかった選び方
  - プラスだった割合が高い順 … 保有60日以降は不合格
  - 値動きが小さい順（低ボラ）… 全条件で不合格

■ 限界
  - 個別銘柄では大きく外れる。分散して初めて成立する方式。
  - 後半3年は日本株が異常に強い期間。この水準が続く前提は危険。
  - 「なぜ上がるか」は説明しない。上がっているから買う、という方式。
"""
from dataclasses import dataclass, field

import pandas as pd

# 上昇率を測る期間（営業日）。120日≒6か月。
LOOKBACK_DAYS = 120
# 想定保有期間（営業日）。120日≒6か月。検証でこの長さが最も優位だった。
HOLD_DAYS = 120

# 銘柄数の検証（実際に使う551銘柄・保有120日・6か月上昇率・株価100円以上）
#   ベンチ  前半+5.57% 後半+12.52% 最悪期-10.3%
#   5銘柄  前半+7.60% 後半+21.73% 最悪期-31.9% 勝率67%  … 集中しすぎ。落ち込みが3倍
#  10銘柄  前半+9.71% 後半+37.23% 最悪期-18.4% 勝率78%  … 採用（全項目で5銘柄を上回る）
#  15銘柄  前半+9.25% 後半+37.39% 最悪期-19.3% 勝率72%
#  30銘柄  前半+7.76% 後半+24.27% 最悪期-20.0% 勝率72%  … 分散しすぎて優位が薄れる
PICK_COUNT = 10

# 低位株の除外。48円のような銘柄は値動きの刻みが粗く、上場廃止リスクも高い。
# 下限を0/100/300/500/1000円で検証し、100円が最良だった（500円以上は不合格）。
MIN_PRICE = 100

# 検証で得られた実績（表示用。将来の保証ではない）
TRACK_RECORD = {
    "前半7年": {"strategy": 9.71, "benchmark": 5.57},
    "後半3年": {"strategy": 37.23, "benchmark": 12.52},
    "worst": -18.4,
    "win_rate": 78,
}


@dataclass
class MomentumPick:
    code: str
    name: str = ""
    sector: str = ""
    price: float = 0.0
    change_pct: float = 0.0
    # 選定の根拠
    momentum_pct: float = 0.0      # 過去6か月の上昇率
    rank: int = 0
    # 保有期間ごとの過去分布（この銘柄自身の実績）
    projection: dict = field(default_factory=dict)
    # 想定する損切り水準（ATRベース。トレーリングではなく単純保有の目安）
    atr: float | None = None
    suggested_stop: float | None = None


def rank_stocks(
    frames: dict[str, pd.DataFrame],
    lookback: int = LOOKBACK_DAYS,
    min_price: float = MIN_PRICE,
) -> list[tuple[str, float]]:
    """過去 lookback 日の上昇率が高い順に並べる。

    frames: {銘柄コード: date/close を持つDataFrame}
    戻り値: [(コード, 上昇率%), ...] 上昇率の高い順
    株価が min_price 未満の銘柄は除外する（検証で100円下限が最良）。
    """
    scores: list[tuple[str, float]] = []
    for code, df in frames.items():
        if df is None or len(df) < lookback + 5:
            continue
        try:
            now = float(df["close"].iloc[-1])
            past = float(df["close"].iloc[-(lookback + 1)])
            if past <= 0 or now < min_price:
                continue
            scores.append((code, (now / past - 1) * 100))
        except Exception:
            continue
    scores.sort(key=lambda x: -x[1])
    return scores


def describe_hold(days: int) -> str:
    months = days / 21
    if months >= 11:
        return f"{days}営業日（約1年）"
    return f"{days}営業日（約{months:.0f}か月）"
