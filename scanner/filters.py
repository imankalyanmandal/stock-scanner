"""Signal engine shared by the daily scan, the backtest and the trade journal.

  features()     vectorised indicator columns for one stock. Causal: row i only uses bars <= i.
  rs_composite() weighted multi-horizon return, ranked across the universe each day.
  prefilter()    cheap vectorised mask (hygiene + trend template + setup candidate). Backtest only.
  metrics_at()   setup detection, buy-stop trigger, stop, overhead resistance at one bar.
  evaluate()     hard gates, flags and score for one metrics dict.
  select()       rank survivors, cap per sector, keep the top N.
"""
import math

import numpy as np
import pandas as pd

from .indicators import atr, ema, pivot_highs, rsi, sma

MIN_BARS = 220  # 200 DMA + a little slope history


# --------------------------------------------------------------------------- features
def features(df: pd.DataFrame, cfg: dict, bench_close: pd.Series | None = None) -> pd.DataFrame | None:
    df = df.dropna(subset=["Close"])[["Open", "High", "Low", "Close", "Volume"]].astype(float)
    if len(df) < MIN_BARS:
        return None
    o, h, l, c, v = (df[k] for k in ("Open", "High", "Low", "Close", "Volume"))
    tr, bo = cfg["trend"], cfg["setups"]["breakout"]
    L = bo["base_len"]

    f = pd.DataFrame({"open": o, "high": h, "low": l, "close": c, "volume": v}, index=df.index)
    f["sma20"], f["sma50"], f["sma200"] = sma(c, 20), sma(c, 50), sma(c, 200)
    f["ema10"], f["ema20"] = ema(c, 10), ema(c, 20)
    n50, n200 = tr.get("sma50_rising_bars", 0), tr.get("sma200_rising_bars", 0)
    f["sma50_rising"] = (f["sma50"] > f["sma50"].shift(n50)) if n50 else True
    f["sma200_rising"] = (f["sma200"] > f["sma200"].shift(n200)) if n200 else True
    f["sma20_rising"] = f["sma20"] > f["sma20"].shift(5)
    f["atr"], f["rsi"] = atr(h, l, c), rsi(c)

    vprev = v.shift(1)  # averages exclude the signal day itself
    f["vol_avg20"] = vprev.rolling(20).mean()
    f["vol_avg50"] = vprev.rolling(50).mean()
    f["vol_ratio"] = v / f["vol_avg50"]
    f["turnover_med20_cr"] = (c * v).shift(1).rolling(20).median() / 1e7

    hi52 = h.rolling(252, min_periods=200).max()
    lo52 = l.rolling(252, min_periods=200).min()
    f["hi52"], f["lo52"] = hi52, lo52
    f["below_high_pct"] = (1 - c / hi52) * 100
    f["above_low_pct"] = (c / lo52 - 1) * 100

    rng = h - l
    f["locked"] = rng <= 0
    f["close_loc"] = ((c - l) / rng).where(rng > 0, 1.0)
    f["locked_20"] = f["locked"].astype(int).rolling(20, min_periods=1).sum()
    f["gap_pct"] = (o / c.shift(1) - 1) * 100
    f["gain_2d_pct"] = (c / c.shift(2) - 1) * 100
    f["ext_atr"] = (c - f["sma20"]) / f["atr"]
    f["green"] = c > c.shift(1)

    # breakout over the base = the L bars before the breakout day
    base_high = h.shift(1).rolling(L).max()
    above = (c > base_high).fillna(False)
    bo1 = above & ~above.shift(1, fill_value=False)
    bo2 = bo1.shift(1, fill_value=False) & (c > base_high.shift(1))
    f["bo_day"] = np.where(bo1, 1, np.where(bo2, 2, 0))
    f["base_high"] = base_high
    f["base_low"] = l.shift(1).rolling(L).min()
    f["base_rng10"] = h.shift(1).rolling(10).max() - l.shift(1).rolling(10).min()
    f["base_vol10"] = vprev.rolling(10).mean()
    # "cascading lows": the second half of the base undercuts the first half's low
    h1 = L // 2
    f["base_undercut"] = (l.shift(1).rolling(L - h1).min() < l.shift(1 + L - h1).rolling(h1).min())
    f["pivot"] = pivot_highs(h, cfg["entry"]["resistance_pivot_window"])

    for n in sorted(set(cfg["rs"]["lookbacks"]) | {21, 63, 126, 252}):
        f[f"ret{n}"] = c / c.shift(n) - 1
    f["vol252"] = np.log(c).diff().rolling(252, min_periods=200).std() * np.sqrt(252)
    if bench_close is not None and len(bench_close):
        b = bench_close.reindex(f.index).ffill()
        f["bench_ret21"] = b / b.shift(21) - 1
        f["bench_ret63"] = b / b.shift(63) - 1
    else:
        f["bench_ret21"] = f["bench_ret63"] = np.nan
    return f


