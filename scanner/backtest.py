"""Walk-forward backtest of the exact scan rules.

Each day: regime -> prefilter -> metrics_at/evaluate -> select -> simulate the trade on later bars.
Respects max_open_positions and never holds the same symbol twice.

Known limits (also printed in the report):
  - survivorship bias: the universe is today's index constituents
  - earnings, delivery %, sector flags and the manual exclusion list are not applied historically
  - daily bars only: if a bar touches both the stop and the target, the stop is assumed first
"""
import html
import json
import logging
import math
from pathlib import Path

import pandas as pd

from .filters import arrays, evaluate, metrics_at, prefilter, rs_rank_table, select
from .market import bench_table, breadth_series, regime_at
from .pipeline import load_market_data
from .trade import simulate, trade_stats
from .universe import load_universe

log = logging.getLogger(__name__)
LIMITS = ["Survivorship bias: uses today's index constituents for the whole period.",
          "Earnings, delivery %, sector flags and the exclusion list are not applied historically.",
          "Daily bars: when a bar hits both stop and target, the stop is assumed to hit first.",
          "Results are per-trade R, net of cost_pct; equity assumes risk_per_trade_pct x regime size."]


def run_backtest(cfg: dict, limit: int | None = None, start: str | None = None,
                 end: str | None = None, universe: pd.DataFrame | None = None,
                 write: bool = True) -> dict:
    if universe is None:
        universe = load_universe(cfg["universe"]["indexes"], cfg["output"]["data_dir"])
    if limit:
        universe = universe.head(limit)
    symbols = universe["symbol"].tolist()
    industry = universe.set_index("symbol")["industry"].to_dict()
    feats, benchmarks, vix = load_market_data(cfg, symbols, cfg["backtest"]["period"])
    if not feats:
        raise RuntimeError("No usable price data")
    log.info("Backtest on %d symbols", len(feats))

    ranks = rs_rank_table(feats, cfg)
    breadth = breadth_series(feats)
    btabs = {k: bench_table(df) for k, df in benchmarks.items()}
    min_rank = cfg["rs"]["min_rank"]
    A = {s: arrays(f) for s, f in feats.items()}
    pos = {s: {ts: i for i, ts in enumerate(f.index)} for s, f in feats.items()}
    pre = pd.DataFrame({s: prefilter(f, cfg) & (ranks[s].reindex(f.index) >= min_rank)
                        for s, f in feats.items()}).fillna(False).astype(bool)

    dates = pre.index.sort_values()
    dates = dates[cfg["backtest"]["warmup_bars"]:]
    if start:
        dates = dates[dates >= pd.Timestamp(start)]
    if end:
        dates = dates[dates <= pd.Timestamp(end)]
    if not len(dates):
        raise RuntimeError("Not enough history for the backtest window")

    max_open = cfg["sizing"]["max_open_positions"]
    open_pos, trades = [], []
    days_blocked = signal_days = 0
    regimes = {}
    for d in dates:
        open_pos = [t for t in open_pos if t["exit_ts"] is None or t["exit_ts"] > d]
        cands = pre.columns[pre.loc[d].to_numpy()]
        bench_now = {}
        for k, t in btabs.items():
            sl = t.loc[:d]
            bench_now[k] = sl.iloc[-1].to_dict() if len(sl) else None
        v = vix.loc[:d].iloc[-1] if vix is not None and len(vix.loc[:d]) else None
        reg = regime_at(bench_now, breadth.get(d, math.nan), v, cfg)
        regimes[reg["regime"]] = regimes.get(reg["regime"], 0) + 1
        if not len(cands):
            continue
        rows = []
        for s in cands:
            i = pos[s].get(d)
            if i is None:
                continue
            m = metrics_at(A[s], i, cfg, ranks.at[d, s])
            if m is None:
                continue
            ev = evaluate(m, cfg)
            if not ev["reject_reasons"]:
                rows.append({"symbol": s, "industry": industry.get(s, "Unknown"), "_i": i, **m, **ev})
        if not rows:
            continue
        signal_days += 1
        if not reg["allow_new_longs"]:
            days_blocked += 1
            continue
        picks, _ = select(rows, cfg)
        held = {t["symbol"] for t in open_pos}
        for p in picks:
            if len(open_pos) >= max_open:
                break
            if p["symbol"] in held:
                continue
            sim = simulate(A[p["symbol"]], p["_i"], p, cfg)
            if sim["status"] in ("closed", "open"):
                idx = feats[p["symbol"]].index
                exit_ts = idx[sim["exit_i"]] if sim["status"] == "closed" else None
                open_pos.append({"symbol": p["symbol"], "exit_ts": exit_ts})
                held.add(p["symbol"])
            trades.append({"signal_date": d.date().isoformat(), "symbol": p["symbol"],
                           "industry": p["industry"], "setup": p["setup"], "score": p["score"],
                           "regime": reg["regime"], "size_mult": reg["size_mult"],
                           "trigger": round(p["trigger"], 2), "stop": round(p["stop"], 2),
                           "stop_atr": round(p["stop_atr"], 2),
                           **{k: v for k, v in sim.items() if k != "exit_i"}})

    tdf = pd.DataFrame(trades)
    result = summarize(tdf, cfg)
    result.update({"period": [dates[0].date().isoformat(), dates[-1].date().isoformat()],
                   "symbols": len(feats), "days": len(dates), "signal_days": signal_days,
                   "signal_days_blocked_by_regime": days_blocked, "regime_days": regimes,
                   "limits": LIMITS})
    if write:
        result["files"] = {k: str(v) for k, v in _write(result, tdf, cfg["output"]["dir"]).items()}
    return result


