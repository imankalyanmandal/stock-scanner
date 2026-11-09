"""Temporary probe 2: historical deals with referer, pledge data, pledge tags in SHP XBRL."""
import json, re
from scanner.results import NSEClient
N = "https://www.nseindia.com"
c = NSEClient(delay=1.0)
def tryget(name, url, referer=None):
    print(f"\n===== {name}")
    try:
        if referer:
            c.s.get(referer, timeout=25)
            c.s.headers["Referer"] = referer
        r = c.s.get(url, timeout=25)
        print("status", r.status_code, r.headers.get("content-type", "")[:40], len(r.content))
        try:
            j = r.json()
            if isinstance(j, dict):
                print("keys", list(j)[:12])
                for k, v in j.items():
                    if isinstance(v, list):
                        print(k, len(v), json.dumps(v[:2])[:1500])
            else:
                print("list", len(j), json.dumps(j[:2])[:1500])
            return j
        except ValueError:
            print("not json:", r.text[:150].replace("\n", " "))
    except Exception as e:
        print("ERROR", e)

ref = f"{N}/report-detail/display-bulk-and-block-deals"
tryget("bulk hist w/ referer", f"{N}/api/historical/bulk-deals?from=01-11-2026&to=07-11-2026", ref)
tryget("block hist w/ referer", f"{N}/api/historical/block-deals?from=01-11-2026&to=07-11-2026", ref)
tryget("bulk hist optionType", f"{N}/api/historical/bulk-deals?optionType=bulk_deals&from=01-11-2026&to=07-11-2026", ref)
tryget("largedeals hist", f"{N}/api/historicalOR/bulk-block-short-deals?optionType=bulk_deals&from=01-11-2026&to=07-11-2026", ref)
tryget("largedeals hist block", f"{N}/api/historicalOR/bulk-block-short-deals?optionType=block_deals&from=01-11-2026&to=07-11-2026", ref)
tryget("archive dated bulk", "https://nsearchives.nseindia.com/content/equities/bulk_06112026.csv")
pref = f"{N}/companies-listing/corporate-filings-pledged-data"
c.s.headers["Referer"] = pref
for s in ["GMRAIRPORT", "JSWSTEEL", "VEDL", "ADANIENT", "TATAPOWER", "SUZLON", "IRB"]:
    j = tryget(f"pledge {s}", f"{N}/api/corporate-pledgedata?index=equities&symbol={s}", None)
j = tryget("pledge all", f"{N}/api/corporate-pledgedata?index=equities", None)
# pledge tags in a pledged company's SHP xbrl
for s in ["VEDL", "GMRAIRPORT"]:
    try:
        m = c.s.get(f"{N}/api/corporate-share-holdings-master?index=equities&symbol={s}", timeout=25).json()
        x = m[0]["xbrl"]; xr = c.s.get(x, timeout=30)
        tags = sorted(set(re.findall(r"<in-bse-shp:([A-Za-z]+)", xr.text)))
        print(f"\n===== {s} shp tags with Pledg/Encumb:", [t for t in tags if re.search("Pledg|Encumb|NDU", t)])
        for t in [t for t in tags if re.search("Pledg|Encumb", t) and not t.startswith("Whether")][:6]:
            print(t, re.findall(rf"<in-bse-shp:{t}[^>]*contextRef=\"([^\"]+)\"[^>]*>([^<]+)<", xr.text)[:6])
    except Exception as e:
        print(s, "xbrl ERROR", e)