def rs_composite(f: pd.DataFrame, cfg: dict) -> pd.Series:
    r = cfg["rs"]
    return sum(w * f[f"ret{n}"] for n, w in zip(r["lookbacks"], r["weights"]))


def rs_rank_table(feats: dict[str, pd.DataFrame], cfg: dict) -> pd.DataFrame:
    """dates x symbols percentile rank (0-100) of the RS composite, across the universe."""
    comp = pd.DataFrame({s: rs_composite(f, cfg) for s, f in feats.items()})
    return comp.rank(axis=1, pct=True) * 100


def momentum_scores(feats: dict[str, pd.DataFrame], cfg: dict) -> pd.DataFrame:
    """NSE-style momentum: z-scores of 6- and 12-month return / 1-year volatility, averaged.

    dates x symbols; NaN where a stock lacks history or fails the liquidity gates.
    """
    g = cfg["gates"]
    m6, m12, ok = {}, {}, {}
    for s, f in feats.items():
        m6[s] = f["ret126"] / f["vol252"]
        m12[s] = f["ret252"] / f["vol252"]
        ok[s] = (f["close"] >= g["min_price"]) & (f["turnover_med20_cr"] >= g["min_median_turnover_cr"])
    m6, m12, ok = pd.DataFrame(m6), pd.DataFrame(m12), pd.DataFrame(ok).fillna(False).astype(bool)

    def z(df):
        return df.sub(df.mean(axis=1), axis=0).div(df.std(axis=1), axis=0)
    return ((z(m6) + z(m12)) / 2).where(ok)


def momentum_rank_table(feats: dict[str, pd.DataFrame], cfg: dict) -> pd.DataFrame:
    """dates x symbols rank position by momentum score (1 = strongest)."""
    return momentum_scores(feats, cfg).rank(axis=1, ascending=False, method="first")


def prefilter(f: pd.DataFrame, cfg: dict) -> pd.Series:
    g, tr = cfg["gates"], cfg["trend"]
    m = ((f["close"] >= g["min_price"]) & (f["turnover_med20_cr"] >= g["min_median_turnover_cr"])
         & (f["vol_avg20"] >= g["min_avg_volume"]) & (f["locked_20"] <= g["max_locked_days_20"])
         & (f["below_high_pct"] <= tr["max_below_52w_high_pct"])
         & (f["above_low_pct"] >= tr["min_above_52w_low_pct"])
         & f["sma50_rising"].astype(bool) & f["sma200_rising"].astype(bool))
    if tr.get("require_close_above_sma50", True):
        m &= f["close"] > f["sma50"]
    if tr.get("require_sma50_above_sma200", True):
        m &= f["sma50"] > f["sma200"]
    m &= (f["bo_day"] > 0) | (f["green"] & (f["close"] > f["sma20"]))
    return m.fillna(False)


# --------------------------------------------------------------------------- per-bar analysis
def arrays(f: pd.DataFrame) -> dict:
    a = {c: f[c].to_numpy() for c in f.columns}
    a["_index"] = f.index
    return a


