"""
保有ポジションの管理。

`backend/positions.json` に記録した取得単価・株数から、含み損益と
ルール上の損切り・利確ラインを計算します。

■ 重要な前提（必ず読んでください）
ここで表示する損切り・利確ラインの根拠は、**検証で買い持ちに負けているルール**です
（同時保有3銘柄で年+19.1%、同じ銘柄を買い持ちすると年+25.1%）。
「このラインで売れば利益が最大になる」という意味ではありません。
ルールが機械的にどこを指すかを表示しているだけです。

■ 損切り・利確の基準
- 損切り = 取得単価 − ATR × STOP_ATR_MULTIPLE
- 利確①  = 取得単価 + 損切り幅 × TAKE_PROFIT_R_MULTIPLE_1
ATRは現時点の値を使うため、値動きが荒くなればラインも自動的に広がります。
"""
import json
import pathlib
from datetime import date

import pandas as pd

from . import config

POSITIONS_PATH = pathlib.Path(__file__).resolve().parent.parent / "positions.json"


def load() -> list[dict]:
    """positions.json を読み込む。無い・壊れている場合は空リスト。"""
    if not POSITIONS_PATH.exists():
        return []
    try:
        data = json.loads(POSITIONS_PATH.read_text(encoding="utf-8"))
    except Exception:
        return []
    items = data.get("positions", []) if isinstance(data, dict) else data
    return [p for p in items if isinstance(p, dict) and p.get("code")]


