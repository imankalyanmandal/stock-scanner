"""Daily scan: universe -> prices -> journal update -> regime -> gates/score -> events -> picks."""
import logging
import math
from datetime import date

import pandas as pd

from . import journal as J
from . import report
from .data import download_prices, fetch_delivery
from .enrich import fetch_news, upcoming_events
from .filters import (arrays, evaluate, features, metrics_at, momentum_rank_table, rs_rank_table,
                      select)
from .market import bench_table, breadth_series, regime_at, sector_stats
from .trade import size_position
from .universe import load_exclusions, load_universe

log = logging.getLogger(__name__)


def market_symbols(cfg: dict) -> list[str]:
    mk = cfg["market"]
    return list(mk["benchmarks"].values()) + ([mk["vix_symbol"]] if mk.get("vix_symbol") else [])


def load_market_data(cfg: dict, symbols: list[str], period: str, prices: dict | None = None):
    """Download prices (unless given) and build features. Returns (feats, benchmarks, vix_close)."""
    mk = cfg["market"]
    bench_syms = mk["benchmarks"]
    vix_sym = mk.get("vix_symbol")
    prices = dict(prices) if prices is not None else \
        download_prices(symbols + market_symbols(cfg), period=period)
    benchmarks = {k: prices.pop(v) for k, v in bench_syms.items() if v in prices}
    vix_df = prices.pop(vix_sym, None) if vix_sym else None
    if "nifty50" not in benchmarks:
        raise RuntimeError("Nifty 50 benchmark data missing; cannot judge the market")
    rs_key = mk.get("rs_benchmark", "nifty50")
    rs_close = benchmarks.get(rs_key, benchmarks["nifty50"])["Close"]
    feats = {}
    for s in symbols:
        if s in prices:
            f = features(prices[s], cfg, rs_close)
            if f is not None:
                feats[s] = f
    vix = vix_df["Close"].astype(float) if vix_df is not None and len(vix_df) else None
    return feats, benchmarks, vix


def _bench_at(benchmarks: dict, ts) -> dict:
    out = {}
    for k, df in benchmarks.items():
        t = bench_table(df).loc[:ts]
        out[k] = t.iloc[-1].to_dict() if len(t) else None
    return out


def _vix_at(vix, ts):
    if vix is None:
        return None
    v = vix.loc[:ts]
    return float(v.iloc[-1]) if len(v) else None


def exit_plan_text(p: dict, cfg: dict) -> str:
    ex = cfg["exits"]
    return (f"Buy-stop {p['trigger']:.2f} (skip if it opens above {p['max_entry']:.2f}). "
            f"Stop {p['stop']:.2f}. Book {ex['partial_pct']}% at {p['t1']:.2f} "
            f"({ex['partial_r']:g}R), move stop to entry, trail the rest on a close below "
            f"{ex['trail_ma'].upper()}. Exit if not +{ex['time_stop_min_r']:g}R after "
            f"{ex['time_stop_days']} sessions.")


