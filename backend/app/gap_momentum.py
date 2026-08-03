"""決算モメンタム（急騰継続）投資法・近似検証版（主力）。

■ なぜ近似なのか（正直な限界）
本来は「決算発表日」「夜間PTSでの急騰」を特定して検証すべきだが、
このアプリのデータ源（yfinance）には決算カレンダーもPTS（夜間取引）データもない。
そのため代理指標として **前日終値比+5%以上の寄り付き窓開け** を
急騰イベントとみなす。決算以外の材料（增資報道・提携等）による
窓開けも混ざる点に注意。

■ 検証結果（241銘柄・10年・片道0.3%のコスト＋実現益のみ課税・保有60営業日）
同一銘柄が保有期間中に複数回イベントを出すケースは間引き（真に独立な
サンプルのみに絞った）。同銘柄のランダムな日から入った場合との比較：
  全期間    イベント後average +6.05%  同銘柄ランダム日 +1.97%（差 +4.08%）
  前半70%   +5.16%  / +2.21%（差 +2.94%）
  後半30%   +8.39%  / +1.89%（差 +6.51%）
+8%以上に閾値を上げるとサンプルは減るが上乗せ幅は拡大する傾向。

■ 限界
  - イベント駆動型のため、該当銘柄がない日は資金の置き場がない
    （このアプリではETF×先物タイミングのポジションで待機する設計）。
  - 「同じ銘柄が短期間に何度も急騰する」場合はサンプル間の独立性が
    完全ではない（間引いてもなお、業種や地合いの影響は残る）。
  - 決算以外の材料による窓開けも含むため、純粋な「決算モメンタム」より
    やや緩い代理指標である。
"""
from dataclasses import dataclass

import pandas as pd

GAP_THRESHOLD = 0.05   # 前日終値比+5%以上の窓開けをイベントとみなす
HOLD_DAYS = 60         # 保有期間（検証で最も効いた水準）
MIN_PRICE = 100        # モメンタム戦略と同じ、低位株の除外

# 表示件数の上限（実務上の理由。統計的な優位性はイベント単位で検証したもので、
# 「同時に何十銘柄も保有できるか」は検証していない。決算集中期は該当が
# 90件を超えることもあり、個人が同時に管理できる数を超えるため、
# 直近のイベントを優先して表示件数を絞る）。
MAX_DISPLAY = 20

TRACK_RECORD = {
    "全期間": {"strategy": 6.05, "benchmark": 1.97},
    "前半70%": {"strategy": 5.16, "benchmark": 2.21},
    "後半30%": {"strategy": 8.39, "benchmark": 1.89},
}


@dataclass
class GapPick:
    code: str
    name: str = ""
    sector: str = ""
    block: str = ""
    entry_price: float = 0.0
    entry_date: str = ""
    gap_pct: float = 0.0       # 発生時の窓開け幅
    current_price: float = 0.0
    change_pct: float = 0.0    # 前日比（当日の値動き）
    return_pct: float = 0.0    # エントリーからの騰落率
    days_held: int = 0
    days_remaining: int = 0


def scan(frames: dict[str, pd.DataFrame]) -> list[GapPick]:
    """直近 HOLD_DAYS 営業日以内に急騰イベントが起きた銘柄を探す。

    同一銘柄で複数回イベントが起きていても、保有期間中に再エントリーは
    しない（バックテストの重複除去ルールと合わせるため、直近の
    "有効な" イベントは window内で最初に見つかったものを採用する）。
    """
    picks: list[GapPick] = []
    for code, df in frames.items():
        if df is None or len(df) < HOLD_DAYS + 2:
            continue
        close = df["close"].values
        open_ = df["open"].values
        n = len(df)
        window_start = max(1, n - HOLD_DAYS)

        found_idx = None
        for i in range(window_start, n):
            if close[i - 1] < MIN_PRICE:
                continue
            gap = open_[i] / close[i - 1] - 1
            if gap >= GAP_THRESHOLD:
                found_idx = i
                break
        if found_idx is None:
            continue

        days_held = (n - 1) - found_idx
        entry_price = float(close[found_idx])
        current_price = float(close[-1])
        prev_price = float(close[-2]) if n >= 2 else current_price
        gap_pct = (open_[found_idx] / close[found_idx - 1] - 1) * 100

        picks.append(GapPick(
            code=code,
            entry_price=round(entry_price, 1),
            entry_date=df["date"].iloc[found_idx].strftime("%Y-%m-%d"),
            gap_pct=round(gap_pct, 2),
            current_price=round(current_price, 1),
            change_pct=round((current_price / prev_price - 1) * 100, 2) if prev_price else 0.0,
            return_pct=round((current_price / entry_price - 1) * 100, 2),
            days_held=days_held,
            days_remaining=HOLD_DAYS - days_held,
        ))

    picks.sort(key=lambda p: p.days_held)
    return picks[:MAX_DISPLAY]
