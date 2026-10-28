"""Quarterly results from NSE (official filings), with Yahoo as fallback. Information only.

  results_calendar()  upcoming board meetings whose purpose includes "Financial Results"
  snapshot()          latest quarter vs previous quarter and vs the same quarter last year,
                      parsed from the companies' XBRL filings; cached per company in data/results/

Amounts are reported in rupees in the XBRL files and shown here in crore.
"""
import json
import logging
import re
import time
import xml.etree.ElementTree as ET
from datetime import date, datetime
from pathlib import Path

import requests

log = logging.getLogger(__name__)
NSE = "https://www.nseindia.com"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/124.0 Safari/537.36")
XBRLI = "http://www.xbrl.org/2003/instance"

# first tag found wins; banks and insurers use different line items
TAGS = {
    "revenue": ["RevenueFromOperations", "InterestEarned", "IncomeFromOperations", "Revenue",
                "TotalRevenueFromOperations"],
    "total_income": ["Income", "TotalIncome"],
    "net_profit": ["ProfitOrLossAttributableToOwnersOfParent", "ProfitLossForPeriod",
                   "ProfitLossForThePeriod", "NetProfitLossForThePeriod",
                   "ProfitLossForPeriodFromContinuingOperations"],
    "pbt": ["ProfitBeforeTax", "ProfitLossBeforeTax", "ProfitLossFromOrdinaryActivitiesBeforeTax"],
    "finance_costs": ["FinanceCosts"],
    "depreciation": ["DepreciationDepletionAndAmortisationExpense", "DepreciationAndAmortisationExpense"],
    "other_income": ["OtherIncome"],
    "eps": ["BasicEarningsLossPerShareFromContinuingAndDiscontinuedOperations",
            "BasicEarningsLossPerShareFromContinuingOperations", "BasicEarningsPerShareAfterExtraordinaryItems",
            "BasicEarningsPerShareBeforeExtraordinaryItems"],
}


class NSEClient:
    """Browser-like session (NSE needs its cookies) with a polite delay between requests."""

    def __init__(self, delay: float = 1.0, timeout: int = 25):
        self.s = requests.Session()
        self.s.headers.update({"User-Agent": UA, "Accept": "*/*", "Accept-Language": "en-US,en;q=0.9",
                               "Referer": f"{NSE}/"})
        self.delay, self.timeout, self._warm, self._last = delay, timeout, False, 0.0

    def get(self, url: str) -> requests.Response:
        if not self._warm:
            self.s.get(NSE, timeout=self.timeout)
            self._warm = True
        wait = self.delay - (time.monotonic() - self._last)
        if wait > 0:
            time.sleep(wait)
        r = self.s.get(url, timeout=self.timeout)
        self._last = time.monotonic()
        r.raise_for_status()
        return r

    def json(self, path: str):
        return self.get(f"{NSE}{path}").json()


def _d(s: str) -> date | None:
    for fmt in ("%d-%b-%Y", "%d-%b-%Y %H:%M:%S", "%d-%b-%Y %H:%M", "%Y-%m-%d"):
        try:
            return datetime.strptime(str(s).strip(), fmt).date()
        except ValueError:
            pass
    return None


def quarter_label(end: date) -> str:
    q = {6: 1, 9: 2, 12: 3, 3: 4}.get(end.month)
    fy = end.year + 1 if end.month > 3 else end.year
    return f"Q{q} FY{str(fy)[2:]}" if q else end.isoformat()


# ----------------------------------------------------------------------------- calendar
def results_calendar(client: NSEClient) -> dict[str, list[str]]:
    """symbol -> upcoming results board-meeting dates (ISO)."""
    out: dict[str, list[str]] = {}
    for x in client.json("/api/event-calendar?index=equities") or []:
        if "financial result" not in str(x.get("purpose", "")).lower():
            continue
        d = _d(x.get("date", ""))
        if d:
            out.setdefault(str(x.get("symbol", "")).upper(), []).append(d.isoformat())
    return out


# ----------------------------------------------------------------------------- filings
def _quarter_start(end: date) -> date:
    m = end.month - 2
    y = end.year if m > 0 else end.year - 1
    return date(y, m if m > 0 else m + 12, 1)


