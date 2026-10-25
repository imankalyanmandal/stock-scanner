"""Market regime: benchmark trends + universe breadth + India VIX -> exposure.

The regime decides how much to trade, not just how picky to be:
  strong  -> full size
  neutral -> neutral_size (default half)
  weak    -> no new longs (weak_action: skip) or half size (weak_action: half)
India VIX scales size further, and above vix_extreme blocks new longs.
"""
import math

import pandas as pd

from .indicators import sma


def bench_table(df: pd.DataFrame) -> pd.DataFrame:
    c = df["Close"].astype(float)
    return pd.DataFrame({"close": c, "sma20": sma(c, 20), "sma50": sma(c, 50)})


def breadth_series(feats: dict[str, pd.DataFrame]) -> pd.Series:
    """% of universe closing above its 50 DMA, per date."""
    above = pd.DataFrame({s: (f["close"] > f["sma50"]).where(f["sma50"].notna())
                          for s, f in feats.items()})
    return above.mean(axis=1, skipna=True) * 100


def sector_stats(rows: list[dict]) -> dict:
    """rows: [{industry, above50}] for one date."""
    out = {}
    if not rows:
        return out
    df = pd.DataFrame(rows)
    for name, grp in df.groupby("industry"):
        out[name] = {"breadth_pct": float(100 * grp["above50"].mean()), "count": int(len(grp))}
    return out


def regime_at(benches: dict[str, dict], breadth: float, vix: float | None, cfg: dict) -> dict:
    """benches: {name: {close, sma20, sma50}} for one date."""
    mc = cfg["market"]
    trends = {}
    for name, b in benches.items():
        if b is None or any(not isinstance(b[k], (int, float)) or math.isnan(b[k])
                            for k in ("close", "sma20", "sma50")):
            continue
        trends[name] = {"close": float(b["close"]), "sma20": float(b["sma20"]),
                        "sma50": float(b["sma50"]), "above_20": bool(b["close"] > b["sma20"]),
                        "above_50": bool(b["close"] > b["sma50"])}
    breadth = 0.0 if breadth is None or math.isnan(breadth) else float(breadth)
    nifty_below_50 = not trends.get("nifty50", {}).get("above_50", True)
    if nifty_below_50 or breadth < mc["breadth_weak_pct"]:
        regime = "weak"
    elif trends and all(t["above_20"] and t["above_50"] for t in trends.values()) \
            and breadth >= mc["breadth_strong_pct"]:
        regime = "strong"
    else:
        regime = "neutral"

    size, allow, why = 1.0, True, []
    if regime == "neutral":
        size *= mc["neutral_size"]
    elif regime == "weak":
        if mc.get("weak_action", "skip") == "skip":
            allow = False
            why.append("weak market")
        else:
            size *= 0.5
    vix = None if vix is None or (isinstance(vix, float) and math.isnan(vix)) else float(vix)
    if vix is not None:
        if vix >= mc["vix_extreme"]:
            allow = False
            why.append(f"India VIX {vix:.1f} >= {mc['vix_extreme']}")
        elif vix >= mc["vix_high"]:
            size *= mc["vix_high_size"]
    return {"regime": regime, "breadth_pct": round(breadth, 1), "vix": vix,
            "benchmarks": trends, "size_mult": round(size if allow else 0.0, 3),
            "allow_new_longs": allow, "block_reason": ", ".join(why)}
