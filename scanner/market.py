"""Market regime gate: benchmark trends + universe breadth + sector breadth."""
import pandas as pd

from .indicators import sma


def benchmark_trend(df: pd.DataFrame) -> dict:
    c = df["Close"].astype(float)
    s20, s50 = sma(c, 20).iloc[-1], sma(c, 50).iloc[-1]
    close = float(c.iloc[-1])
    return {"close": close, "above_20": bool(close > s20), "above_50": bool(close > s50),
            "sma20": float(s20), "sma50": float(s50)}


def market_regime(benchmarks: dict[str, pd.DataFrame], metrics: dict[str, dict],
                  universe: pd.DataFrame, cfg: dict) -> dict:
    mc = cfg["market"]
    trends = {name: benchmark_trend(df) for name, df in benchmarks.items() if len(df) >= 50}
    above = [m["above_sma20"] for m in metrics.values()]
    breadth = 100 * sum(above) / len(above) if above else 0.0

    ind = universe.set_index("symbol")["industry"]
    rows = [{"industry": ind.get(s, "Unknown"), "above": m["above_sma20"],
             "ret5": m["ret5"], "ret20": m["ret20"]} for s, m in metrics.items()]
    sec = pd.DataFrame(rows)
    sectors = {}
    if not sec.empty:
        for name, grp in sec.groupby("industry"):
            sectors[name] = {"breadth_pct": 100 * grp["above"].mean(),
                             "ret5_med": float(grp["ret5"].median()),
                             "ret20_med": float(grp["ret20"].median()),
                             "count": int(len(grp))}

    all_above_50 = all(t["above_50"] for t in trends.values()) if trends else False
    all_above_20 = all(t["above_20"] for t in trends.values()) if trends else False
    nifty_below_50 = not trends.get("nifty50", {}).get("above_50", True)
    if nifty_below_50 or breadth < mc["breadth_weak_pct"]:
        regime = "weak"
    elif all_above_20 and all_above_50 and breadth >= mc["breadth_strong_pct"]:
        regime = "strong"
    else:
        regime = "neutral"
    return {"regime": regime, "breadth_pct": round(breadth, 1),
            "benchmarks": trends, "sectors": sectors}