def summarize(tdf: pd.DataFrame, cfg: dict) -> dict:
    if tdf.empty:
        return {"overall": {"trades": 0}, "status_counts": {}, "by_setup": {}, "by_regime": {},
                "by_year": {}, "exit_reasons": {}, "equity": []}
    filled = tdf[tdf["status"].isin(["closed", "open"])].copy()
    closed = tdf[tdf["status"] == "closed"].copy()
    closed["year"] = closed["exit_date"].str[:4]
    risk = cfg["sizing"]["risk_per_trade_pct"]
    eq = closed.sort_values("exit_date")
    eq_pct = (eq["r_multiple"] * eq["size_mult"] * risk).cumsum()
    dd_pct = float((eq_pct - eq_pct.cummax().clip(lower=0)).min()) if len(eq_pct) else 0.0
    overall = trade_stats(closed)
    overall.update({"return_pct_simple": round(float(eq_pct.iloc[-1]), 1) if len(eq_pct) else 0.0,
                    "max_drawdown_pct": round(dd_pct, 1),
                    "fill_rate_pct": round(100 * len(filled) / len(tdf), 1)})
    group = lambda col: {str(k): trade_stats(g) for k, g in closed.groupby(col)}
    return {"overall": overall,
            "status_counts": tdf["status"].value_counts().to_dict(),
            "by_setup": group("setup"), "by_regime": group("regime"), "by_year": group("year"),
            "exit_reasons": closed["exit_reason"].value_counts().to_dict(),
            "equity": [{"date": d, "pct": round(float(p), 2)}
                       for d, p in zip(eq["exit_date"], eq_pct)]}


def _table(title: str, data: dict) -> str:
    if not data:
        return ""
    cols = ["trades", "win_rate_pct", "avg_win_r", "avg_loss_r", "expectancy_r", "total_r",
            "profit_factor", "max_drawdown_r"]
    head = "".join(f"<th>{c.replace('_', ' ')}</th>" for c in cols)
    body = "".join(f"<tr><td>{html.escape(k)}</td>" + "".join(
        f"<td>{v.get(c, '–') if v.get(c) is not None else '–'}</td>" for c in cols) + "</tr>"
        for k, v in data.items())
    return f"<h2>{title}</h2><table><tr><th></th>{head}</tr>{body}</table>"


