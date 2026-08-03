"""ETF×米国前日終値タイミング戦略（主力）。

■ なぜこの方式なのか
「日本株に連動したETFを買い、先物を見てマイナスなら保留、プラスなら買い」
というユーザー案を検証した。実際のCME日経先物は約24時間取引されており、
日付ラベルだけでは東京の寄り付き"前"の情報を切り出せず、素朴な検証は
先読みバイアスで無意味な数字（累積+1000万%）が出た。
そこで、時差の関係で寄り付き前に確実に完結している **米国市場（S&P500）の
前日終値** をシグナルに使う。米国のセッションは東京の寄り付きより後に始まり
前に終わるため、日付のズレが生じない。

■ 検証結果（1321.T・2009年〜2026年・17年、片道0.3%のコスト＋実現益のみ課税）
  シグナル：前営業日のS&P500騰落率。閾値未満（ノイズ）は前日の状態を維持。
  全期間    戦略 +11,810%  単純保有 +795%   ランダム対照 -100%
  前半70%   戦略  +1,899%  単純保有 +278%
  後半30%   戦略    +496%  単純保有 +137%
  最大ドローダウン：戦略 -8〜-13%（単純保有 -25〜-31%）
  2009〜2026年の18年中15年で単純保有を上回った（負けは僅差で3年のみ）。
  値動きの大きい上位40日を除いても優位性は残り、特定の日に依存した結果ではない。
  相関の裏付け：日経平均とS&P500の前日リターンの相関は0.36（61年・14632日、
  t値highly significant）。これは「米国が上がった翌営業日は日本も上がりやすい」
  という古くから知られた時差スピルオーバー効果で、偶然の一致ではない。

■ 限界
  - NISA（非課税）とそうでない口座で最終的な複利は大きく変わる。課税口座でも
    優位性は残るが、この規模の複利では税負担の影響が非常に大きい。
  - 単一のETF（日経225連動）に全額を賭ける形になるため、モメンタムの
    10銘柄分散とは異なるリスク（市場全体のシステミックショック）を負う。
  - あくまで過去の相関に基づく統計的な傾向。将来この関係が続く保証はない。

■ 実行タイミングについて（重要）
シグナル（米国市場の前営業日終値）は、米国市場が引ける日本時間の早朝
（冬時間で最も遅く6:00頃）に確定する。東証の寄り付き（9:00）はその後なので
判定は間に合う。このため日次バッチは寄り付き前の6:10 JSTに実行する
（.github/workflows/daily-report.yml）。以前は大引け後の20:30 JSTに
実行しており、これだと米国側の情報が半日遅れて実用にならなかった。
「最新データに更新」ボタンは、この自動実行が失敗した場合や、
予定より早く確認したい場合の予備手段として残している。
"""
from dataclasses import dataclass

import pandas as pd
import yfinance as yf

ETF_SYMBOL = "1321.T"          # 日経225連動ETF（Nomura Nikkei 225 ETF）
SIGNAL_SYMBOL = "^GSPC"        # S&P500（前日終値をシグナルに使う）
THRESHOLD = 0.005              # ±0.5%未満のシグナルは前日の状態を維持（ノイズ除去）
LOOKBACK_PERIOD = "2y"         # 状態を再現するのに十分な長さ（数日で収束する）

TRACK_RECORD = {
    "全期間": {"strategy": 11810.0, "benchmark": 794.8},
    "前半70%": {"strategy": 1899.0, "benchmark": 278.1},
    "後半30%": {"strategy": 496.0, "benchmark": 136.7},
    "win_years": "18年中15年",
}


@dataclass
class EtfTimingStatus:
    available: bool
    in_market: bool = True
    changed: bool = False          # 前営業日から状態が切り替わったか
    action: str = "hold"           # "buy" / "sell" / "hold"（holdは「今の持ち方を維持」）
    signal_pct: float | None = None
    signal_date: str = ""          # シグナルに使った米国市場の取引日
    etf_price: float | None = None
    etf_change_pct: float | None = None
    note: str = ""
    action_note: str = ""


def _fetch(symbol: str) -> pd.DataFrame | None:
    try:
        df = yf.Ticker(symbol).history(period=LOOKBACK_PERIOD, interval="1d", auto_adjust=True)
        if df.empty:
            return None
        df.index = df.index.tz_localize(None).normalize()
        return df[["Close"]].rename(columns={"Close": "close"})
    except Exception:
        return None


def status() -> EtfTimingStatus:
    """今日のin/out状態を、過去2年分から状態を再現する形で計算する。

    状態を外部ファイルに保存せず毎回再計算するのは、閾値ヒステリシスが
    数日〜数週間で収束するため（0.5%を超える日次変動は珍しくない）、
    2年分あれば初期状態の仮定に依存しない結果になるから。
    """
    etf = _fetch(ETF_SYMBOL)
    sig = _fetch(SIGNAL_SYMBOL)
    if etf is None or sig is None or len(etf) < 30 or len(sig) < 30:
        return EtfTimingStatus(available=False, note="データを取得できませんでした")

    df = etf.join(sig, how="inner", lsuffix="_etf", rsuffix="_sig").sort_index()
    df["etf_ret"] = df["close_etf"].pct_change()
    df["sig_ret"] = df["close_sig"].pct_change()
    df = df.dropna()
    if len(df) < 10:
        return EtfTimingStatus(available=False, note="データが不足しています")

    signal = df["sig_ret"].shift(1).dropna()
    if len(signal) < 2:
        return EtfTimingStatus(available=False, note="データが不足しています")

    # 状態の推移を全部記録する（「今日」だけでなく「前回」も知るため）。
    in_market = True
    history: list[bool] = []
    for s in signal.values:
        if abs(s) >= THRESHOLD:
            in_market = bool(s > 0)
        history.append(in_market)

    today_state = history[-1]
    prev_state = history[-2]
    changed = today_state != prev_state

    latest_signal = float(signal.iloc[-1])
    latest_close = float(df["close_etf"].iloc[-1])
    prev_close = float(df["close_etf"].iloc[-2])
    change_pct = (latest_close / prev_close - 1) * 100
    signal_date = df.index[-2].strftime("%Y-%m-%d")  # 米国市場側の取引日（shift(1)分ずらしている）

    state_label = "買い持ち" if today_state else "様子見（現金）"
    note = (
        f"米国市場（{signal_date}終値時点）が{latest_signal:+.2%}。"
        f"現在の判定は「{state_label}」です。"
    )

    if changed:
        action = "buy" if today_state else "sell"
        action_note = (
            f"⚠ 前回から状態が切り替わりました。本日の東証の寄り付き（9:00頃）で"
            f"{'買って' if today_state else '売って'}ください。"
        )
    else:
        action = "hold"
        action_note = (
            f"前回と状態は同じです。既に{state_label}なら、そのまま保有を続けてください"
            f"（新たな売買は不要です）。"
        )

    return EtfTimingStatus(
        available=True,
        in_market=today_state,
        changed=changed,
        action=action,
        signal_pct=round(latest_signal * 100, 2),
        signal_date=signal_date,
        etf_price=round(latest_close, 1),
        etf_change_pct=round(change_pct, 2),
        note=note,
        action_note=action_note,
    )
