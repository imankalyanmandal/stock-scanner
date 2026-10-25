"""Position sizing, trade simulation and performance stats.

simulate() replays one trade plan bar by bar with the exit rules in config.yaml:
  - buy-stop at `trigger`, valid for `trigger_valid_days`; skipped if the open is above `max_entry`
  - initial stop; on the fill day a touch of the stop counts as stopped out (conservative)
  - partial exit at fill + partial_r * R, then stop to breakeven
  - the rest trails: exit on a close below the trailing EMA
  - time stop: not at least time_stop_min_r R up after time_stop_days -> exit at close
  - hard exit after max_hold_days
  - if the stop and the target are both inside one bar, the stop is assumed to hit first
R multiples are net of `cost_pct` round-trip costs.
"""
import math

import numpy as np
import pandas as pd


def size_position(trigger: float, stop: float, cfg: dict, size_mult: float) -> dict:
    s = cfg["sizing"]
    risk = trigger - stop
    if risk <= 0 or size_mult <= 0:
        return {"qty": 0, "position_value": 0.0, "risk_amount": 0.0, "size_mult": size_mult,
                "capped": False}
    qty = math.floor(s["capital"] * s["risk_per_trade_pct"] / 100 * size_mult / risk)
    max_qty = math.floor(s["capital"] * s["max_position_pct"] / 100 / trigger)
    capped = qty > max_qty
    qty = min(qty, max_qty)
    return {"qty": int(qty), "position_value": round(qty * trigger, 2),
            "risk_amount": round(qty * risk, 2), "size_mult": size_mult, "capped": capped}


def _d(idx, k):
    return pd.Timestamp(idx[k]).date().isoformat()


def simulate(A: dict, i: int, plan: dict, cfg: dict) -> dict:
    """A: filters.arrays(features). i: signal bar position. plan: trigger, max_entry, stop."""
    ex, en = cfg["exits"], cfg["entry"]
    idx, n = A["_index"], len(A["close"])
    O, H, L, C, T = A["open"], A["high"], A["low"], A["close"], A[ex["trail_ma"]]
    trig, mx, stop0 = plan["trigger"], plan["max_entry"], plan["stop"]
    last_valid = i + en["trigger_valid_days"]

    fill, fk = None, None
    for k in range(i + 1, min(n, last_valid + 1)):
        if O[k] > mx:
            return {"status": "skipped_gap", "fill_date": _d(idx, k)}
        if H[k] >= trig:
            fill, fk = float(max(O[k], trig)), k
            break
    if fill is None:
        return {"status": "pending" if n - 1 < last_valid else "not_triggered"}

    risk = fill - stop0
    t1 = fill + ex["partial_r"] * risk
    pfrac = ex["partial_pct"] / 100
    cur_stop, remaining, proceeds, partial = stop0, 1.0, 0.0, False
    exit_px = exit_k = reason = None

    for k in range(fk, n):
        held = k - fk
        if k == fk:
            if L[k] <= cur_stop:
                exit_px, exit_k, reason = cur_stop, k, "stop"
        elif O[k] <= cur_stop:
            exit_px, exit_k, reason = float(O[k]), k, "breakeven" if partial else "gap_stop"
        elif L[k] <= cur_stop:
            exit_px, exit_k, reason = cur_stop, k, "breakeven" if partial else "stop"
        if exit_px is not None:
            break
        if not partial and H[k] >= t1:
            px = t1 if k == fk else max(float(O[k]), t1)
            proceeds += pfrac * px
            remaining -= pfrac
            partial = True
            if ex.get("breakeven_after_partial", True):
                cur_stop = max(cur_stop, fill)
        if k == fk:
            continue
        if partial and np.isfinite(T[k]) and C[k] < T[k]:
            exit_px, exit_k, reason = float(C[k]), k, "trail"
        elif not partial and held >= ex["time_stop_days"] \
                and C[k] < fill + ex["time_stop_min_r"] * risk:
            exit_px, exit_k, reason = float(C[k]), k, "time_stop"
        elif held >= ex["max_hold_days"]:
            exit_px, exit_k, reason = float(C[k]), k, "max_hold"
        if exit_px is not None:
            break

    status = "closed" if exit_px is not None else "open"
    mark = exit_px if exit_px is not None else float(C[n - 1])
    total = proceeds + remaining * mark
    r_gross = (total - fill) / risk
    cost_r = ex["cost_pct"] / 100 * fill / risk
    end_k = exit_k if exit_k is not None else n - 1
    return {"status": status, "fill_date": _d(idx, fk), "fill_price": round(fill, 2),
            "exit_date": _d(idx, exit_k) if exit_k is not None else None,
            "exit_price": round(mark, 2), "exit_reason": reason, "partial_taken": partial,
            "r_multiple": round(r_gross - cost_r, 3), "pct_return": round((total / fill - 1) * 100, 2),
            "bars_held": int(end_k - fk), "exit_i": end_k}


def trade_stats(df: pd.DataFrame) -> dict:
    """df: closed trades with r_multiple (+ optional exit_date, bars_held)."""
    if df is None or df.empty:
        return {"trades": 0}
    r = df["r_multiple"].astype(float)
    wins, losses = r[r > 0], r[r <= 0]
    order = df.sort_values("exit_date") if "exit_date" in df else df
    curve = order["r_multiple"].astype(float).cumsum()
    dd = float((curve - curve.cummax().clip(lower=0)).min()) if len(curve) else 0.0
    gross_loss = -losses.sum()
    return {
        "trades": int(len(r)),
        "win_rate_pct": round(100 * len(wins) / len(r), 1),
        "avg_win_r": round(float(wins.mean()), 2) if len(wins) else 0.0,
        "avg_loss_r": round(float(losses.mean()), 2) if len(losses) else 0.0,
        "expectancy_r": round(float(r.mean()), 3),
        "total_r": round(float(r.sum()), 2),
        "profit_factor": round(float(wins.sum() / gross_loss), 2) if gross_loss > 0 else None,
        "max_drawdown_r": round(dd, 2),
        "avg_bars_held": round(float(df["bars_held"].mean()), 1) if "bars_held" in df else None,
    }
