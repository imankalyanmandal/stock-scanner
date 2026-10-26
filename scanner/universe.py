"""Index constituents from NSE's published CSVs, cached locally.

If the download fails (NSE blocks or is down), the last cached copy is used.
You can also drop the CSV into data/ yourself with the same file name.
"""
import io
import logging
from pathlib import Path

import pandas as pd
import requests

log = logging.getLogger(__name__)

INDEX_FILES = {
    "nifty50": "ind_nifty50list.csv",
    "niftynext50": "ind_niftynext50list.csv",
    "niftymidcap150": "ind_niftymidcap150list.csv",
    "niftysmallcap250": "ind_niftysmallcap250list.csv",
    "nifty500": "ind_nifty500list.csv",
}
HOSTS = [
    "https://archives.nseindia.com/content/indices/",
    "https://nsearchives.nseindia.com/content/indices/",
    "https://www.niftyindices.com/IndexConstituent/",
]
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0 Safari/537.36",
    "Accept": "text/csv,*/*",
}


def _fetch_csv(fname: str) -> pd.DataFrame | None:
    for host in HOSTS:
        try:
            r = requests.get(host + fname, headers=HEADERS, timeout=20)
            if r.status_code == 200 and "Symbol" in r.text[:500]:
                return pd.read_csv(io.StringIO(r.text))
        except requests.RequestException as e:
            log.debug("fetch %s from %s failed: %s", fname, host, e)
    return None


def load_universe(index_keys: list[str], data_dir: str = "data") -> pd.DataFrame:
    """Returns DataFrame[symbol, company, industry, isin, indexes]."""
    cache = Path(data_dir)
    cache.mkdir(parents=True, exist_ok=True)
    frames = []
    for key in index_keys:
        fname = INDEX_FILES[key]
        df = _fetch_csv(fname)
        path = cache / fname
        if df is not None:
            df.to_csv(path, index=False)
            log.info("Fetched %s constituents (%d)", key, len(df))
        elif path.exists():
            df = pd.read_csv(path)
            log.warning("Using cached constituents for %s (%d)", key, len(df))
        else:
            raise RuntimeError(
                f"Could not download {fname} and no cached copy in {cache}/. "
                "Download it from niftyindices.com and place it there."
            )
        df.columns = [c.strip() for c in df.columns]
        if "Series" in df.columns:
            df = df[df["Series"].astype(str).str.strip() == "EQ"]
        frames.append(
            pd.DataFrame(
                {
                    "symbol": df["Symbol"].astype(str).str.strip(),
                    "company": df["Company Name"].astype(str).str.strip(),
                    "industry": df["Industry"].astype(str).str.strip(),
                    "isin": (df["ISIN Code"].astype(str).str.strip() if "ISIN Code" in df
                             else ""),
                    "indexes": key,
                }
            )
        )
    uni = pd.concat(frames, ignore_index=True)
    uni = (
        uni.groupby("symbol", as_index=False)
        .agg(company=("company", "first"), industry=("industry", "first"), isin=("isin", "first"),
             indexes=("indexes", lambda s: ",".join(sorted(set(s)))))
    )
    return uni


def load_exclusions(path: str | None) -> dict[str, str]:
    """Manual do-not-trade list (ASM/GSM, F&O ban, ...). CSV: symbol,reason."""
    if not path or not Path(path).exists():
        return {}
    df = pd.read_csv(path, comment="#")
    df.columns = [c.strip().lower() for c in df.columns]
    reasons = df["reason"] if "reason" in df else pd.Series(["manual"] * len(df))
    return {str(s).strip().upper(): str(r).strip() or "manual"
            for s, r in zip(df["symbol"], reasons.fillna("manual"))}