def _svg(equity: list[dict]) -> str:
    if len(equity) < 2:
        return ""
    ys = [e["pct"] for e in equity]
    lo, hi = min(0, min(ys)), max(0, max(ys))
    span = (hi - lo) or 1
    W, H = 900, 220
    pts = " ".join(f"{i / (len(ys) - 1) * W:.1f},{H - (y - lo) / span * H:.1f}"
                   for i, y in enumerate(ys))
    zero = H - (0 - lo) / span * H
    return (f"<svg viewBox='0 0 {W} {H}' preserveAspectRatio='none' class=eq>"
            f"<line x1=0 x2={W} y1={zero:.1f} y2={zero:.1f} class=zero />"
            f"<polyline points='{pts}' class=line /></svg>"
            f"<p class=m>{equity[0]['date']} → {equity[-1]['date']} · "
            f"final {ys[-1]:+.1f}% · range {lo:.1f}% to {hi:.1f}%</p>")


def _write(res: dict, tdf: pd.DataFrame, out_dir: str) -> dict:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    stem = f"backtest_{res['period'][0]}_{res['period'][1]}"
    paths = {"json": d / f"{stem}.json", "csv": d / f"{stem}_trades.csv", "html": d / f"{stem}.html"}
    paths["json"].write_text(json.dumps({k: v for k, v in res.items() if k != "files"},
                                        indent=2, default=str))
    tdf.to_csv(paths["csv"], index=False)
    o = res["overall"]
    kpis = "".join(f"<div class=k><span>{html.escape(k.replace('_', ' '))}</span><b>{v}</b></div>"
                   for k, v in o.items())
    misc = "".join(f"<li>{html.escape(str(k))}: {v}</li>" for k, v in
                   {**res["status_counts"], **{f"exit {k}": v for k, v in res["exit_reasons"].items()}}.items())
    lim = "".join(f"<li>{html.escape(x)}</li>" for x in res["limits"])
    paths["html"].write_text(f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1"><title>Backtest {res['period'][0]} to {res['period'][1]}</title>
<style>
:root{{--bg:#fafaf8;--fg:#1d1d1b;--m:#6b6b66;--card:#fff;--line:#e4e2dc;--acc:#1f6f50}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151514;--fg:#ecebe6;--m:#9a9a93;--card:#1f1f1d;--line:#33332f;--acc:#5fc29a}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,sans-serif;margin:auto;padding:16px;max-width:1000px}}
.kp{{display:grid;grid-template-columns:repeat(auto-fill,minmax(150px,1fr));gap:8px}}
.k{{background:var(--card);border:1px solid var(--line);border-radius:8px;padding:8px}} .k span{{display:block;color:var(--m);font-size:12px}}
table{{border-collapse:collapse;font-size:12px;display:block;overflow-x:auto}} th,td{{padding:4px 8px;border-bottom:1px solid var(--line);text-align:right}}
.m{{color:var(--m);font-size:12px}} .eq{{width:100%;height:220px;background:var(--card);border:1px solid var(--line);border-radius:8px}}
.line{{fill:none;stroke:var(--acc);stroke-width:2;vector-effect:non-scaling-stroke}} .zero{{stroke:var(--m);stroke-dasharray:4;vector-effect:non-scaling-stroke}}
</style></head><body>
<h1>Backtest {res['period'][0]} → {res['period'][1]}</h1>
<p class=m>{res['symbols']} symbols · {res['days']} sessions · {res['signal_days']} days with signals
({res['signal_days_blocked_by_regime']} blocked by regime) · regime days {res['regime_days']}</p>
<div class=kp>{kpis}</div>
<h2>Equity (% of capital, simple sum)</h2>{_svg(res['equity'])}
{_table('By setup', res['by_setup'])}{_table('By regime', res['by_regime'])}{_table('By exit year', res['by_year'])}
<h2>Trade outcomes</h2><ul>{misc}</ul>
<h2>Limits</h2><ul class=m>{lim}</ul>
</body></html>""")
    return paths
