"""
価格以外の情報を使う特徴量。

移動平均やRSIは「価格をこねくり回しただけ」で新しい情報を持ちません。
10年検証でテクニカル指標だけでは優位性が出なかったため、
情報の種類そのものを増やす目的で以下を追加しています。

1. 出来高急増（VOL_RATIO）… 直近の出来高が平常時の何倍か
2. 市場相対強弱（RS_MARKET）… TOPIX ETF に対する超過リターン
3. 業種相対強弱（RS_SECTOR）… 同業種の平均に対する超過リターン

いずれも「後ろ向きのrolling計算」だけで作っており、未来の情報は入りません。
決算日は event.py 側で扱います（過去データが乏しくバックテストできないため、
スコアには入れず警告表示のみ）。
"""
import pandas as pd

# 市場全体の代理として使うETF（TOPIX連動）
MARKET_SYMBOL = "1306.T"

# 出来高の平常時を測る期間
VOLUME_WINDOW = 20
# 相対強弱を測る期間（営業日）
RS_WINDOW = 20


def add_volume_features(df: pd.DataFrame) -> pd.DataFrame:
    """出来高が平常時の何倍かを VOL_RATIO 列に入れる。

    1.0 が平常。2.0 なら平均の2倍の商いが出ている。
    """
    df = df.copy()
    base = df["volume"].rolling(window=VOLUME_WINDOW, min_periods=VOLUME_WINDOW).mean()
    df["VOL_RATIO"] = (df["volume"] / base.replace(0, pd.NA)).astype("float64")
    return df


def _pct_change(series: pd.Series, window: int) -> pd.Series:
    return series.pct_change(periods=window) * 100


def add_relative_strength(
    df: pd.DataFrame, market: pd.DataFrame | None, peers: pd.DataFrame | None = None
) -> pd.DataFrame:
    """市場・業種に対する超過リターンを RS_MARKET / RS_SECTOR 列に入れる。

    market: 列 date, close を持つ市場指数のDataFrame
    peers : 列 date, close を持つ同業種平均のDataFrame（無ければ RS_SECTOR は NaN）
    どちらも日付で突き合わせるため、休日のズレがあっても壊れません。
    """
    df = df.copy()
    own = _pct_change(df["close"], RS_WINDOW)

    for col, ref in (("RS_MARKET", market), ("RS_SECTOR", peers)):
        if ref is None or ref.empty:
            # 基準データが無い場合は「値なし」を float の NaN で埋める
            df[col] = float("nan")
            continue
        ref_series = (
            ref.set_index("date")["close"].sort_index().reindex(df["date"]).ffill()
        )
        ref_change = _pct_change(ref_series.reset_index(drop=True), RS_WINDOW)
        df[col] = (own.reset_index(drop=True) - ref_change).to_numpy()

    return df


def compute_all(
    df: pd.DataFrame, market: pd.DataFrame | None = None, peers: pd.DataFrame | None = None
) -> pd.DataFrame:
    df = add_volume_features(df)
    df = add_relative_strength(df, market, peers)
    return df


def build_sector_average(frames: dict[str, pd.DataFrame]) -> pd.DataFrame:
    """同業種の銘柄群から、等ウェイトの業種インデックスを作る。

    各銘柄の終値を最初の値で正規化してから平均する（株価水準の違いを打ち消すため）。
    """
    normalized = []
    for df in frames.values():
        if df.empty or "close" not in df.columns:
            continue
        s = df.set_index("date")["close"]
        first = s.iloc[0]
        if first and first > 0:
            normalized.append(s / first)
    if not normalized:
        return pd.DataFrame(columns=["date", "close"])

    avg = pd.concat(normalized, axis=1).mean(axis=1).sort_index()
    return pd.DataFrame({"date": avg.index, "close": avg.to_numpy()})
