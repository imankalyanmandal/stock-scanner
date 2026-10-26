"""Run several rule variants on one price download and compare them side by side.

Variants are config overrides (deep-merged into config.yaml) listed in experiments.yaml.
Each breakout variant also reports expectancy for the first and second half of the
period separately: a change that only helps in one half is probably noise.
"""
import copy
import json
import logging
from datetime import date
from pathlib import Path

import pandas as pd

from .backtest import run_backtest
from .data import download_prices
from .momentum import run_momentum
from .pipeline import load_market_data, market_symbols
from .trade import trade_stats
from .filters import liquidity_requirements
from .universe import load_universe

log = logging.getLogger(__name__)


def deep_merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        out[k] = deep_merge(out[k], v) if isinstance(v, dict) and isinstance(out.get(k), dict) else v
    return out


def _subset(universe: pd.DataFrame, cfg: dict) -> pd.DataFrame:
    """Rows of the downloaded universe that belong to this variant's `universe.indexes`."""
    keys = set(cfg["universe"]["indexes"])
    mask = universe["indexes"].astype(str).str.split(",").apply(lambda xs: bool(keys & {x.strip() for x in xs}))
    return universe[mask]


def _halves(tdf: pd.DataFrame, period: list[str]) -> tuple[dict, dict]:
    closed = tdf[tdf["status"] == "closed"] if not tdf.empty else tdf
    if closed.empty:
        return {"trades": 0}, {"trades": 0}
    mid = pd.Timestamp(period[0]) + (pd.Timestamp(period[1]) - pd.Timestamp(period[0])) / 2
    sd = pd.to_datetime(closed["signal_date"])
    return trade_stats(closed[sd < mid]), trade_stats(closed[sd >= mid])


def run_experiments(cfg: dict, spec: dict, universe: pd.DataFrame | None = None,
                    prices: dict | None = None, limit: int | None = None,
                    out_dir: str | None = None) -> dict:
    period = spec.get("period", cfg["backtest"]["period"])
    if universe is None:
        universe = load_universe(cfg["universe"]["indexes"], cfg["output"]["data_dir"])
    if limit:
        universe = universe.head(limit)
    symbols = universe["symbol"].tolist()
    if prices is None:
        prices = download_prices(symbols + market_symbols(cfg), period=period)
    cfg = deep_merge(cfg, {"backtest": {"period": period}})

    breakout, errors = {}, {}
    for name, over in (spec.get("breakout_variants") or {}).items():
        log.info("Breakout variant %s", name)
        try:
            vcfg = deep_merge(cfg, over)
            res = run_backtest(vcfg, universe=_subset(universe, vcfg), prices=prices, write=False)
        except Exception as e:  # one broken variant shouldn't sink the whole run
            log.exception("variant %s failed", name)
            errors[name] = f"{type(e).__name__}: {e}"
            continue
        tdf = res.pop("trades_df")
        h1, h2 = _halves(tdf, res["period"])
        breakout[name] = {"overrides": over, "period": res["period"], "overall": res["overall"],
                          "by_setup": res["by_setup"], "first_half": h1, "second_half": h2,
                          "exit_reasons": res["exit_reasons"], "by_year": res["by_year"]}

    momentum = {}
    if spec.get("momentum_variants"):
        feats, benchmarks, _ = load_market_data(cfg, symbols, period, prices,
                                                liquidity_requirements(universe, cfg))
        for name, over in spec["momentum_variants"].items():
            log.info("Momentum variant %s", name)
            try:
                vcfg = deep_merge(cfg, over)
                keep = set(_subset(universe, vcfg)["symbol"])
                momentum[name] = {"overrides": over, "stocks": len(keep),
                                  **run_momentum({s: f for s, f in feats.items() if s in keep},
                                                 benchmarks, vcfg)}
            except Exception as e:
                log.exception("variant %s failed", name)
                errors[name] = f"{type(e).__name__}: {e}"

    result = {"run_date": date.today().isoformat(), "period_setting": period,
              "symbols": len(symbols), "breakout": breakout, "momentum": momentum,
              "errors": errors}
    d = Path(out_dir or cfg["output"]["dir"])
    d.mkdir(parents=True, exist_ok=True)
    tag = spec.get("name") or ("blend" if not spec.get("breakout_variants") else "")
    stem = d / f"experiments_{result['run_date']}{'_' + tag if tag else ''}"
    stem.with_suffix(".json").write_text(json.dumps(result, indent=2, default=str))
    md = to_markdown(result)
    stem.with_suffix(".md").write_text(md)
    result["files"] = {"md": str(stem.with_suffix(".md")), "json": str(stem.with_suffix(".json"))}
    result["markdown"] = md
    return result


