"""Light use of NSE's official MCP server: a few dozen calls per run, never bulk history.

NSE's firewall blocks heavy use (a GitHub runner got HTTP 403 after ~600 calls in 75 s), and a
block could also cut off the nseindia.com scrapers used later in the same run. So yfinance keeps
the bulk, split/dividend-adjusted price history, and NSE supplies only what is cheap to get:

  benchmarks()       Nifty 50 / Nifty 500 / India VIX daily history (12 months per call)
  official_bars()    the latest session's official OHLCV for every stock (bulk quotes)
  patch_last_bar()   corrects or appends that session in the yfinance frames
  ex_dividends()     upcoming ex-dividend dates for the scan's few survivors (enrich.py)

Every function is best effort: on any failure it returns what it has and the caller keeps the
yfinance data. NSE labels this data informational/educational (personal use only).
"""
import logging
from datetime import date, timedelta

import pandas as pd

from .nse_mcp import NSEMCP, MCPError, pick, rows

log = logging.getLogger(__name__)

# Yahoo tickers used in config.yaml -> NSE index names
INDEX_NAMES = {
    "^NSEI": "Nifty 50",
    "^CRSLDX": "Nifty 500",
    "^INDIAVIX": "India VIX",
    "^NSEBANK": "Nifty Bank",
}


class Budget:
    """Shared client with a hard cap on calls per run, so a bug can't hammer NSE."""

    def __init__(self, cfg: dict | None = None):
        c = (cfg or {}).get("data", {}).get("nse_mcp", {})
        self.max_calls = int(c.get("max_calls", 60))
        self.calls = 0
        self.client = NSEMCP("bhavcopy", delay=float(c.get("request_delay_sec", 1.0)), retries=1)
        self.blocked = False

    def call(self, name: str, **args):
        if self.blocked:
            raise MCPError("NSE MCP blocked earlier in this run")
        if self.calls >= self.max_calls:
            raise MCPError(f"NSE MCP call budget ({self.max_calls}) used up")
        self.calls += 1
        try:
            return self.client.call(name, **args)
        except MCPError as e:
            if "HTTP 403" in str(e):
                self.blocked = True      # stop at once; don't dig the hole deeper
                log.warning("NSE MCP returned 403; no more NSE MCP calls this run")
            raise


def _months(period: str) -> int:
    p = str(period).lower()
    if p.endswith("mo"):
        return int(p[:-2])
    if p.endswith("y"):
        return int(float(p[:-1]) * 12)
    return 24


def index_history(b: Budget, name: str, period: str, end: date | None = None) -> pd.DataFrame | None:
    """Daily OHLCV for an NSE index (Volume 0 where NSE has none, e.g. India VIX)."""
    months = min(_months(period), 60)
    end = end or date.today()
    start = end - timedelta(days=int(months * 30.44))
    parts, cursor = [], end
    while cursor > start:
        r = b.call("get_index_history", indexName=name, months=12, endDate=cursor.isoformat())
        recs = rows(r, "data")
        if not recs:
            break
        parts.extend(recs)
        nxt = r.get("next_end_date") if isinstance(r, dict) else None
        if not nxt:
            break
        cursor = date.fromisoformat(nxt)
    if not parts:
        return None
    df = pd.DataFrame([{
        "Date": pd.Timestamp(pick(d, "date")),
        "Open": float(pick(d, "open", default=pick(d, "close"))),
        "High": float(pick(d, "high", default=pick(d, "close"))),
        "Low": float(pick(d, "low", default=pick(d, "close"))),
        "Close": float(pick(d, "close")),
        "Volume": float(pick(d, "volume", default=0) or 0),
    } for d in parts if pick(d, "close") not in (None, 0)]).set_index("Date")
    df = df[~df.index.duplicated(keep="last")].sort_index()
    return df[df.index >= pd.Timestamp(start)]


