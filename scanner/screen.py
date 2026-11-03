"""Breakout quality screen, separate from the swing picks.

Checks every stock with price data, in this order:
  1. volume breakout: close above the prior base high on at least `min_vol_ratio` x average volume
  2. close above the 200 DMA
  3. ROE above `roe_min_pct`

ROE is looked up only for stocks that reach step 3. This is information only: it never changes
the swing picks or the journal, and has no liquidity, regime or top-N limit.
"""
import math

import pandas as pd

from .fundamentals import get_roe


def find_breakouts(feats: dict[str, pd.DataFrame], cfg: dict) -> list[dict]:
    sc = cfg["screen"]
    out = []
    for s, f in feats.items():
        day = int(f["bo_day"].iloc[-1])
        if not 1 <= day <= sc["breakout_max_day"]:
            continue
        bar = f.iloc[-day]          # the breakout bar; volume is judged on that day
        vol = float(bar["vol_ratio"])
        if math.isnan(vol) or vol < sc["min_vol_ratio"]:
            continue
        last = f.iloc[-1]
        out.append({"symbol": s, "close": round(float(last["close"]), 2), "bo_day": day,
                    "base_high": round(float(bar["base_high"]), 2), "vol_ratio": round(vol, 2),
                    "sma200": round(float(last["sma200"]), 2)})
    return out


def run_screen(feats: dict[str, pd.DataFrame], info: pd.DataFrame, cfg: dict,
               excluded: dict[str, str] | None = None, fetch: bool = True, fetcher=None) -> dict:
    """feats: stocks with data for the session. info: universe indexed by symbol."""
    excluded = excluded or {}
    breakouts = find_breakouts(feats, cfg)
    above = [r for r in breakouts if r["close"] > r["sma200"]]
    roe = get_roe([r["symbol"] for r in above], cfg, fetch, fetcher=fetcher)
    passed, unknown = [], []
    for r in above:
        s = r["symbol"]
        r = {**r, "company": info.at[s, "company"], "industry": info.at[s, "industry"],
             "above_200dma_pct": round((r["close"] / r["sma200"] - 1) * 100, 1),
             "excluded": excluded.get(s.upper(), "")}
        v = roe.get(s)
        if v is None:
            unknown.append(r)
            continue
        r.update(roe_pct=v["roe_pct"], roe_source=v["source"], roe_fetched=v["fetched"],
                 roe_stale=v["stale"])
        if v["roe_pct"] > cfg["screen"]["roe_min_pct"]:
            passed.append(r)
    passed.sort(key=lambda r: -r["vol_ratio"])
    unknown.sort(key=lambda r: -r["vol_ratio"])
    return {"checked": len(feats), "breakouts": len(breakouts), "above_200dma": len(above),
            "passed": passed, "roe_unknown": unknown,
            "roe_min_pct": cfg["screen"]["roe_min_pct"]}
