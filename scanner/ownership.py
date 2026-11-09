"""Who is buying and selling: quarterly shareholding changes and NSE bulk/block deals.

  shareholding()  promoter, FII, DII and mutual-fund holding (% of shares) plus the share of promoter
                  holding that is pledged, for the latest two quarters. Parsed from the shareholding
                  pattern XBRL each company files with NSE; parsed numbers are cached per filing.
  score_ownership()  rising promoter / FII / MF holding or a falling pledge -> positive, and vice versa.
  fetch_deals()   bulk and block deals for a date range (one request each, all symbols).
  score_deals()   net buying vs selling per stock; same-client buy+sell churn is ignored.

Information signals: they tag holdings news and add flags to scan picks, never reject a pick.
"""
import json
import logging
import re
from datetime import date
from pathlib import Path
from urllib.parse import quote

from .results import NSEClient, _d, quarter_label

log = logging.getLogger(__name__)

CTX = {"promoter": "ShareholdingOfPromoterAndPromoterGroup_ContextI",
       "fii": "InstitutionsForeign_ContextI",
       "dii": "InstitutionsDomestic_ContextI",
       "mf": "MutualFundsOrUTI_ContextI"}
PCT_TAG = "ShareholdingAsAPercentageOfTotalNumberOfShares"
PLEDGE_TAG = "EncumberedShareUnderPledgedAsPercentageOfTotalNumberOfShares"
LABEL = {"promoter": "Promoter", "fii": "FII", "dii": "DII", "mf": "MF", "pledge": "Pledged"}


# ----------------------------------------------------------------------------- shareholding
def _xbrl_value(text: str, tag: str, ctx: str) -> float | None:
    m = re.search(rf'<in-bse-shp:{tag}\b[^>]*contextRef="{ctx}"[^>]*>([^<]+)<', text)
    if not m:
        return None
    try:
        return round(float(m.group(1)) * 100, 2)
    except ValueError:
        return None


def parse_shp_xbrl(text: str) -> dict:
    """Holding percentages from one shareholding-pattern XBRL filing."""
    out = {k: _xbrl_value(text, PCT_TAG, c) for k, c in CTX.items()}
    pledge = _xbrl_value(text, PLEDGE_TAG, CTX["promoter"])
    out["pledge"] = pledge if pledge is not None else 0.0   # tag absent = nothing pledged
    return out


def _quarters(master: list[dict]) -> list[dict]:
    """One filing per quarter end (latest broadcast wins for revisions), newest first."""
    def filed(f):
        return _d(str(f.get("broadcastDate") or "")) or _d(str(f.get("submissionDate") or "")) or date.min

    by_q = {}
    for f in master or []:
        q = _d(str(f.get("date", "")))
        if q and f.get("xbrl") and (q not in by_q or filed(f) >= filed(by_q[q])):
            by_q[q] = f
    return [by_q[q] for q in sorted(by_q, reverse=True)]


def shareholding(client: NSEClient, symbol: str, cache_dir: str = "data/ownership") -> dict:
    """Latest two quarters of holding percentages and the change between them (percentage points)."""
    master = client.json(f"/api/corporate-share-holdings-master?index=equities&symbol={quote(symbol, safe='')}")
    qs = _quarters(master)[:2]
    if len(qs) < 2:
        return {"error": "fewer than two shareholding filings"}
    path = Path(cache_dir) / f"{symbol.replace('&', '_')}.json"
    try:
        cache = json.loads(path.read_text())
    except (OSError, ValueError):
        cache = {}
    rows = []
    for f in qs:
        key = f["xbrl"]
        if key not in cache:
            cache[key] = parse_shp_xbrl(client.get(key).text)
            if cache[key]["promoter"] is None and f.get("pr_and_prgrp"):
                cache[key]["promoter"] = float(f["pr_and_prgrp"])
        rows.append({"quarter": quarter_label(_d(f["date"])), "filed": f.get("submissionDate"), **cache[key]})
    keep = {r["xbrl"] for r in qs}
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({k: v for k, v in cache.items() if k in keep}, indent=1) + "\n")
    latest, prev = rows
    change = {k: round(latest[k] - prev[k], 2) for k in ("promoter", "fii", "dii", "mf", "pledge")
              if latest.get(k) is not None and prev.get(k) is not None}
    return {"quarter": latest["quarter"], "prev_quarter": prev["quarter"], "filed": latest["filed"],
            "latest": {k: latest[k] for k in LABEL}, "prev": {k: prev[k] for k in LABEL},
            "change": change}


