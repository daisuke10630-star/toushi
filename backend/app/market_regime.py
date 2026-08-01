"""
市場全体の急落を検知する。

■ 位置づけ
主力はモメンタム戦略（momentum.py）。ここで判定する「市場急落」は
新規エントリーを置き換えるものではなく、**強調する補助シグナル**として使う。

■ 検証結果（551銘柄・10年・往復コスト0.1%込み）
日経平均が急落した翌日にモメンタム上位10銘柄を買うと、常時のモメンタム単独より
効くケースがあった（90日保有・前半+8.97% vs 通常+4.13%）。
ただし：
  - 10年で該当は124日、90日以上の余白がある機会は109回のみ（年10回程度）
  - 後半3年は通常のモメンタム単独のほうが優れていた（+24.71% vs +6.99%）
  - サンプルが薄く（後半31〜42件）、生存バイアスの影響も相対的に大きい
このため「常時の主力を置き換える」のではなく、
「急落直後はモメンタム候補への注目度を上げる」という補助的な使い方に留める。

■ 閾値
過去10年の日経平均の日次騰落率で、下位5%点を「急落」とする。
"""
from dataclasses import dataclass

import pandas as pd
import yfinance as yf

CRASH_QUANTILE = 0.05
NIKKEI_SYMBOL = "^N225"


@dataclass
class MarketRegime:
    is_crash_day: bool
    change_pct: float | None
    threshold_pct: float | None
    note: str


def fetch_nikkei(period: str = "10y") -> pd.DataFrame | None:
    try:
        df = yf.Ticker(NIKKEI_SYMBOL).history(period=period, interval="1d", auto_adjust=True)
        if df.empty:
            return None
        return df
    except Exception:
        return None


def evaluate(df: pd.DataFrame | None, close_col: str = "Close") -> MarketRegime:
    """直近の市場指数の値動きから、今日が「急落日」だったかを判定する。

    df は yfinance の履歴（列名 "Close"）、または本アプリの正規化済み
    DataFrame（列名 "close"）のどちらでも受け付ける（close_col で指定）。
    """
    if df is None or len(df) < 260:
        return MarketRegime(False, None, None, "市場指数のデータを取得できませんでした")

    ret = df[close_col].pct_change() * 100
    threshold = float(ret.quantile(CRASH_QUANTILE))
    latest = float(ret.iloc[-1])
    is_crash = latest < threshold

    if is_crash:
        note = (
            f"本日、市場が{latest:+.2f}%と急落しました"
            f"（過去の下位5%水準={threshold:.2f}%以下）。"
            f"過去の検証では、こうした日の翌営業日にモメンタム上位銘柄を"
            f"買うと成績が上振れする傾向がありました（年10回程度の低頻度・"
            f"サンプルが薄いため補助的な参考情報です）"
        )
    else:
        note = f"本日の市場騰落率 {latest:+.2f}%（急落の目安: {threshold:.2f}%以下）"

    return MarketRegime(is_crash, round(latest, 2), round(threshold, 2), note)
