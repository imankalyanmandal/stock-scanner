"""End-to-end scan: universe -> prices -> market gate -> gates/scores -> enrichment -> report."""
import logging
from datetime import date

import pandas as pd

from . import report
from .data import download_prices, fetch_delivery
from .enrich import fetch_news, upcoming_events
from .filters import compute_metrics, evaluate
from .market import market_regime
from .universe import load_universe

log = logging.getLogger(__name__)
SETUP_RANK = {"breakout": 0, "pullback": 1, "none": 2}


def run(cfg: dict, with_news: bool = True, limit: int | None = None) -> dict:
    out_cfg = cfg["output"]
    universe = load_universe(cfg["universe"]["indexes"], out_cfg["data_dir"])
    if limit:
        universe = universe.head(limit)
    symbols = universe["symbol"].tolist()
    log.info("Universe: %d stocks", len(symbols))

    bench_syms = cfg["market"]["benchmarks"]
    prices = download_prices(symbols + list(bench_syms.values()))
    benchmarks = {k: prices.pop(v) for k, v in bench_syms.items() if v in prices}
    if "nifty50" not in benchmarks:
        raise RuntimeError("Nifty 50 benchmark data missing; cannot compute relative strength")
    bench_close = benchmarks["nifty50"]["Close"]

    metrics = {}
    for s in symbols:
        if s in prices:
            m = compute_metrics(prices[s], bench_close, cfg.get("risk"))
            if m:
                metrics[s] = m
    if not metrics:
        raise RuntimeError("No usable price data")

    last_date = max(m["last_date"] for m in metrics.values())
    regime = market_regime(benchmarks, metrics, universe, cfg)
    log.info("Session %s | regime=%s breadth=%.1f%%", last_date, regime["regime"],
             regime["breadth_pct"])

    min_score = cfg["scoring"]["min_score"]
    if regime["regime"] == "weak":
        if cfg["market"]["weak_market_action"] == "skip":
            result = {"session": last_date, "regime": regime, "picks": [], "rejected": [],
                      "note": "Weak market: scan skipped by config"}
            report.write(result, out_cfg["dir"])
            return result
        min_score += cfg["market"]["tighten_min_score_add"]

    delivery = fetch_delivery(date.fromisoformat(last_date))
    info = universe.set_index("symbol")
    picks, rejected = [], []
    for s, m in metrics.items():
        if m["last_date"] != last_date:
            rejected.append({"symbol": s, "reasons": ["stale_data"]})
            continue
        ind = info.at[s, "industry"]
        ev = evaluate(m, cfg, regime["sectors"].get(ind, {}), delivery.get(s))
        row = {"symbol": s, "company": info.at[s, "company"], "industry": ind,
               "indexes": info.at[s, "indexes"], **m, **ev}
        if regime["regime"] == "weak" and m["setup"] == "none":
            ev["reject_reasons"].append("weak_market_no_setup")
        if ev["reject_reasons"]:
            rejected.append({"symbol": s, "reasons": ev["reject_reasons"]})
        elif ev["score"] < min_score:
            rejected.append({"symbol": s, "reasons": [f"score_{ev['score']}<{min_score}"]})
        else:
            picks.append(row)

    today = date.today()
    for p in picks:
        p["events"] = upcoming_events(p["symbol"], today, cfg["events"]["lookahead_days"])
        if p["events"]:
            p["flags"].append("event_soon")
        if with_news and cfg["news"]["enabled"]:
            n = cfg["news"]
            p["news"] = fetch_news(p["company"], p["symbol"], n["lookback_days"],
                                   n["fresh_hours"], n["max_items"])
        else:
            p["news"] = {"has_news": None, "fresh": None, "items": []}

    picks.sort(key=lambda p: (SETUP_RANK[p["setup"]], -p["score"]))
    result = {"session": last_date, "regime": regime, "min_score": min_score,
              "picks": picks, "rejected": rejected, "universe_size": len(symbols),
              "evaluated": len(metrics)}
    report.write(result, out_cfg["dir"])
    log.info("Done: %d picks, %d rejected", len(picks), len(rejected))
    return result