def score_ownership(sh: dict, cfg: dict) -> dict:
    """positive if promoter / FII / MF holding rose or the pledge fell by more than the thresholds."""
    if not sh or sh.get("error"):
        return {"verdict": "none", "reasons": [], "score": 0}
    oc = cfg.get("ownership", {})
    rules = [("promoter", oc.get("promoter_change_pp", 0.25), 2, +1),
             ("fii", oc.get("fii_change_pp", 0.5), 1, +1),
             ("mf", oc.get("mf_change_pp", 0.5), 1, +1),
             ("pledge", oc.get("pledge_change_pp", 1.0), 2, -1)]   # more pledged = bad
    score, good, bad = 0, [], []
    for k, thr, weight, sign in rules:
        d = sh["change"].get(k)
        if d is None or abs(d) < thr:
            continue
        s = weight * sign * (1 if d > 0 else -1)
        score += s
        what = (f"{LABEL[k]} {sh['prev'][k]:.2f}% → {sh['latest'][k]:.2f}% ({d:+.2f}pp)" if k != "pledge"
                else f"Promoter pledge {sh['prev'][k]:.1f}% → {sh['latest'][k]:.1f}% of their shares")
        (good if s > 0 else bad).append(what)
    verdict = "positive" if score > 0 else "negative" if score < 0 else ("mixed" if good or bad else "neutral")
    return {"verdict": verdict, "score": score, "reasons": bad + good if score < 0 else good + bad,
            "quarter": sh["quarter"], "prev_quarter": sh["prev_quarter"]}


# ----------------------------------------------------------------------------- deals
def fetch_deals(client: NSEClient, start: date, end: date) -> list[dict]:
    """Bulk and block deals between two dates (inclusive), all symbols."""
    out = []
    for kind in ("bulk", "block"):
        j = client.json(f"/api/historicalOR/bulk-block-short-deals?optionType={kind}_deals"
                        f"&from={start:%d-%m-%Y}&to={end:%d-%m-%Y}")
        for r in (j or {}).get("data", []) or []:
            try:
                out.append({"date": (_d(r["BD_DT_DATE"]) or start).isoformat(), "kind": kind,
                            "symbol": str(r["BD_SYMBOL"]).strip().upper(),
                            "client": str(r.get("BD_CLIENT_NAME") or "").strip(),
                            "side": str(r.get("BD_BUY_SELL") or "").strip().upper(),
                            "qty": float(r.get("BD_QTY_TRD") or 0),
                            "price": float(r.get("BD_TP_WATP") or 0)})
            except (KeyError, TypeError, ValueError):
                continue
    return out


def score_deals(deals: list[dict], cfg: dict) -> dict:
    """Net buying or selling in one stock's deals. Clients who bought and sold about the same
    quantity (intraday churn) are left out; the rest are netted and valued in crore."""
    if not deals:
        return {"verdict": "none", "net_cr": 0.0, "lines": [], "deals": 0}
    dc = cfg.get("deals", {})
    churn = dc.get("churn_ratio", 0.8)
    per = {}
    for d in deals:
        c = per.setdefault(d["client"], {"buy": 0.0, "sell": 0.0, "buy_v": 0.0, "sell_v": 0.0, "kinds": set()})
        side = "buy" if d["side"].startswith("B") else "sell"
        c[side] += d["qty"]
        c[side + "_v"] += d["qty"] * d["price"] / 1e7
        c["kinds"].add(d["kind"])
    net, lines = 0.0, []
    for name, c in per.items():
        hi, lo = max(c["buy"], c["sell"]), min(c["buy"], c["sell"])
        if hi and lo / hi >= churn:
            continue
        v = round(c["buy_v"] - c["sell_v"], 2)
        net += v
        lines.append((abs(v), f"{name.title()} {'bought' if v > 0 else 'sold'} ₹{abs(v):,.1f} cr "
                              f"({'/'.join(sorted(c['kinds']))})"))
    lines = [t for _, t in sorted(lines, reverse=True)][:3]
    thr = dc.get("min_net_value_cr", 1.0)
    verdict = "positive" if net >= thr else "negative" if net <= -thr else "neutral"
    return {"verdict": verdict, "net_cr": round(net, 2), "lines": lines, "deals": len(deals),
            "dates": sorted({d["date"] for d in deals})}


def deals_by_symbol(deals: list[dict]) -> dict[str, list[dict]]:
    out: dict[str, list[dict]] = {}
    for d in deals:
        out.setdefault(d["symbol"], []).append(d)
    return out


# ----------------------------------------------------------------------------- scan flags
GOOD_FLAGS = {"ownership_up", "deals_buying"}


def annotate(rows: list[dict], client: NSEClient, cfg: dict, ref: date) -> None:
    """Add ownership/deal details and flags to scan picks. Never removes a pick."""
    oc, dc = cfg.get("ownership", {}), cfg.get("deals", {})
    by = {}
    if dc.get("enabled") and rows:
        from datetime import timedelta
        try:
            by = deals_by_symbol(fetch_deals(client, ref - timedelta(days=dc.get("scan_lookback_days", 30)), ref))
        except Exception as e:  # noqa: BLE001
            log.warning("deals fetch failed: %s", e)
    for p in rows:
        if oc.get("enabled"):
            try:
                s = score_ownership(shareholding(client, p["symbol"], oc.get("cache_dir", "data/ownership")), cfg)
            except Exception as e:  # noqa: BLE001
                s = {"verdict": "error", "reasons": [f"{type(e).__name__}"]}
            p["ownership"] = s
            if s["verdict"] == "positive":
                p["flags"].append("ownership_up")
            elif s["verdict"] == "negative":
                p["flags"].append("ownership_down")
        if dc.get("enabled"):
            d = score_deals(by.get(p["symbol"].upper(), []), cfg)
            p["deals"] = d
            if d["verdict"] == "positive":
                p["flags"].append("deals_buying")
            elif d["verdict"] == "negative":
                p["flags"].append("deals_selling")
