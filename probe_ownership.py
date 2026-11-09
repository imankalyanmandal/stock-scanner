"""Temporary probe: what do the ownership / deal endpoints return from GitHub Actions?"""
import json, re
from scanner.results import NSEClient

c = NSEClient(delay=1.0)
def show(name, fn):
    print(f"\n===== {name}")
    try:
        r = fn()
        print("status", r.status_code, r.headers.get("content-type", "")[:40], len(r.content))
        t = r.text
        try:
            j = r.json()
            if isinstance(j, dict):
                print("dict keys", list(j)[:15])
                for k, v in j.items():
                    if isinstance(v, list):
                        print(k, "len", len(v)); print(json.dumps(v[:2], indent=0)[:1500])
                    elif isinstance(v, dict):
                        print(k, "dict keys", list(v)[:15]); print(json.dumps(v, indent=0)[:1200])
            else:
                print("list len", len(j)); print(json.dumps(j[:3], indent=0)[:2500])
        except ValueError:
            print(t[:1500])
    except Exception as e:
        print("ERROR", type(e).__name__, str(e)[:300])

N = "https://www.nseindia.com"
show("shp master RADICO", lambda: c.get(f"{N}/api/corporate-share-holdings-master?index=equities&symbol=RADICO"))
show("pledge ADANIPOWER", lambda: c.get(f"{N}/api/corporate-pledgedata?index=equities&symbol=ADANIPOWER"))
show("bulk hist", lambda: c.get(f"{N}/api/historical/bulk-deals?from=01-11-2026&to=07-11-2026"))
show("block hist", lambda: c.get(f"{N}/api/historical/block-deals?from=01-11-2026&to=07-11-2026"))
show("largedeal snapshot", lambda: c.get(f"{N}/api/snapshot-capital-market-largedeal"))
show("archive bulk.csv", lambda: c.s.get("https://nsearchives.nseindia.com/content/equities/bulk.csv", timeout=25))
show("archive block.csv", lambda: c.s.get("https://nsearchives.nseindia.com/content/equities/block.csv", timeout=25))
import requests
H = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0 Safari/537.36"}
r = requests.get("https://www.screener.in/company/RADICO/consolidated/", headers=H, timeout=25)
print("\n===== screener RADICO status", r.status_code, len(r.text))
i = r.text.find('id="shareholding"')
print("shareholding at", i)
seg = r.text[i:i+9000]
print(re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "|", seg))[:3000])
# xbrl from shp master
try:
    j = c.get(f"{N}/api/corporate-share-holdings-master?index=equities&symbol=RADICO").json()
    first = j[0] if isinstance(j, list) else j
    x = next((v for k, v in first.items() if "xbrl" in k.lower() and v), None)
    print("\n===== xbrl url", x)
    if x:
        xr = c.s.get(x, timeout=30); print("xbrl status", xr.status_code, len(xr.content))
        tags = sorted(set(re.findall(r"<in-bse-shp:([A-Za-z]+)", xr.text)))
        print("tags", len(tags), tags[:150])
        for t in ("ShareholdingAsAPercentageOfTotalNumberOfShares","NumberOfShares"):
            print(t, re.findall(rf"<in-bse-shp:{t}[^>]*contextRef=\"([^\"]+)\"[^>]*>([^<]+)<", xr.text)[:40])
except Exception as e:
    print("xbrl ERROR", e)