def run(cfg: dict, with_news: bool = True, limit: int | None = None, force: bool = False) -> dict:
    out_cfg, jcfg = cfg["output"], cfg["journal"]
    universe = load_universe(cfg["universe"]["indexes"], out_cfg["data_dir"])
    if limit:
        universe = universe.head(limit)
    symbols = universe["symbol"].tolist()
    excluded = load_exclusions(cfg["universe"].get("exclude_file"))
    log.info("Universe: %d stocks (%d on the exclusion list)", len(symbols), len(excluded))

    feats, benchmarks, vix = load_market_data(cfg, symbols, cfg["data"]["period"])
    if not feats:
        raise RuntimeError("No usable price data")
    last_ts = max(f.index[-1] for f in feats.values())
    session = pd.Timestamp(last_ts).date().isoformat()

    # journal: replay open trades with the new prices before adding today's picks
    jdf = J.update(J.load(jcfg["path"]), feats, cfg)
    duplicate = (not force) and J.last_session(jcfg["state_path"]) == session

    regime = regime_at(_bench_at(benchmarks, last_ts),
                       breadth_series(feats).get(last_ts, math.nan), _vix_at(vix, last_ts), cfg)
    log.info("Session %s | regime=%s breadth50=%.1f%% vix=%s size=%.2f", session,
             regime["regime"], regime["breadth_pct"], regime["vix"], regime["size_mult"])

    ranks = rs_rank_table(feats, cfg)
    rank_today = ranks.loc[last_ts] if last_ts in ranks.index else pd.Series(dtype=float)
    moms = momentum_rank_table(feats, cfg)
    mom_today = moms.loc[last_ts] if last_ts in moms.index else pd.Series(dtype=float)
    info = universe.set_index("symbol")
    current = {s: f for s, f in feats.items() if f.index[-1] == last_ts}
    regime["sectors"] = sector_stats([
        {"industry": info.at[s, "industry"],
         "above50": bool(f["close"].iloc[-1] > f["sma50"].iloc[-1])} for s, f in current.items()])

    delivery = fetch_delivery(date.fromisoformat(session))
    passed, rejected = [], []
    for s in symbols:
        if s not in feats:
            rejected.append({"symbol": s, "reasons": ["no_data_or_short_history"]})
            continue
        if s not in current:
            rejected.append({"symbol": s, "reasons": ["stale_data"]})
            continue
        f = current[s]
        m = metrics_at(arrays(f), len(f) - 1, cfg, rank_today.get(s), mom_today.get(s))
        if m is None:
            rejected.append({"symbol": s, "reasons": ["insufficient_history"]})
            continue
        ind = info.at[s, "industry"]
        ev = evaluate(m, cfg, regime["sectors"].get(ind), delivery.get(s), excluded.get(s.upper()))
        row = {"symbol": s, "company": info.at[s, "company"], "industry": ind,
               "indexes": info.at[s, "indexes"], **m, **ev}
        if ev["reject_reasons"]:
            rejected.append({"symbol": s, "reasons": ev["reject_reasons"]})
        else:
            passed.append(row)

    # event risk on the survivors (network-heavy, so only here)
    ecfg = cfg["events"]
    ref = date.fromisoformat(session)
    survivors = []
    for p in passed:
        p["events"] = upcoming_events(p["symbol"], ref,
                                      max(ecfg["earnings_window_days"], ecfg["dividend_flag_days"]))
        days = {e["type"]: (date.fromisoformat(e["date"]) - ref).days for e in p["events"]}
        if "earnings" in days and days["earnings"] <= ecfg["earnings_window_days"]:
            if ecfg.get("reject_earnings", True):
                rejected.append({"symbol": p["symbol"], "reasons": ["earnings_in_hold_window"]})
                continue
            p["flags"].append("earnings_soon")
        if days.get("ex_dividend", 99) <= ecfg["dividend_flag_days"]:
            p["flags"].append("ex_dividend_soon")
        survivors.append(p)

    notes, watchlist = [], []
    if not regime["allow_new_longs"]:
        picks = []
        watchlist = sorted(survivors, key=lambda r: -r["score"])[:cfg["selection"]["top_n"]]
        rejected += [{"symbol": p["symbol"], "reasons": ["regime_block"]} for p in survivors]
        notes.append(f"No new longs today ({regime['block_reason']}). "
                     "Setups shown as a watchlist only.")
    else:
        picks, dropped = select(survivors, cfg)
        rejected += dropped

    for p in picks:
        p.update(size_position(p["trigger"], p["stop"], cfg, regime["size_mult"]))
        p["exit_plan"] = exit_plan_text(p, cfg)
        if with_news and cfg["news"]["enabled"]:
            n = cfg["news"]
            p["news"] = fetch_news(p["company"], p["symbol"], n["lookback_days"],
                                   n["fresh_hours"], n["max_items"])
        else:
            p["news"] = {"has_news": None, "fresh": None, "items": []}

    if not duplicate:
        jdf = J.add_picks(jdf, picks, session, regime)
    J.save(jdf, jcfg["path"])
    J.mark_session(jcfg["state_path"], session)

    age = (date.today() - ref).days
    if age > 4:
        notes.append(f"Latest price data is from {session} ({age} days old). "
                     "The data source may be lagging.")
    if duplicate:
        notes.append("Same session as the previous run; picks were not added to the journal again.")

    result = {"session": session, "regime": regime, "picks": picks, "watchlist": watchlist,
              "rejected": rejected, "universe_size": len(symbols), "evaluated": len(current),
              "passed_gates": len(passed), "min_score": cfg["selection"]["min_score"],
              "duplicate": duplicate, "note": " ".join(notes),
              "journal": J.summary(jdf)}
    report.write(result, out_cfg["dir"])
    log.info("Done: %d picks, %d passed gates, %d rejected", len(picks), len(passed), len(rejected))
    return result
