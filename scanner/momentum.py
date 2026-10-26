"""Monthly factor rotation backtest: momentum, low volatility, or a blend of the two.

At each month-end close: rank the universe by the NSE-style momentum score
(filters.momentum_scores). Next session's open: hold the top `top_n` in equal weight,
keeping existing holdings while they rank within `exit_rank`. With `regime_filter`,
go fully to cash when Nifty 50 closed below its 200 DMA at month end.
Costs are charged per side on traded value; idle cash earns `cash_yield_pct`.
"""
import math

import numpy as np
import pandas as pd

from .filters import momentum_scores
from .indicators import sma


def _series_stats(eq: pd.Series) -> dict:
    eq = eq.dropna()
    if len(eq) < 2:
        return {}
    years = (eq.index[-1] - eq.index[0]).days / 365.25
    r = eq.pct_change().dropna()
    dd = eq / eq.cummax() - 1
    yearly = eq.groupby(eq.index.year).last()
    first = eq.iloc[0]
    yr = (yearly / yearly.shift(1).fillna(first) - 1) * 100
    cagr = (eq.iloc[-1] / first) ** (1 / years) - 1 if years > 0 else float("nan")
    vol = r.std() * math.sqrt(252)
    return {"cagr_pct": round(cagr * 100, 1), "vol_pct": round(vol * 100, 1),
            "max_drawdown_pct": round(float(dd.min()) * 100, 1),
            "return_over_vol": round(cagr / vol, 2) if vol else None,
            "total_return_pct": round((eq.iloc[-1] / first - 1) * 100, 1),
            "worst_year_pct": round(float(yr.min()), 1),
            "yearly_pct": {int(k): round(float(v), 1) for k, v in yr.items()}}


def sleeves_from(mc: dict) -> list[dict]:
    """Portfolio sleeves. Default: one sleeve using `momentum.score` (momentum)."""
    raw = mc.get("sleeves") or [{"score": mc.get("score", "momentum"), "weight": 1.0}]
    total = sum(float(x.get("weight", 1.0)) for x in raw) or 1.0
    return [{"score": x.get("score", "momentum"), "weight": float(x.get("weight", 1.0)) / total,
             "top_n": int(x.get("top_n", mc["top_n"])), "exit_rank": int(x.get("exit_rank", mc["exit_rank"])),
             "regime_filter": bool(x.get("regime_filter", mc.get("regime_filter", False)))} for x in raw]


def score_tables(feats: dict[str, pd.DataFrame], cfg: dict, kinds: set[str]) -> dict[str, pd.DataFrame]:
    """dates x symbols ranking scores (higher = better) for each requested kind.

    momentum : NSE-style z(6m ret/vol) + z(12m ret/vol)
    low_vol  : minus 1-year volatility of daily returns (NSE Low Volatility index style)
    blend    : z(momentum) * w + z(low_vol) * (1 - w), w = momentum.blend_momentum_weight
    """
    mom = momentum_scores(feats, cfg)                       # NaN where illiquid / no history
    vol = pd.DataFrame({s: f["vol252"] for s, f in feats.items()}).reindex(mom.index)
    low = (-vol).where(mom.notna())

    def z(df):
        return df.sub(df.mean(axis=1), axis=0).div(df.std(axis=1), axis=0)
    w = cfg["momentum"].get("blend_momentum_weight", 0.5)
    out = {}
    for k in kinds:
        if k == "momentum":
            out[k] = mom
        elif k == "low_vol":
            out[k] = low
        elif k == "blend":
            out[k] = w * z(mom) + (1 - w) * z(low)
        else:
            raise ValueError(f"unknown score {k!r}")
    return out