def save(positions: list[dict]) -> None:
    """positions.json を書き換える。_readme は保持する。"""
    readme = []
    if POSITIONS_PATH.exists():
        try:
            existing = json.loads(POSITIONS_PATH.read_text(encoding="utf-8"))
            readme = existing.get("_readme", []) if isinstance(existing, dict) else []
        except Exception:
            pass
    POSITIONS_PATH.write_text(
        json.dumps({"_readme": readme, "positions": positions},
                   ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def upsert(entry: dict) -> list[dict]:
    """1銘柄分を追加または更新して保存する。lots が空なら削除扱い。"""
    code = str(entry.get("code", "")).strip()
    if not code:
        raise ValueError("証券コードが指定されていません")

    lots = [
        {"avg_cost": float(l["avg_cost"]), "shares": int(l["shares"])}
        for l in entry.get("lots", [])
        if l.get("avg_cost") is not None and l.get("shares")
    ]

    current = [p for p in load() if str(p.get("code")) != code]
    if lots:
        record = {"code": code, "name": entry.get("name") or code, "lots": lots}
        if entry.get("acquired_on"):
            record["acquired_on"] = entry["acquired_on"]
        current.append(record)
    save(current)
    return current


def _to_date(v) -> date | None:
    if not v:
        return None
    try:
        return pd.Timestamp(v).date()
    except Exception:
        return None


def _lots(position: dict) -> list[dict]:
    """ロット一覧を取り出す。単一ロット形式（avg_cost/shares）にも対応する。"""
    lots = position.get("lots")
    if isinstance(lots, list) and lots:
        return [
            l for l in lots
            if isinstance(l, dict) and l.get("avg_cost") is not None and l.get("shares")
        ]
    if position.get("avg_cost") is not None and position.get("shares"):
        return [{"avg_cost": position["avg_cost"], "shares": position["shares"]}]
    return []


def _weighted_cost(lots: list[dict]) -> tuple[float | None, int]:
    """加重平均取得単価と合計株数。"""
    total_shares = sum(int(l["shares"]) for l in lots)
    if not total_shares:
        return None, 0
    total_cost = sum(float(l["avg_cost"]) * int(l["shares"]) for l in lots)
    return total_cost / total_shares, total_shares


def evaluate(position: dict, df: pd.DataFrame, tf) -> dict:
    """1銘柄分の含み損益とルール上のラインを計算する。

    df は indicators.compute_all 済み（ATR列が必要）。
    複数回に分けて買っている場合は加重平均取得単価で評価し、
    ロットごとの損益も併せて返す。
    """
    latest = df.iloc[-1]
    price = float(latest["close"])
    lots = _lots(position)
    cost, shares = _weighted_cost(lots)

    result = {
        "code": position["code"],
        "name": position.get("name") or position["code"],
        "avg_cost": round(cost, 2) if cost is not None else None,
        "shares": shares,
        "price": round(price, 1),
        "updated_at": latest["date"].strftime(tf.date_format),
        "has_cost": cost is not None,
    }

    if cost is None:
        result["note"] = (
            "取得単価が未入力です。backend/positions.json の avg_cost（または lots）に"
            "入力すると含み損益と損切り・利確ラインを計算します"
        )
        return result

    result["unrealized_pct"] = round((price / cost - 1) * 100, 2)
    result["unrealized_yen"] = round((price - cost) * shares, 0)

    # 複数回に分けて買っている場合は、ロットごとの損益も出す
    if len(lots) > 1:
        result["lots"] = [
            {
                "avg_cost": float(l["avg_cost"]),
                "shares": int(l["shares"]),
                "unrealized_pct": round((price / float(l["avg_cost"]) - 1) * 100, 2),
                "unrealized_yen": round((price - float(l["avg_cost"])) * int(l["shares"]), 0),
            }
            for l in lots
        ]

    # 損切り・利確は取得単価を基準に、現時点のATRで幅を決める
    atr = latest.get("ATR")
    if pd.notna(atr) and float(atr) > 0:
        risk = config.STOP_ATR_MULTIPLE * float(atr)
        basis_note = f"ATR（{tf.atr_period}日平均値幅 {float(atr):,.1f}円）×{config.STOP_ATR_MULTIPLE:g}"
    else:
        risk = cost * config.stop_loss_pct(tf.key)
        basis_note = f"取得単価の{config.stop_loss_pct(tf.key):.0%}"

    stop = cost - risk
    result["stop_loss"] = round(stop, 1)
    result["stop_loss_note"] = f"取得単価 {cost:,.1f}円 − {basis_note}"

    # --- トレーリングストップ ---
    # 検証で唯一買い持ちを上回った方式。取得後につけた高値から一定幅下を追いかけ、
    # 切り上げるだけで下げない。取得日が未入力なら直近90日の高値で代用する。
    acquired = _to_date(position.get("acquired_on"))
    if acquired is not None:
        since = df[df["date"].dt.date >= acquired]
        peak_note = f"取得日（{acquired:%Y-%m-%d}）以降の高値"
    else:
        since = df.tail(tf.forward_bars)
        peak_note = f"直近{tf.forward_bars}営業日の高値"

    if not since.empty:
        peak = float(since["high"].max())
        trail = max(peak - risk, stop)  # 切り上げるだけ（初期の損切りより下げない）
        result["peak_since_entry"] = round(peak, 1)
        result["trailing_stop"] = round(trail, 1)
        result["trailing_note"] = (
            f"{peak_note} {peak:,.1f}円 − {basis_note}。高値更新に合わせて切り上がります"
        )
        result["locked_in_pct"] = round((trail / cost - 1) * 100, 2)
    result["take_profit_1"] = round(cost + config.TAKE_PROFIT_R_MULTIPLE_1 * risk, 1)
    result["take_profit_2"] = round(cost + config.TAKE_PROFIT_R_MULTIPLE_2 * risk, 1)
    result["target_note"] = (
        f"損切り幅（{risk:,.1f}円）の{config.TAKE_PROFIT_R_MULTIPLE_1:g}倍／"
        f"{config.TAKE_PROFIT_R_MULTIPLE_2:g}倍"
    )

    # ルール上どの状態にあるか。現行ルールはトレーリングなので、そちらを優先して判定する
    trail = result.get("trailing_stop")
    if trail is not None and config.STOP_MODE == "trail_atr":
        if price <= trail:
            result["status"] = "stop_hit"
            result["status_label"] = "ルール上はトレーリングストップを下回っています"
        else:
            result["status"] = "holding"
            room = (price / trail - 1) * 100
            result["status_label"] = (
                f"トレーリングストップまで {room:.1f}% の余裕があります"
            )
    elif price <= stop:
        result["status"] = "stop_hit"
        result["status_label"] = "ルール上は損切りラインを既に下回っています"
    elif price >= result["take_profit_1"]:
        result["status"] = "target_hit"
        result["status_label"] = "ルール上は利確①に到達しています"
    else:
        result["status"] = "holding"
        result["status_label"] = "損切りと利確①の間にあります"

    if acquired:
        result["held_days"] = (date.today() - acquired).days

    return result


def summarize(rows: list[dict]) -> dict:
    """全ポジションの合計。取得単価が入っているものだけを集計する。"""
    priced = [r for r in rows if r.get("has_cost") and r.get("shares")]
    if not priced:
        return {"total_cost": 0, "total_value": 0, "unrealized_yen": 0, "unrealized_pct": None}
    total_cost = sum(r["avg_cost"] * r["shares"] for r in priced)
    total_value = sum(r["price"] * r["shares"] for r in priced)
    return {
        "total_cost": round(total_cost, 0),
        "total_value": round(total_value, 0),
        "unrealized_yen": round(total_value - total_cost, 0),
        "unrealized_pct": round((total_value / total_cost - 1) * 100, 2) if total_cost else None,
        "counted": len(priced),
        "total": len(rows),
    }