def normalize_integrated(rows: list[dict]) -> list[dict]:
    """Integrated Filing (Financials) rows, Mar-2025 quarter onwards -> the old row shape."""
    out = []
    for x in rows or []:
        low = {k.lower(): v for k, v in x.items()}
        url = str(low.get("xbrl") or "")
        if not url.startswith("http") or url.rstrip("/").endswith("null"):
            continue
        end = _d(str(low.get("qe_date", ""))[:11])
        if not end:
            continue
        cons = str(low.get("consolidated", "")).strip().lower()
        out.append({"period": "Quarterly", "fromDate": _quarter_start(end).strftime("%d-%b-%Y"),
                    "toDate": end.strftime("%d-%b-%Y"),
                    "consolidated": "Consolidated" if cons.startswith("consolidated") else "Non-Consolidated",
                    "xbrl": url, "seqNumber": str(low.get("seq_id", "")),
                    "filingDate": str(low.get("broadcast_date") or low.get("creation_date") or "")[:11],
                    "audited": low.get("audited") or low.get("type_sub"), "source": "integrated"})
    return out


def all_filings(client: "NSEClient", symbol: str, max_pages: int = 3) -> list[dict]:
    """Integrated filings (2025+) plus the older Reg-33 filings (up to Dec 2024)."""
    rows, seen = [], set()
    for page in range(1, max_pages + 1):
        q = (f"/api/integrated-filing-results?index=equities&symbol={symbol}"
             f"&type=Integrated%20Filing-%20Financials&page={page}")
        try:
            env = client.json(q)
        except requests.HTTPError:
            break
        data = env.get("data", []) if isinstance(env, dict) else (env or [])
        new = [r for r in normalize_integrated(data) if (r["xbrl"]) not in seen]
        if not new:
            break
        seen.update(r["xbrl"] for r in new)
        rows += new
        total = env.get("totalCount") if isinstance(env, dict) else None
        if total is not None and len(seen) >= int(total):
            break
    try:
        rows += client.json(f"/api/corporates-financial-results?index=equities&symbol={symbol}"
                            f"&period=Quarterly") or []
    except requests.HTTPError:
        pass
    return rows


def pick_filings(rows: list[dict]) -> dict:
    """Latest quarter, the quarter before and the same quarter a year earlier.

    Prefers consolidated figures for every period when the latest quarter has them.
    """
    qs = []
    for x in rows or []:
        if str(x.get("period", "Quarterly")).lower() != "quarterly" or not x.get("xbrl"):
            continue
        frm, to = _d(x.get("fromDate", "")), _d(x.get("toDate", ""))
        if not frm or not to or (to - frm).days > 100:
            continue
        cons = str(x.get("consolidated", "")).lower() == "consolidated"
        qs.append({"from": frm, "to": to, "consolidated": cons, "xbrl": x["xbrl"],
                   "filed": _d(str(x.get("filingDate", ""))[:11]), "seq": str(x.get("seqNumber", "")),
                   "audited": x.get("audited")})
    if not qs:
        return {}
    latest_to = max(q["to"] for q in qs)
    basis = any(q["consolidated"] for q in qs if q["to"] == latest_to)

    def find(to: date):
        cands = [q for q in qs if q["to"] == to]
        same = [q for q in cands if q["consolidated"] == basis]
        cands = same or cands
        return max(cands, key=lambda q: (q["filed"] or date.min, q["seq"])) if cands else None

    def shift(d: date, months: int) -> date:
        m = d.month - months
        y = d.year + (m - 1) // 12
        m = (m - 1) % 12 + 1
        last = {1: 31, 2: 29 if y % 4 == 0 else 28, 3: 31, 4: 30, 5: 31, 6: 30, 7: 31, 8: 31,
                9: 30, 10: 31, 11: 30, 12: 31}[m]
        return date(y, m, last)
    return {"basis": "consolidated" if basis else "standalone", "latest": find(latest_to),
            "prev": find(shift(latest_to, 3)), "year_ago": find(shift(latest_to, 12))}


def parse_xbrl(xml: bytes | str, start: date, end: date) -> dict:
    """Non-dimensional facts for the [start, end] period -> {metric: value}, plus all tags seen."""
    root = ET.fromstring(xml if isinstance(xml, bytes) else xml.encode())
    ctx = {}
    for c in root.iter(f"{{{XBRLI}}}context"):
        per = c.find(f"{{{XBRLI}}}period")
        if per is None:
            continue
        s, e = per.findtext(f"{{{XBRLI}}}startDate"), per.findtext(f"{{{XBRLI}}}endDate")
        dims = c.find(f".//{{{XBRLI}}}segment") is not None or c.find(f".//{{{XBRLI}}}scenario") is not None
        ctx[c.get("id")] = (s, e, dims)
    want = (start.isoformat(), end.isoformat())
    facts: dict[str, float] = {}
    for el in root:
        if not isinstance(el.tag, str) or "}" not in el.tag or el.get("contextRef") not in ctx:
            continue
        s, e, dims = ctx[el.get("contextRef")]
        if dims or (s, e) != want:
            continue
        try:
            facts.setdefault(el.tag.split("}", 1)[1], float((el.text or "").strip()))
        except ValueError:
            continue
    out = {k: next((facts[t] for t in tags if t in facts), None) for k, tags in TAGS.items()}
    out["_tags"] = sorted(facts)
    return out