def _pullback(A: dict, i: int, p: dict, atr_v: float) -> tuple[dict | None, str]:
    s = max(0, i - p["lookback"])
    if i - s < 1:
        return None, "pb_no_history"
    j_hi = s + int(np.argmax(A["high"][s:i]))
    n_pb = i - j_hi - 1
    if n_pb < p["min_bars"]:
        return None, "pb_no_pullback"
    lows = A["low"][j_hi + 1:i + 1]
    pb_low = float(lows.min())
    depth = (A["high"][j_hi] - pb_low) / atr_v
    if depth < p["min_depth_atr"]:
        return None, "pb_too_shallow"
    if depth > p["max_depth_atr"]:
        return None, "pb_too_deep"
    off = float(((lows - A["sma20"][j_hi + 1:i + 1]) / atr_v).min())
    if off > p["touch_above_atr"]:
        return None, "pb_no_20dma_touch"
    if off < -p["touch_below_atr"]:
        return None, "pb_broke_20dma"
    if not A["sma20_rising"][i]:
        return None, "pb_20dma_not_rising"
    c, pc = A["close"][i], A["close"][i - 1]
    if not (c > pc and c >= A["open"][i] and c > A["sma20"][i]):
        return None, "pb_no_reversal_day"
    return {"pb_high": float(A["high"][j_hi]), "pb_low": pb_low, "pb_depth_atr": float(depth),
            "pb_bars": n_pb, "touch_offset_atr": off,
            "setup_vol_ratio": float(A["volume"][j_hi + 1:i].mean() / A["vol_avg50"][i]),
            "setup_close_loc": float(A["close_loc"][i]), "setup_low": pb_low,
            "trigger_base": float(A["high"][i])}, ""


def _f(x):
    return None if x is None or (isinstance(x, float) and math.isnan(x)) else float(x)


def metrics_at(A: dict, i: int, cfg: dict, rs_rank: float | None = None,
               mom_rank: float | None = None) -> dict | None:
    if i < 5:
        return None
    atr_v = A["atr"][i]
    if not np.isfinite(atr_v) or atr_v <= 0 or not np.isfinite(A["sma200"][i]) \
            or not np.isfinite(A["hi52"][i]):
        return None
    st, en, ex = cfg["setups"], cfg["entry"], cfg["exits"]
    close = float(A["close"][i])

    setup, info, setup_fail = "none", {}, ""
    bo_day = int(A["bo_day"][i])
    if bo_day and bo_day <= st["breakout"]["max_day"] and st["breakout"].get("enabled", True):
        j = i - (bo_day - 1)  # the breakout bar
        bh, bl = A["base_high"][j], A["base_low"][j]
        setup = "breakout"
        info = {"bo_day": bo_day, "base_high": float(bh),
                "base_depth_pct": float((bh - bl) / bh * 100),
                "contraction": float(A["base_rng10"][j] / (bh - bl)) if bh > bl else 1.0,
                "base_vol_ratio": float(A["base_vol10"][j] / A["vol_avg50"][j]),
                "base_undercut": bool(A["base_undercut"][j]),
                "setup_vol_ratio": float(A["vol_ratio"][j]),
                "setup_close_loc": float(A["close_loc"][j]),
                "setup_low": float(A["low"][j:i + 1].min()),
                "trigger_base": float(A["high"][j:i + 1].max())}
    elif st["pullback"].get("enabled", True):
        pb, setup_fail = _pullback(A, i, st["pullback"], atr_v)
        if pb:
            setup, info = "pullback", pb
    else:
        setup_fail = "pullbacks_disabled"
    if setup == "none":
        info = {"setup_low": float(A["low"][i - 4:i + 1].min()), "trigger_base": float(A["high"][i])}

    trigger = info["trigger_base"] * (1 + en["trigger_buffer_pct"] / 100)
    max_entry = trigger * (1 + en["max_gap_above_trigger_pct"] / 100)
    stop = min(info["setup_low"] - en["stop_buffer_atr"] * atr_v,
               trigger - en["min_stop_atr"] * atr_v)
    risk = trigger - stop

    # overhead resistance: confirmed pivots (known by bar i) + the last few unconfirmed highs
    w = en["resistance_pivot_window"]
    lo_j = max(0, i - 250)
    conf_end = max(lo_j, i - w + 1)
    piv = A["high"][lo_j:conf_end][A["pivot"][lo_j:conf_end].astype(bool)]
    levels = np.concatenate([piv, A["high"][max(0, i - w):i]])
    over = levels[levels > trigger]
    resistance = float(over.min()) if over.size else None
    room_r = (resistance - trigger) / risk if resistance is not None else math.inf

    return {
        "last_date": pd.Timestamp(A["_index"][i]).date().isoformat(),
        "close": close, "open": float(A["open"][i]), "high": float(A["high"][i]),
        "low": float(A["low"][i]), "atr": float(atr_v), "atr_pct": float(atr_v / close * 100),
        "rsi": _f(A["rsi"][i]), "sma20": float(A["sma20"][i]), "sma50": float(A["sma50"][i]),
        "sma200": float(A["sma200"][i]), "ext_atr": float(A["ext_atr"][i]),
        "gain_2d_pct": _f(A["gain_2d_pct"][i]), "gap_pct": _f(A["gap_pct"][i]),
        "close_location": float(A["close_loc"][i]), "vol_ratio": _f(A["vol_ratio"][i]),
        "avg_volume20": _f(A["vol_avg20"][i]), "median_turnover_cr": _f(A["turnover_med20_cr"][i]),
        "locked_20": int(A["locked_20"][i]),
        "below_high_pct": float(A["below_high_pct"][i]), "above_low_pct": float(A["above_low_pct"][i]),
        "close_above_sma50": bool(close > A["sma50"][i]),
        "sma50_above_sma200": bool(A["sma50"][i] > A["sma200"][i]),
        "sma50_rising": bool(A["sma50_rising"][i]), "sma200_rising": bool(A["sma200_rising"][i]),
        "setup": setup, "setup_fail": setup_fail, **info,
        "trigger": float(trigger), "max_entry": float(max_entry), "stop": float(stop),
        "risk_per_share": float(risk), "stop_atr": float(risk / atr_v),
        "t1": float(trigger + ex["partial_r"] * risk),
        "resistance": resistance, "room_r": float(room_r),
        "ret21": _f(A["ret21"][i]), "ret63": _f(A["ret63"][i]),
        "bench_ret21": _f(A["bench_ret21"][i]), "bench_ret63": _f(A["bench_ret63"][i]),
        "rs_rank": _f(rs_rank), "mom_rank": _f(mom_rank),
    }


