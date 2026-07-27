"""
「どこまで上がる／下がるか」を過去の実測分布から示す。

■ これは予測ではありません
将来の株価を当てる能力はこのアプリにありません。ここで出すのは
**「過去に同じことをしていたら、実際にどうなったか」の分布**です。
中央値・上位25%・上位10%といった形で幅を示し、単一の予想値は出しません。

■ 2種類の統計
1. 無条件（unconditional）… 過去の任意の日に買った場合。全銘柄について高速に計算。
2. シグナル条件付き（conditional）… ブレイクアウトのシグナルが出た日に買った場合。
   計算が重いので、上位候補の数銘柄だけに適用する。

■ 用語
- MFE（最大上昇）… 保有期間内に一度でも到達した最大の含み益
- MAE（最大下落）… 同じく最大の含み損
「どこまで上がるか」はMFE、「どこまで下がるか」はMAEで答える。
MFEに到達しても、そこで売らなければ利益は確定しない点に注意。
"""
from dataclasses import dataclass, field

import numpy as np
import pandas as pd

from . import backtest, config
from .timeframes import TimeframeParams

# 日々の値動きを測る期間（営業日）
DAILY_WINDOW = 500
# 表示するパーセンタイル
PERCENTILES = (50, 75, 90)


@dataclass
class Projection:
    """過去の実測分布。すべて % 表記。"""

    samples: int = 0
    horizon_days: int = 0
    # 保有期間内に到達した最大上昇（MFE）
    up_p50: float | None = None
    up_p75: float | None = None
    up_p90: float | None = None
    # 保有期間内に到達した最大下落（MAE）
    down_p50: float | None = None
    down_p90: float | None = None
    # 保有期間終了時点のリターン
    end_p50: float | None = None
    # 1日の値動き（絶対値）
    day_p50: float | None = None
    day_p80: float | None = None
    conditional: bool = False
    note: str = ""

    def as_dict(self) -> dict:
        return {k: v for k, v in vars(self).items()}


def _pct(series: np.ndarray, q: int) -> float | None:
    if series.size == 0:
        return None
    return round(float(np.percentile(series, q)), 2)


def daily_move(df: pd.DataFrame) -> tuple[float | None, float | None]:
    """1日の値動き（終値ベース・絶対値%）の中央値と上位20%水準。"""
    ch = df["close"].pct_change().abs().dropna().tail(DAILY_WINDOW) * 100
    arr = ch.to_numpy()
    return _pct(arr, 50), _pct(arr, 80)


def unconditional(df: pd.DataFrame, tf: TimeframeParams) -> Projection:
    """過去の任意の日に買った場合の分布。ベクトル化して高速に計算する。"""
    n = tf.forward_bars
    if len(df) < n + DAILY_WINDOW // 4:
        return Projection(horizon_days=n, note="データ不足")

    close = df["close"].to_numpy(dtype=float)
    # 翌日から n 本先までの最高値・最安値（未来の値を「その時点の結果」として使う。
    # 過去分布の記述であり、売買判断に未来を混ぜているわけではない）
    fwd_high = df["high"].shift(-1).rolling(n, min_periods=n).max().shift(-(n - 1)).to_numpy()
    fwd_low = df["low"].shift(-1).rolling(n, min_periods=n).min().shift(-(n - 1)).to_numpy()
    fwd_close = df["close"].shift(-n).to_numpy()

    ok = ~(np.isnan(fwd_high) | np.isnan(fwd_low) | np.isnan(fwd_close)) & (close > 0)
    if not ok.any():
        return Projection(horizon_days=n, note="データ不足")

    mfe = (fwd_high[ok] / close[ok] - 1) * 100
    mae = (fwd_low[ok] / close[ok] - 1) * 100
    end = (fwd_close[ok] / close[ok] - 1) * 100
    d50, d80 = daily_move(df)

    return Projection(
        samples=int(ok.sum()),
        horizon_days=n,
        up_p50=_pct(mfe, 50), up_p75=_pct(mfe, 75), up_p90=_pct(mfe, 90),
        down_p50=_pct(mae, 50), down_p90=_pct(mae, 10),
        end_p50=_pct(end, 50),
        day_p50=d50, day_p80=d80,
        conditional=False,
        note=f"過去{int(ok.sum())}営業日それぞれで買ったと仮定した場合の実測分布",
    )


def conditional(df: pd.DataFrame, tf: TimeframeParams, min_stars: int = 4) -> Projection:
    """ブレイクアウトのシグナルが出た日に買った場合の分布（計算が重い）。"""
    n = tf.forward_bars
    labeled, events = backtest.collect_events(df, tf, min_stars=min_stars)
    if not events:
        return unconditional(df, tf)

    mfe, mae, end = [], [], []
    entry_spec = (config.ENTRY_MODE,)
    for ev in events:
        future = labeled.iloc[ev.index + 1 : ev.index + 1 + n]
        if future.empty:
            continue
        fill = backtest._find_fill(future, ev, entry_spec)
        if fill is None:
            continue
        j, price = fill
        if price <= 0:
            continue
        seg = future.iloc[j:]
        mfe.append((float(seg["high"].max()) / price - 1) * 100)
        mae.append((float(seg["low"].min()) / price - 1) * 100)
        end.append((float(seg["close"].iloc[-1]) / price - 1) * 100)

    if len(mfe) < 20:
        p = unconditional(df, tf)
        p.note += "（シグナル条件付きの試行が20回未満のため無条件の分布）"
        return p

    a_mfe, a_mae, a_end = np.array(mfe), np.array(mae), np.array(end)
    d50, d80 = daily_move(df)
    return Projection(
        samples=len(mfe),
        horizon_days=n,
        up_p50=_pct(a_mfe, 50), up_p75=_pct(a_mfe, 75), up_p90=_pct(a_mfe, 90),
        down_p50=_pct(a_mae, 50), down_p90=_pct(a_mae, 10),
        end_p50=_pct(a_end, 50),
        day_p50=d50, day_p80=d80,
        conditional=True,
        note=f"過去{len(mfe)}回の同型シグナル（高値ブレイク）で実際に約定した場合の分布",
    )
