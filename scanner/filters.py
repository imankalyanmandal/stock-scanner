"""Per-stock metrics, hard gates, flags and scoring.

Pipeline per stock:
  compute_metrics()  -> raw numbers from price history
  evaluate()         -> reject reasons (gates), flags (warnings), score breakdown
"""
import math

import numpy as np
import pandas as pd

from .indicators import atr, rsi, sma, swing_highs

MIN_BARS = 60


def _streak(mask: pd.Series) -> int:
    n = 0
    for v in reversed(mask.tolist()):
        if not v:
            break
        n += 1
    return n


def _ret(s: pd.Series, n: int, offset: int = 0) -> float:
    end = len(s) - 1 - offset
    start = end - n
    if start < 0:
        return float("nan")
    return float(s.iloc[end] / s.iloc[start] - 1)


def compute_metrics(df: pd.DataFrame, bench_close: pd.Series,
                    risk_params: dict | None = None) -> dict | None:
    df = df.dropna(subset=["Close"])
    if len(df) < MIN_BARS:
        return None
    o, h, l, c, v = (df[k].astype(float) for k in ("Open", "High", "Low", "Close", "Volume"))
    s20, s50 = sma(c, 20), sma(c, 50)
    r = rsi(c)
    a = atr(h, l, c)
    close, hi, lo, op = c.iloc[-1], h.iloc[-1], l.iloc[-1], o.iloc[-1]
    atr_v = float(a.iloc[-1])
    if not atr_v or math.isnan(atr_v):
        return None

    # green-day streaks
    up_prev = c.diff() > 0
    up_candle = c > o
    turnover = c * v
    vol_avg20 = float(v.iloc[-21:-1].mean())

    # structure
    consol_high = float(h.iloc[-22:-2].max())
    hh_hl = bool(h.iloc[-5:].max() > h.iloc[-10:-5].max() and l.iloc[-5:].min() > l.iloc[-10:-5].min())
    s20_rising = bool(s20.iloc[-1] > s20.iloc[-6])
    touched_20dma = bool(((l.iloc[-5:] - s20.iloc[-5:]) / atr_v).min() <= 0.5)
    breakout = bool(close > consol_high)
    pullback = bool(s20_rising and touched_20dma and close > s20.iloc[-1])
    setup = "breakout" if breakout else ("pullback" if pullback else "none")

    # risk / reward
    #   stop: breakout -> just under the breakout level; otherwise under the last 5 bars' low.
    #         Always at least 1 ATR from the close.
    #   target: nearest significant prior swing high above price; if none (fresh highs),
    #           a fixed ATR multiple.
    rp = risk_params or {}
    pivot_w = rp.get("resistance_pivot_window", 10)
    blue_sky = rp.get("blue_sky_target_atr", 4.0)
    if setup == "breakout":
        stop = consol_high - 0.5 * atr_v
    else:
        stop = float(l.iloc[-5:].min()) - 0.1 * atr_v
    if close - stop < atr_v:
        stop = close - atr_v
    risk = close - stop
    sh = swing_highs(h.iloc[:-2], window=pivot_w).iloc[-250:]
    overhead = sh[sh > close + 0.5 * atr_v]
    at_high = overhead.empty
    target = float(overhead.min()) if not at_high else close + blue_sky * atr_v
    rr = (target - close) / risk if risk > 0 else 0.0

    # relative strength vs benchmark
    b = bench_close.reindex(df.index).ffill()
    rs5 = _ret(c, 5) - _ret(b, 5)
    rs20 = _ret(c, 20) - _ret(b, 20)
    rs5_prev = _ret(c, 5, offset=5) - _ret(b, 5, offset=5)

    gap = op / c.iloc[-2] - 1
    return {
        "last_date": df.index[-1].date().isoformat(),
        "close": float(close),
        "open": float(op),
        "green_prev_streak": _streak(up_prev),
        "green_candle_streak": _streak(up_candle),
        "green_both_streak": _streak(up_prev & up_candle),
        "gain_2d_pct": _ret(c, 2) * 100,
        "median_turnover_cr": float(turnover.iloc[-20:].median()) / 1e7,
        "avg_volume20": vol_avg20,
        "vol_ratio": float(v.iloc[-1] / vol_avg20) if vol_avg20 else 0.0,
        "close_location": float((close - lo) / (hi - lo)) if hi > lo else 1.0,
        "no_range": bool(hi == lo),
        "gap_pct": float(gap * 100),
        "sma20": float(s20.iloc[-1]),
        "sma50": float(s50.iloc[-1]),
        "above_sma20": bool(close > s20.iloc[-1]),
        "rsi": float(r.iloc[-1]),
        "atr": atr_v,
        "atr_pct": atr_v / close * 100,
        "extension_atr": float((close - s20.iloc[-1]) / atr_v),
        "hh_hl": hh_hl,
        "setup": setup,
        "consol_high": consol_high,
        "entry": float(close),
        "stop": float(stop),
        "target": float(target),
        "stop_atr": float(risk / atr_v),
        "reward_risk": float(rr),
        "at_resistance_free_high": at_high,
        "ret5": _ret(c, 5),
        "ret20": _ret(c, 20),
        "rs5_idx": rs5,
        "rs20_idx": rs20,
        "rs_improving": bool(rs5 > rs5_prev),
    }


