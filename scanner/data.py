"""Price data (split/bonus/dividend adjusted via yfinance) and NSE delivery data."""
import io
import logging
import time
from datetime import date

import pandas as pd
import requests
import yfinance as yf

from .universe import HEADERS

log = logging.getLogger(__name__)


def to_yf(symbol: str) -> str:
    special = symbol.startswith("^") or "=" in symbol or "." in symbol
    return symbol if special else f"{symbol}.NS"


def download_prices(symbols: list[str], period: str = "1y", chunk: int = 50,
                    retries: int = 2) -> dict[str, pd.DataFrame]:
    """Returns {symbol: DataFrame[Open, High, Low, Close, Volume]} with adjusted prices."""
    out: dict[str, pd.DataFrame] = {}
    for i in range(0, len(symbols), chunk):
        batch = symbols[i:i + chunk]
        tickers = [to_yf(s) for s in batch]
        for attempt in range(retries + 1):
            try:
                raw = yf.download(tickers, period=period, interval="1d", auto_adjust=True,
                                  group_by="ticker", threads=True, progress=False)
                break
            except Exception as e:  # yfinance raises assorted errors
                log.warning("download batch %d failed (attempt %d): %s", i, attempt + 1, e)
                time.sleep(3 * (attempt + 1))
        else:
            continue
        for sym, tk in zip(batch, tickers):
            try:
                df = raw[tk] if isinstance(raw.columns, pd.MultiIndex) else raw
                df = df[["Open", "High", "Low", "Close", "Volume"]].dropna(subset=["Close"])
                if len(df):
                    out[sym] = df
            except KeyError:
                log.debug("no data for %s", sym)
    missing = set(symbols) - set(out)
    if missing:
        log.warning("No price data for %d symbols: %s", len(missing), sorted(missing)[:15])
    return out


def fetch_delivery(trade_date: date) -> dict[str, float]:
    """Delivery % per symbol from NSE's full bhavcopy. Empty dict if unavailable."""
    name = f"sec_bhavdata_full_{trade_date:%d%m%Y}.csv"
    for host in ("https://archives.nseindia.com/products/content/",
                 "https://nsearchives.nseindia.com/products/content/"):
        try:
            r = requests.get(host + name, headers=HEADERS, timeout=20)
            if r.status_code != 200 or "SYMBOL" not in r.text[:300]:
                continue
            df = pd.read_csv(io.StringIO(r.text))
            df.columns = [c.strip() for c in df.columns]
            df = df[df["SERIES"].astype(str).str.strip() == "EQ"]
            pct = pd.to_numeric(df["DELIV_PER"].astype(str).str.strip(), errors="coerce")
            return dict(zip(df["SYMBOL"].astype(str).str.strip(), pct))
        except requests.RequestException as e:
            log.debug("delivery fetch from %s failed: %s", host, e)
    log.warning("Delivery data unavailable for %s; delivery score set neutral", trade_date)
    return {}