def run_momentum(feats: dict[str, pd.DataFrame], benchmarks: dict[str, pd.DataFrame],
                 cfg: dict, start: str | None = None) -> dict:
    mc = cfg["momentum"]
    warmup = cfg["backtest"]["warmup_bars"]
    sleeves = sleeves_from(mc)
    tables = score_tables(feats, cfg, {sl["score"] for sl in sleeves})
    scores = next(iter(tables.values()))
    close = pd.DataFrame({s: f["close"] for s, f in feats.items()}).sort_index().ffill(limit=5)
    open_ = pd.DataFrame({s: f["open"] for s, f in feats.items()}).reindex(close.index)
    dates = close.index
    nifty = benchmarks["nifty50"]["Close"].astype(float).reindex(dates).ffill()
    nifty200 = sma(nifty, 200)

    per = pd.Series(dates.to_period("M"), index=dates)
    is_end = per != per.shift(-1)
    sig_dates = [d for d in dates[warmup:-1] if is_end[d] and scores.loc[d].notna().sum() >= min(10, mc["top_n"])]
    if start:
        sig_dates = [d for d in sig_dates if d >= pd.Timestamp(start)]
    if not sig_dates:
        raise RuntimeError("Not enough history for the momentum backtest")
    exec_for = {dates[dates.get_loc(d) + 1]: d for d in sig_dates}

    side = mc["cost_pct"] / 200
    cash_r = (1 + mc.get("cash_yield_pct", 0) / 100) ** (1 / 252) - 1
    cash, shares = 1.0, {}
    held = [set() for _ in sleeves]          # what each sleeve currently owns
    eq, log = {}, []
    first = dates.get_loc(sig_dates[0]) + 1
    for d in dates[first:]:
        cash *= 1 + cash_r
        if d in exec_for:
            sig = exec_for[d]
            below200 = bool(nifty[sig] < nifty200[sig])
            weights: dict[str, float] = {}
            in_cash = True
            for k, sl in enumerate(sleeves):
                if sl["regime_filter"] and below200:
                    held[k] = set()
                    continue
                in_cash = False
                ranked = tables[sl["score"]].loc[sig].dropna().sort_values(ascending=False)
                pos = {s: n + 1 for n, s in enumerate(ranked.index)}
                keep = sorted([s for s in held[k] if pos.get(s, 1e9) <= sl["exit_rank"]],
                              key=lambda s: pos[s])[:sl["top_n"]]
                new = [s for s in ranked.index if s not in keep][:sl["top_n"] - len(keep)]
                held[k] = set(keep + new)
                for s in held[k]:
                    weights[s] = weights.get(s, 0.0) + sl["weight"] / len(held[k])
            target = list(weights)

            def px(s):
                p = open_.at[d, s]
                return p if np.isfinite(p) else close[s].loc[:d].iloc[-2]
            value = cash + sum(n * px(s) for s, n in shares.items())
            traded = 0.0
            for s in list(shares):                       # sells and trims first
                p = px(s)
                cur = shares[s] * p
                want = value * weights.get(s, 0.0)
                if cur > want:
                    sell = cur - want
                    cash += sell * (1 - side)
                    traded += sell
                    shares[s] = want / p
                    if shares[s] <= 0:
                        del shares[s]
            for s in target:                             # then buys
                p = px(s)
                if not np.isfinite(p) or p <= 0:
                    continue
                buy = value * weights[s] - shares.get(s, 0.0) * p
                if buy <= 0:
                    continue
                buy = min(buy, cash / (1 + side))
                shares[s] = shares.get(s, 0.0) + buy / p
                cash -= buy * (1 + side)
                traded += buy
            log.append({"date": d.date().isoformat(), "holdings": len(shares), "in_cash": in_cash,
                        "turnover_pct": round(100 * traded / value, 1) if value else 0.0})
        eq[d] = cash + sum(n * close.at[d, s] for s, n in shares.items()
                           if np.isfinite(close.at[d, s]))

    equity = pd.Series(eq)
    out = {"strategy": _series_stats(equity),
           "rebalances": len(log),
           "months_in_cash_pct": round(100 * sum(x["in_cash"] for x in log) / len(log), 1),
           "avg_turnover_pct": round(float(np.mean([x["turnover_pct"] for x in log])), 1),
           "period": [equity.index[0].date().isoformat(), equity.index[-1].date().isoformat()],
           "benchmarks": {}}
    for k, df in benchmarks.items():
        b = df["Close"].astype(float).reindex(equity.index).ffill()
        out["benchmarks"][k] = _series_stats(b / b.iloc[0])
    out["equity_monthly"] = {d.date().isoformat(): round(float(v), 4)
                             for d, v in equity.groupby(equity.index.to_period("M")).tail(1).items()}
    return out
