"""Enrichment for survivors only (slow/network-heavy): events and news."""
import logging
import re
import xml.etree.ElementTree as ET
from datetime import date, datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from urllib.parse import quote_plus

import requests
import yfinance as yf

from .data import to_yf

log = logging.getLogger(__name__)


def upcoming_events(symbol: str, today: date, lookahead_days: int) -> list[str]:
    """Earnings / ex-dividend dates within the lookahead window (best effort via yfinance)."""
    found = []
    try:
        cal = yf.Ticker(to_yf(symbol)).calendar or {}
    except Exception as e:
        log.debug("calendar %s failed: %s", symbol, e)
        return found
    horizon = today + timedelta(days=lookahead_days)
    for key in ("Earnings Date", "Ex-Dividend Date", "Dividend Date"):
        vals = cal.get(key) if isinstance(cal, dict) else None
        if vals is None:
            continue
        for d in vals if isinstance(vals, (list, tuple)) else [vals]:
            d = d.date() if isinstance(d, datetime) else d
            if isinstance(d, date) and today <= d <= horizon:
                found.append(f"{key.lower().replace(' ', '_')}:{d.isoformat()}")
    return found


_SUFFIX = re.compile(r"\b(limited|ltd\.?|india)\b", re.I)


def fetch_news(company: str, symbol: str, lookback_days: int, fresh_hours: int,
               max_items: int) -> dict:
    """Recent headlines from Google News RSS. Returns has_news, fresh flag and items."""
    name = _SUFFIX.sub("", company).strip(" .,")
    q = quote_plus(f'"{name}" OR "{symbol}" stock when:{lookback_days}d')
    url = f"https://news.google.com/rss/search?q={q}&hl=en-IN&gl=IN&ceid=IN:en"
    items = []
    try:
        r = requests.get(url, timeout=15, headers={"User-Agent": "Mozilla/5.0"})
        r.raise_for_status()
        root = ET.fromstring(r.content)
        now = datetime.now(timezone.utc)
        for it in root.iter("item"):
            pub = it.findtext("pubDate")
            try:
                ts = parsedate_to_datetime(pub) if pub else None
            except (TypeError, ValueError):
                ts = None
            items.append({
                "title": (it.findtext("title") or "").strip(),
                "source": (it.findtext("source") or "").strip(),
                "link": it.findtext("link") or "",
                "published": ts.isoformat() if ts else "",
                "age_hours": round((now - ts).total_seconds() / 3600, 1) if ts else None,
            })
    except (requests.RequestException, ET.ParseError) as e:
        log.warning("news fetch failed for %s: %s", symbol, e)
        return {"has_news": None, "fresh": None, "items": [], "error": str(e)}
    items.sort(key=lambda x: x["age_hours"] if x["age_hours"] is not None else 1e9)
    items = items[:max_items]
    fresh = any(i["age_hours"] is not None and i["age_hours"] <= fresh_hours for i in items)
    return {"has_news": bool(items), "fresh": fresh, "items": items,
            "sentiment": classify_news(items)}


def classify_news(items: list[dict]) -> str | None:
    """Hook for LLM sentiment/materiality classification (e.g. your Gemini/Groq chain).

    Return "positive" | "negative" | "neutral", or None to leave unclassified.
    Keep it score-only: it should never rescue a stock that failed the technical gates.
    """
    return None