def _v(x, suffix=""):
    return "–" if x is None else f"{x}{suffix}"


def to_markdown(r: dict) -> str:
    L = [f"# Strategy experiments ({r['run_date']})", "",
         f"{r['symbols']} stocks, data period {r['period_setting']}. Same prices for every variant.", ""]
    if r["breakout"]:
        any_p = next(iter(r["breakout"].values()))["period"]
        L += [f"## Swing variants ({any_p[0]} to {any_p[1]})", "",
              "Expectancy is average R per trade after costs. **1st / 2nd half** splits the period "
              "in two: a real improvement should show up in both.", "",
              "| Variant | Trades | Win % | Avg win R | Avg loss R | Expectancy R | 1st half | 2nd half | Profit factor | Return % (1% risk) | Max DD % |",
              "|---|---|---|---|---|---|---|---|---|---|---|"]
        for name, b in r["breakout"].items():
            o, h1, h2 = b["overall"], b["first_half"], b["second_half"]
            L.append(f"| {name} | {o.get('trades', 0)} | {_v(o.get('win_rate_pct'))} | "
                     f"{_v(o.get('avg_win_r'))} | {_v(o.get('avg_loss_r'))} | **{_v(o.get('expectancy_r'))}** | "
                     f"{_v(h1.get('expectancy_r'))} ({h1.get('trades', 0)}) | "
                     f"{_v(h2.get('expectancy_r'))} ({h2.get('trades', 0)}) | {_v(o.get('profit_factor'))} | "
                     f"{_v(o.get('return_pct_simple'))} | {_v(o.get('max_drawdown_pct'))} |")
        L += ["", "Variant settings:", ""]
        L += [f"- **{n}**: `{json.dumps(b['overrides'])}`" for n, b in r["breakout"].items()]
        L.append("")
    if r["momentum"]:
        first = next(iter(r["momentum"].values()))
        L += [f"## Monthly momentum rotation ({first['period'][0]} to {first['period'][1]})", "",
              "| Strategy | CAGR % | Volatility % | Max drawdown % | Worst year % | Return / vol | Months in cash % | Avg turnover % |",
              "|---|---|---|---|---|---|---|---|"]
        for name, m in r["momentum"].items():
            s = m["strategy"]
            L.append(f"| {name} | **{_v(s.get('cagr_pct'))}** | {_v(s.get('vol_pct'))} | "
                     f"{_v(s.get('max_drawdown_pct'))} | {_v(s.get('worst_year_pct'))} | "
                     f"{_v(s.get('return_over_vol'))} | {m['months_in_cash_pct']} | {m['avg_turnover_pct']} |")
        for k, s in first["benchmarks"].items():
            L.append(f"| {k} (buy & hold) | **{_v(s.get('cagr_pct'))}** | {_v(s.get('vol_pct'))} | "
                     f"{_v(s.get('max_drawdown_pct'))} | {_v(s.get('worst_year_pct'))} | "
                     f"{_v(s.get('return_over_vol'))} | – | – |")
        years = sorted({y for m in r["momentum"].values() for y in m["strategy"].get("yearly_pct", {})})
        rows = {n: m["strategy"].get("yearly_pct", {}) for n, m in r["momentum"].items()}
        rows.update({f"{k} B&H": s.get("yearly_pct", {}) for k, s in first["benchmarks"].items()})
        L += ["", "Calendar-year returns (%):", "",
              "| | " + " | ".join(str(y) for y in years) + " |",
              "|---|" + "---|" * len(years)]
        for n, yr in rows.items():
            L.append(f"| {n} | " + " | ".join(_v(yr.get(y, yr.get(str(y)))) for y in years) + " |")
        L += ["", "Variant settings:", ""]
        L += [f"- **{n}**: `{json.dumps(m['overrides'])}`" for n, m in r["momentum"].items()]
        L.append("")
    if r.get("errors"):
        L += ["## Variants that failed", ""] + [f"- **{k}**: {v}" for k, v in r["errors"].items()] + [""]
    L += ["## Read with care", "",
          "- **Survivorship bias:** every variant uses today's index members for the whole period, "
          "which flatters results, more so the further back the test goes.",
          "- **Picking the best of many variants overfits.** Prefer a variant that wins in both halves "
          "and for a reason you can explain.",
          "- Swing results: earnings, delivery % and the exclusion list are not applied historically.",
          "- Momentum results: costs and cash yield included; taxes are not.", ""]
    return "\n".join(L)