# --------------------------------------------------------------------------- gates + score
def _clip01(x) -> float:
    if x is None or (isinstance(x, float) and math.isnan(x)):
        return 0.0
    return float(min(max(x, 0.0), 1.0))


def evaluate(m: dict, cfg: dict, sector_ctx: dict | None = None,
             delivery_pct: float | None = None, excluded: str | None = None) -> dict:
    g, tr, sc, st = cfg["gates"], cfg["trend"], cfg["scoring"], cfg["setups"]
    sector_ctx = sector_ctx or {}
    R, flags = [], []

    # hygiene
    if excluded:
        R.append(f"excluded:{excluded}")
    if m["close"] < g["min_price"]:
        R.append(f"price<{g['min_price']}")
    if (m["median_turnover_cr"] or 0) < g["min_median_turnover_cr"]:
        R.append("low_turnover")
    if (m["avg_volume20"] or 0) < g["min_avg_volume"]:
        R.append("low_volume")
    if m["locked_20"] > g["max_locked_days_20"]:
        R.append("circuit_history")
    # stage-2 trend template
    if tr.get("require_close_above_sma50", True) and not m["close_above_sma50"]:
        R.append("below_50dma")
    if tr.get("require_sma50_above_sma200", True) and not m["sma50_above_sma200"]:
        R.append("50dma_below_200dma")
    if not m["sma50_rising"]:
        R.append("50dma_not_rising")
    if not m["sma200_rising"]:
        R.append("200dma_not_rising")
    if m["below_high_pct"] > tr["max_below_52w_high_pct"]:
        R.append("far_from_52w_high")
    if m["above_low_pct"] < tr["min_above_52w_low_pct"]:
        R.append("too_close_to_52w_low")
    # leadership
    if m["rs_rank"] is not None and m["rs_rank"] < cfg["rs"]["min_rank"]:
        R.append("weak_rs_rank")
    top_n = cfg["rs"].get("momentum_top_n")
    if top_n and (m.get("mom_rank") is None or m["mom_rank"] > top_n):
        R.append("not_momentum_leader")
    # setup quality
    if m["setup"] == "none":
        R.append(f"no_setup{':' + m['setup_fail'] if m.get('setup_fail') else ''}")
    elif m["setup"] == "breakout":
        b = st["breakout"]
        if m["base_depth_pct"] > b["max_base_depth_pct"]:
            R.append("base_too_deep")
        if m["setup_vol_ratio"] < b["min_vol_ratio"]:
            R.append("breakout_low_volume")
        if m["setup_close_loc"] < b["min_close_location"]:
            R.append("breakout_weak_close")
        if b.get("reject_undercut_base") and m.get("base_undercut"):
            R.append("base_undercut")
    else:
        p = st["pullback"]
        if m["setup_vol_ratio"] > p["max_vol_ratio"]:
            R.append("pullback_heavy_volume")
        if m["setup_close_loc"] < p["min_close_location"]:
            R.append("reversal_weak_close")
    # not chasing
    if (m["rsi"] or 0) > g["rsi_hard_max"]:
        R.append("overbought_rsi")
    if m["ext_atr"] > g["max_extension_atr"]:
        R.append("stretched_from_20dma")
    if (m["gain_2d_pct"] or 0) > g["max_2day_gain_pct"]:
        R.append("extended_2day_gain")
    # risk
    if m["stop_atr"] > g["max_stop_atr"]:
        R.append("stop_too_wide")
    room_min = (st.get(m["setup"], {}) or {}).get("min_room_r") or g["min_room_r"]
    if m["room_r"] < room_min:
        R.append("resistance_too_close")

    # flags (warnings)
    gap = m["gap_pct"] or 0
    if abs(gap) > sc["gap_flag_pct"]:
        flags.append("abnormal_gap")
    if gap > 3 and m["close_location"] < 0.5:
        flags.append("gap_up_weak_close")
    if m["atr_pct"] > sc["high_atr_pct"]:
        flags.append("high_volatility")
    mk = cfg["market"]
    if sector_ctx.get("count", 0) >= mk["sector_min_count"] \
            and sector_ctx.get("breadth_pct", 100) < mk["sector_weak_pct"]:
        flags.append("weak_sector")
    if math.isinf(m["room_r"]):
        flags.append("blue_sky")

    # score (each component 0..1)
    vr = m.get("setup_vol_ratio")
    if m["setup"] == "breakout":
        vol_s = _clip01((vr - 1.0) / 1.5)
        tight_s = _clip01((0.8 - m["contraction"]) / 0.5)
    elif m["setup"] == "pullback":
        vol_s = _clip01((1.2 - vr) / 0.6)
        tight_s = _clip01(1 - abs(m["touch_offset_atr"]) / st["pullback"]["touch_below_atr"])
    else:
        vol_s = tight_s = 0.0
    rmin = cfg["rs"]["min_rank"]
    b21, b63 = m["bench_ret21"], m["bench_ret63"]
    beats = [m["ret21"] > b21 if None not in (m["ret21"], b21) else None,
             m["ret63"] > b63 if None not in (m["ret63"], b63) else None]
    beats = [x for x in beats if x is not None]
    comp = {
        "rs_rank": _clip01(((m["rs_rank"] or rmin) - rmin) / max(100 - rmin, 1)),
        "volume": vol_s,
        "tightness": tight_s,
        "close_location": _clip01((m.get("setup_close_loc", m["close_location"]) - 0.5) / 0.4),
        "near_high": _clip01(1 - m["below_high_pct"] / tr["max_below_52w_high_pct"]),
        "beats_benchmark": sum(beats) / len(beats) if beats else 0.5,
        "room": 1.0 if math.isinf(m["room_r"]) else _clip01((m["room_r"] - g["min_room_r"]) / 2),
        "delivery": 0.5 if delivery_pct is None or np.isnan(delivery_pct)
        else _clip01((delivery_pct - 25) / 30),
    }
    w = sc["weights"]
    score = sum(comp[k] * w.get(k, 0) for k in comp) / (sum(w.values()) or 1) * 100
    return {"rejected": bool(R), "reject_reasons": R, "flags": flags, "score": round(score, 1),
            "components": {k: round(v, 2) for k, v in comp.items()}, "delivery_pct": delivery_pct}


def select(rows: list[dict], cfg: dict) -> tuple[list[dict], list[dict]]:
    """rows passed every gate. Sort by score, cap per sector, keep top N."""
    sel = cfg["selection"]
    picks, dropped, per_sector = [], [], {}
    for r in sorted(rows, key=lambda r: -r["score"]):
        sec = r.get("industry", "Unknown")
        if r["score"] < sel["min_score"]:
            dropped.append({"symbol": r["symbol"], "reasons": [f"score_{r['score']}<{sel['min_score']}"]})
        elif per_sector.get(sec, 0) >= sel["max_per_sector"]:
            dropped.append({"symbol": r["symbol"], "reasons": ["sector_cap"]})
        elif len(picks) >= sel["top_n"]:
            dropped.append({"symbol": r["symbol"], "reasons": ["outside_top_n"]})
        else:
            picks.append(r)
            per_sector[sec] = per_sector.get(sec, 0) + 1
    return picks, dropped