def benchmarks(b: Budget, tickers: list[str], period: str) -> dict[str, pd.DataFrame]:
    """{yahoo_ticker: DataFrame} for the benchmark tickers NSE can serve."""
    out = {}
    for t in tickers:
        name = INDEX_NAMES.get(t)
        if not name:
            continue
        try:
            df = index_history(b, name, period)
            if df is not None and len(df) > 200:
                out[t] = df
        except MCPError as e:
            log.warning("NSE index history for %s failed: %s", name, e)
            if b.blocked:
                break
    return out


def official_bars(b: Budget, symbols: list[str], chunk: int = 50) -> dict[str, dict]:
    """{symbol: {date, Open, High, Low, Close, Volume}} for the latest session, via bulk quotes."""
    out: dict[str, dict] = {}
    i = 0
    while i < len(symbols):
        batch = symbols[i:i + chunk]
        try:
            r = b.call("get_bulk_quote", symbols=batch)
        except MCPError as e:
            if b.blocked or "budget" in str(e) or chunk <= 10:
                log.warning("NSE bulk quotes stopped at %d of %d symbols: %s", i, len(symbols), e)
                break
            chunk //= 2             # maybe the batch was too big; retry smaller
            continue
        for q in rows(r, "quotes"):
            s, close = pick(q, "symbol"), pick(q, "close")
            if not s or close in (None, 0):
                continue
            out[str(s).strip().upper()] = {
                "date": pd.Timestamp(pick(q, "date")).normalize(),
                "Open": float(pick(q, "open", default=close)), "High": float(pick(q, "high", default=close)),
                "Low": float(pick(q, "low", default=close)), "Close": float(close),
                "Volume": float(pick(q, "volume", default=0) or 0)}
        i += len(batch)
    return out


def patch_last_bar(prices: dict[str, pd.DataFrame], bars: dict[str, dict],
                   session: date | None = None, tol_pct: float = 0.5) -> dict:
    """Puts NSE's official bar for the latest session into each yfinance frame.

    Same date and yfinance off by more than tol_pct -> replace the bar ("corrected").
    yfinance one session behind and NSE has the expected session -> append it ("appended").
    Only the latest bar is touched; earlier bars keep yfinance's adjustment, so if the latest
    session is an ex-date the jump is real and appears in both sources.
    """
    stats = {"checked": 0, "matched": 0, "corrected": [], "appended": [], "nse_missing": 0}
    want = pd.Timestamp(session) if session else None
    for s, df in list(prices.items()):
        if s in INDEX_NAMES or s.startswith("^"):
            continue
        bar = bars.get(s.upper())
        if bar is None:
            stats["nse_missing"] += 1
            continue
        if df is None or not len(df):
            continue
        stats["checked"] += 1
        d = bar["date"]
        last = pd.Timestamp(df.index[-1]).normalize()
        row = pd.DataFrame([[bar[k] for k in ("Open", "High", "Low", "Close", "Volume")]],
                           columns=["Open", "High", "Low", "Close", "Volume"], index=[d])
        if last == d:
            yc = float(df["Close"].iloc[-1])
            if abs(yc / bar["Close"] - 1) * 100 > tol_pct:
                df = df.iloc[:-1]
                prices[s] = pd.concat([df[row.columns], row])
                stats["corrected"].append({"symbol": s, "yfinance": round(yc, 2), "nse": bar["Close"]})
            else:
                stats["matched"] += 1
        elif last < d and (want is None or d == want):
            prices[s] = pd.concat([df[row.columns], row])
            stats["appended"].append(s)
    return stats


def ex_dividends(b: Budget, symbol: str, today: date, lookahead_days: int) -> list[dict]:
    """Ex-dividend dates in [today, today + lookahead] from NSE's corporate-action list."""
    horizon = today + timedelta(days=lookahead_days)
    r = b.call("get_corporate_actions", symbol=symbol, fromDate=today.isoformat(),
               toDate=horizon.isoformat())
    out = []
    for a in rows(r, "actions"):
        kind = str(pick(a, "actionType", default="")).upper()
        d = str(pick(a, "exDate", default=""))[:10]
        if kind == "DIVIDEND" and d and today.isoformat() <= d <= horizon.isoformat():
            out.append({"type": "ex_dividend", "date": d, "source": "nse"})
    return out
