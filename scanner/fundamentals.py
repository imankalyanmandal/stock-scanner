"""Return on equity from Yahoo, cached per company.

ROE moves slowly and is only needed for stocks that broke out, so values are fetched on demand
and reused for `screen.roe_max_age_days`. If a refresh fails, the last known value is kept and
marked stale. A stock with no value at all stays "unknown"; it is never treated as a fail.
"""
import json
import logging
import math
import time
from datetime import date
from pathlib import Path
from typing import Callable

import yfinance as yf

from .data import to_yf

log = logging.getLogger(__name__)


def _finite(v) -> float | None:
    try:
        v = float(v)
    except (TypeError, ValueError):
        return None
    return v if math.isfinite(v) else None


def roe_from_statements(income, balance) -> float | None:
    """Latest annual net income / year-end equity, in %. None if either is missing or equity <= 0."""
    if income is None or balance is None or income.empty or balance.empty:
        return None
    equity_rows = [r for r in ("Stockholders Equity", "Common Stock Equity") if r in balance.index]
    if "Net Income" not in income.index or not equity_rows:
        return None
    for col in sorted(set(income.columns) & set(balance.columns), reverse=True):
        ni = _finite(income.at["Net Income", col])
        eq = _finite(balance.at[equity_rows[0], col])
        if ni is not None and eq is not None and eq > 0:
            return ni / eq * 100
    return None


def yahoo_roe(symbol: str) -> tuple[float, str] | None:
    """(ROE %, source), or None if Yahoo has nothing usable. Yahoo reports ROE as a fraction."""
    t = yf.Ticker(to_yf(symbol))
    try:
        v = _finite((t.info or {}).get("returnOnEquity"))
        if v is not None:
            return v * 100, "yahoo_info"
    except Exception as e:  # yfinance raises assorted errors
        log.debug("info %s failed: %s", symbol, e)
    try:
        v = roe_from_statements(t.income_stmt, t.balance_sheet)
        if v is not None:
            return v, "yahoo_annual"
    except Exception as e:
        log.debug("statements %s failed: %s", symbol, e)
    return None


def _load(path: Path) -> dict:
    try:
        return json.loads(path.read_text())
    except (OSError, ValueError):
        return {}


def get_roe(symbols: list[str], cfg: dict, fetch: bool = True, today: date | None = None,
            fetcher: Callable[[str], tuple[float, str] | None] | None = None) -> dict[str, dict]:
    """symbol -> {roe_pct, source, fetched, stale}. Symbols with no value are left out."""
    sc = cfg["screen"]
    fetcher = fetcher or yahoo_roe
    today = today or date.today()
    path = Path(sc["roe_cache"])
    cache = _load(path)
    out, changed = {}, False
    for i, s in enumerate(symbols):
        old = cache.get(s)
        age = (today - date.fromisoformat(old["fetched"])).days if old else None
        if old and age <= sc["roe_max_age_days"]:
            out[s] = {**old, "stale": False}
            continue
        got = None
        if fetch:
            if i:
                time.sleep(sc.get("request_delay_sec", 0))
            try:
                got = fetcher(s)
            except Exception as e:
                log.warning("ROE fetch failed for %s: %s", s, e)
        if got:
            cache[s] = {"roe_pct": round(got[0], 2), "source": got[1], "fetched": today.isoformat()}
            out[s] = {**cache[s], "stale": False}
            changed = True
        elif old:
            out[s] = {**old, "stale": True}
    if changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(dict(sorted(cache.items())), indent=1) + "\n")
    return out