def _pct(a, b):
    return round((a / b - 1) * 100, 1) if a is not None and b not in (None, 0) and b > 0 else None


def _margin(m: dict) -> float | None:
    """Operating margin = (PBT + finance costs + depreciation - other income) / revenue; else PBT margin."""
    rev, pbt = m.get("revenue"), m.get("pbt")
    if not rev or pbt is None:
        return None
    op = pbt + (m.get("finance_costs") or 0) + (m.get("depreciation") or 0) - (m.get("other_income") or 0)
    return round(op / rev * 100, 1)


def _cr(v):
    return round(v / 1e7, 1) if v is not None else None


def snapshot(client: NSEClient, symbol: str, cache_dir: str = "data/results") -> dict:
    """Results snapshot for one company. Re-parses only when a newer filing appears."""
    rows = all_filings(client, symbol)
    sel = pick_filings(rows)
    if not sel or not sel.get("latest"):
        return {"source": "nse", "error": "no quarterly filings found"}
    latest = sel["latest"]
    path = Path(cache_dir) / f"{symbol}.json"
    try:
        cached = json.loads(path.read_text())
        if cached.get("seq") == latest["seq"] and cached.get("xbrl") == latest["xbrl"] \
                and cached.get("version") == 2:
            return cached
    except (OSError, ValueError):
        pass
    parsed = {}
    for k in ("latest", "prev", "year_ago"):
        q = sel.get(k)
        if q:
            parsed[k] = parse_xbrl(client.get(q["xbrl"]).content, q["from"], q["to"])
    L, P, Y = parsed.get("latest", {}), parsed.get("prev", {}), parsed.get("year_ago", {})
    out = {
        "version": 2, "source": "nse", "symbol": symbol, "seq": latest["seq"], "xbrl": latest["xbrl"],
        "basis": sel["basis"],
        "quarter": quarter_label(latest["to"]), "period_end": latest["to"].isoformat(),
        "filed": latest["filed"].isoformat() if latest["filed"] else None,
        "audited": latest.get("audited"),
        "revenue_cr": _cr(L.get("revenue")), "net_profit_cr": _cr(L.get("net_profit")),
        "eps": L.get("eps"),
        "revenue_yoy_pct": _pct(L.get("revenue"), Y.get("revenue")),
        "profit_yoy_pct": _pct(L.get("net_profit"), Y.get("net_profit")),
        "revenue_qoq_pct": _pct(L.get("revenue"), P.get("revenue")),
        "profit_qoq_pct": _pct(L.get("net_profit"), P.get("net_profit")),
        "op_margin_pct": _margin(L), "op_margin_year_ago_pct": _margin(Y),
        "loss": (L.get("net_profit") or 0) < 0,
        "missing": [k for k in ("revenue", "net_profit") if L.get(k) is None],
        "tags_seen": L.get("_tags", [])[:80] if L.get("revenue") is None or L.get("net_profit") is None else [],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, indent=1))
    return out


# ----------------------------------------------------------------------------- Yahoo fallback
def yahoo_snapshot(symbol: str) -> dict:
    import yfinance as yf
    q = yf.Ticker(f"{symbol}.NS").quarterly_income_stmt
    if q is None or q.empty:
        return {"source": "yahoo", "error": "no quarterly data"}
    cols = sorted(q.columns, reverse=True)

    def row(names, col):
        for n in names:
            if n in q.index and col in q.columns:
                v = q.at[n, col]
                if v == v:
                    return float(v)
        return None
    latest = cols[0]
    year_ago = next((c for c in cols if 330 <= (latest - c).days <= 400), None)
    prev = next((c for c in cols if 80 <= (latest - c).days <= 100), None)
    rev = lambda c: row(["Total Revenue", "Operating Revenue"], c) if c is not None else None
    np_ = lambda c: row(["Net Income", "Net Income Common Stockholders"], c) if c is not None else None
    end = latest.date()
    return {"source": "yahoo", "symbol": symbol, "quarter": quarter_label(end), "period_end": end.isoformat(),
            "basis": "as reported by Yahoo", "filed": None,
            "revenue_cr": _cr(rev(latest)), "net_profit_cr": _cr(np_(latest)),
            "revenue_yoy_pct": _pct(rev(latest), rev(year_ago)), "profit_yoy_pct": _pct(np_(latest), np_(year_ago)),
            "revenue_qoq_pct": _pct(rev(latest), rev(prev)), "profit_qoq_pct": _pct(np_(latest), np_(prev)),
            "op_margin_pct": None, "loss": (np_(latest) or 0) < 0,
            "stale": (date.today() - end).days > 150}