def _clip01(x: float) -> float:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return 0.0
    return float(min(max(x, 0.0), 1.0))


def evaluate(m: dict, cfg: dict, sector_ctx: dict, delivery_pct: float | None) -> dict:
    """Apply gates, flags and score. sector_ctx: {ret5_med, ret20_med, breadth_pct}."""
    g, sc = cfg["gates"], cfg["scoring"]
    reasons, flags = [], []

    # ---- hard gates ----
    if m["close"] < g["min_price"]:
        reasons.append(f"price<{g['min_price']}")
    if m["median_turnover_cr"] < g["min_median_turnover_cr"]:
        reasons.append("low_turnover")
    if m["avg_volume20"] < g["min_avg_volume"]:
        reasons.append("low_volume")
    streak = m[{"prev_close": "green_prev_streak", "candle": "green_candle_streak",
                "both": "green_both_streak"}[g["green_mode"]]]
    if streak < g["green_days"]:
        reasons.append(f"not_{g['green_days']}_green_days")
    if m["gain_2d_pct"] > g["max_2day_gain_pct"]:
        reasons.append("extended_2day_gain")
    if m["rsi"] > g["rsi_hard_max"]:
        reasons.append("overbought_rsi")
    if m["extension_atr"] > g["max_extension_atr"]:
        reasons.append("stretched_from_20dma")
    if m["reward_risk"] < g["min_reward_risk"]:
        reasons.append("reward_risk_below_min")
    if m["stop_atr"] > g["max_stop_atr"]:
        reasons.append("stop_too_wide")
    if g.get("reject_no_setup") and m["setup"] == "none":
        reasons.append("no_setup")

    # ---- flags (warnings, not rejects) ----
    if m["no_range"]:
        flags.append("possible_circuit")
    if m["gap_pct"] > 3 and m["close_location"] < 0.5:
        flags.append("gap_up_weak_close")
    if abs(m["gap_pct"]) > sc["gap_flag_pct"]:
        flags.append("abnormal_gap")
    if m["atr_pct"] > sc["high_atr_pct"]:
        flags.append("high_volatility")
    if m["setup"] == "breakout" and m["vol_ratio"] < sc["volume_ratio_target"]:
        flags.append("breakout_low_volume")
    if sector_ctx.get("breadth_pct", 100) < cfg["market"]["sector_weak_pct"]:
        flags.append("weak_sector")
    if m["at_resistance_free_high"]:
        flags.append("no_overhead_resistance")

    # ---- scoring (each component 0..1) ----
    rs5_sec = m["ret5"] - sector_ctx.get("ret5_med", 0.0)
    rs20_sec = m["ret20"] - sector_ctx.get("ret20_med", 0.0)
    lo_b, hi_b = sc["rsi_band"]
    rsi_v = m["rsi"]
    if lo_b <= rsi_v <= hi_b:
        rsi_s = 1.0
    elif lo_b - 5 <= rsi_v <= hi_b + 5:
        rsi_s = 0.5
    else:
        rsi_s = 0.0
    rs_hits = sum(x > 0 for x in (m["rs5_idx"], m["rs20_idx"], rs5_sec, rs20_sec))
    comp = {
        "volume": _clip01((m["vol_ratio"] - 1.0) / (2 * sc["volume_ratio_target"] - 2.0 or 1)),
        "close_location": _clip01((m["close_location"] - 0.4) / (sc["close_location_min"] - 0.4)),
        "delivery": 0.5 if delivery_pct is None or np.isnan(delivery_pct)
        else _clip01((delivery_pct - 25) / (sc["delivery_min_pct"] + 15 - 25)),
        "structure": 1.0 if m["hh_hl"] else 0.0,
        "trend": (int(m["close"] > m["sma20"]) + int(m["sma20"] > m["sma50"])
                  + int(m["close"] > m["sma50"])) / 3,
        "rsi": rsi_s,
        "rel_strength": _clip01((rs_hits + (1 if m["rs_improving"] else 0)) / 5),
        "reward_risk": _clip01((m["reward_risk"] - g["min_reward_risk"]) / 2.0),
    }
    w = sc["weights"]
    total_w = sum(w.values()) or 1
    score = sum(comp[k] * w[k] for k in w) / total_w * 100

    return {
        "rejected": bool(reasons),
        "reject_reasons": reasons,
        "flags": flags,
        "score": round(score, 1),
        "components": {k: round(val, 2) for k, val in comp.items()},
        "rs5_sec": rs5_sec,
        "rs20_sec": rs20_sec,
        "delivery_pct": delivery_pct,
    }
