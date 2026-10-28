"""Probe: can quarterly-results sources be reached from here? Writes a status report.

Checks NSE (official filings, results calendar, announcements) and Yahoo (quarterly financials,
earnings dates) for a few large companies. Prints only status codes and small samples.
Usage: python -m scanner.results_probe [out.json]
"""
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

import requests

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
NSE = "https://www.nseindia.com"
SYMBOLS = ["RELIANCE", "TCS", "HDFCBANK"]


def _shape(r: requests.Response) -> dict:
    out = {"status": r.status_code, "content_type": r.headers.get("content-type", "")[:40],
           "bytes": len(r.content)}
    try:
        j = r.json()
        if isinstance(j, list):
            out["json"] = f"list[{len(j)}]"
            if j and isinstance(j[0], dict):
                out["first_keys"] = list(j[0])[:12]
        elif isinstance(j, dict):
            out["json"] = f"dict keys={list(j)[:10]}"
            for k in ("data", "rows", "Table"):
                if isinstance(j.get(k), list):
                    out[f"{k}_len"] = len(j[k])
                    if j[k] and isinstance(j[k][0], dict):
                        out["first_keys"] = list(j[k][0])[:12]
    except ValueError:
        out["text_head"] = r.text[:120]
    return out


def probe_nse() -> dict:
    res = {}
    s = requests.Session()
    s.headers.update({"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9",
                      "Referer": f"{NSE}/"})
    try:
        r = s.get(NSE, timeout=20)
        res["homepage"] = {"status": r.status_code, "cookies": sorted(s.cookies.keys())[:8]}
    except requests.RequestException as e:
        return {"homepage": f"error {type(e).__name__}: {str(e)[:120]}"}
    time.sleep(1.5)
    urls = {
        "financial_results_RELIANCE": f"{NSE}/api/corporates-financial-results?index=equities"
                                      f"&symbol=RELIANCE&period=Quarterly",
        "results_calendar": f"{NSE}/api/event-calendar?index=equities",
        "announcements_TCS": f"{NSE}/api/corporate-announcements?index=equities&symbol=TCS",
        "quote_HDFCBANK": f"{NSE}/api/quote-equity?symbol=HDFCBANK",
    }
    for name, url in urls.items():
        try:
            res[name] = _shape(s.get(url, timeout=20))
        except requests.RequestException as e:
            res[name] = f"error {type(e).__name__}: {str(e)[:120]}"
        time.sleep(1.5)
    return res


def probe_yahoo() -> dict:
    import yfinance as yf
    res = {}
    for sym in SYMBOLS:
        t = yf.Ticker(f"{sym}.NS")
        r = {}
        try:
            q = t.quarterly_income_stmt
            r["quarters"] = [str(c)[:10] for c in list(q.columns)[:5]]
            for row in ("Total Revenue", "Net Income", "Operating Income"):
                if row in q.index:
                    r[row] = [None if v != v else round(float(v) / 1e7, 1) for v in q.loc[row].iloc[:5]]
        except Exception as e:
            r["quarterly_error"] = f"{type(e).__name__}: {str(e)[:100]}"
        try:
            cal = t.calendar or {}
            r["calendar_earnings_date"] = [str(d) for d in (cal.get("Earnings Date") or [])] \
                if isinstance(cal, dict) else str(cal)[:100]
        except Exception as e:
            r["calendar_error"] = f"{type(e).__name__}: {str(e)[:100]}"
        try:
            ed = t.get_earnings_dates(limit=4)
            r["earnings_dates"] = [str(i)[:10] for i in ed.index] if ed is not None else None
        except Exception as e:
            r["earnings_dates_error"] = f"{type(e).__name__}: {str(e)[:100]}"
        res[sym] = r
    return res


if __name__ == "__main__":
    out = {"utc_now": datetime.now(timezone.utc).isoformat(timespec="seconds"),
           "nse": probe_nse(), "yahoo": probe_yahoo(),
           "note": "Yahoo revenue/profit figures are in Rs crore"}
    text = json.dumps(out, indent=2, default=str)
    print(text)
    if len(sys.argv) > 1:
        Path(sys.argv[1]).parent.mkdir(parents=True, exist_ok=True)
        Path(sys.argv[1]).write_text(text + "\n")