def get_snapshot(client: NSEClient | None, symbol: str, cfg: dict) -> dict:
    rc = cfg.get("results", {})
    errors = []
    if client is not None:
        try:
            s = snapshot(client, symbol, rc.get("cache_dir", "data/results"))
            if not s.get("error") and not s.get("missing"):
                return s
            errors.append(f"nse: {s.get('error') or 'missing ' + ','.join(s['missing'])}")
            nse_partial = s
        except Exception as e:
            errors.append(f"nse: {type(e).__name__}: {str(e)[:80]}")
            nse_partial = None
    else:
        nse_partial = None
    if rc.get("yahoo_fallback", True):
        try:
            y = yahoo_snapshot(symbol)
            if not y.get("error"):
                return {**y, "fallback_reason": "; ".join(errors)}
            errors.append(f"yahoo: {y['error']}")
        except Exception as e:
            errors.append(f"yahoo: {type(e).__name__}")
    return nse_partial or {"source": "-", "error": "; ".join(errors) or "unavailable"}


def summary_line(s: dict | None) -> str:
    """Short human line for Telegram / report."""
    if not s or s.get("error"):
        return ""
    bits = [f"{s['quarter']}"]
    if s.get("revenue_yoy_pct") is not None:
        bits.append(f"Rev {s['revenue_yoy_pct']:+.0f}% YoY")
    if s.get("loss"):
        bits.append("net LOSS")
    elif s.get("profit_yoy_pct") is not None:
        bits.append(f"PAT {s['profit_yoy_pct']:+.0f}% YoY")
    if s.get("op_margin_pct") is not None and s.get("op_margin_year_ago_pct") is not None:
        d = s["op_margin_pct"] - s["op_margin_year_ago_pct"]
        bits.append(f"margin {s['op_margin_pct']:.0f}% ({d:+.1f}pp)")
    if s.get("filed"):
        age = (date.today() - date.fromisoformat(s["filed"])).days
        bits.append(f"filed {age}d ago" if age <= 10 else f"filed {s['filed']}")
    if s.get("source") == "yahoo":
        bits.append("via Yahoo" + (", may be stale" if s.get("stale") else ""))
    return " · ".join(bits)


if __name__ == "__main__":
    import sys
    syms = [a for a in sys.argv[1:] if not a.endswith(".json")] or ["RELIANCE", "HDFCBANK", "TCS"]
    target = next((a for a in sys.argv[1:] if a.endswith(".json")), None)
    import yaml
    cfg = yaml.safe_load(open("config.yaml"))
    c = NSEClient(delay=cfg.get("results", {}).get("request_delay_sec", 1.0))
    out = {}
    try:
        cal = results_calendar(c)
        out["calendar"] = {"companies": len(cal), "sample": dict(list(cal.items())[:5]),
                           **{s: cal.get(s) for s in syms}}
    except Exception as e:
        out["calendar"] = f"error {type(e).__name__}: {str(e)[:120]}"
    cache = cfg.get("results", {}).get("cache_dir", "data/results")
    try:
        env = c.json(f"/api/integrated-filing-results?index=equities&symbol={syms[0]}"
                     f"&type=Integrated%20Filing-%20Financials")
        data = env.get("data", []) if isinstance(env, dict) else env
        out["integrated_raw"] = {"envelope_keys": list(env)[:8] if isinstance(env, dict) else "list",
                                 "totalCount": env.get("totalCount") if isinstance(env, dict) else None,
                                 "rows": len(data), "first_row": data[0] if data else None,
                                 "qe_dates": [d.get("qe_Date") for d in data][:12]}
    except Exception as e:
        out["integrated_raw"] = f"error {type(e).__name__}: {str(e)[:160]}"
    for s in syms:
        try:
            out[s] = snapshot(c, s, cache)
            out[s]["line"] = summary_line(out[s])
        except Exception as e:
            out[s] = f"error {type(e).__name__}: {str(e)[:160]}"
    text = json.dumps(out, indent=2, default=str)
    print(text)
    if target:
        Path(target).parent.mkdir(parents=True, exist_ok=True)
        Path(target).write_text(text + "\n")
