"""Forward trade journal: every pick is logged, then replayed against real prices on each run.

The journal stores only the plan (trigger, max entry, stop). Outcomes are recomputed from price
data with trade.simulate(), so live results are measured with exactly the backtest's rules.
"""
import json
import logging
from datetime import datetime
from pathlib import Path

import pandas as pd

from .filters import arrays
from .trade import simulate, trade_stats

log = logging.getLogger(__name__)

COLUMNS = ["session", "symbol", "company", "industry", "setup", "score", "regime", "size_mult",
           "trigger", "max_entry", "stop", "t1", "risk_per_share", "qty",
           "status", "fill_date", "fill_price", "exit_date", "exit_price", "exit_reason",
           "r_multiple", "pct_return", "bars_held", "updated",
           "news_verdict", "news_headline", "vetoed", "premarket_news"]
OPEN_STATES = {"pending", "open", ""}


def load(path: str) -> pd.DataFrame:
    p = Path(path)
    if p.exists():
        df = pd.read_csv(p, dtype={"session": str, "symbol": str})
        for c in COLUMNS:
            if c not in df:
                df[c] = None
        return df[COLUMNS].astype(object)
    return pd.DataFrame(columns=COLUMNS, dtype=object)


def save(df: pd.DataFrame, path: str) -> None:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    df.to_csv(path, index=False)


def _is_true(x) -> bool:
    return str(x).strip().lower() == "true"


def add_picks(df: pd.DataFrame, picks: list[dict], session: str, regime: dict,
              vetoed: bool = False) -> pd.DataFrame:
    have = set(zip(df["session"].astype(str), df["symbol"].astype(str)))
    rows = [{"session": session, "symbol": p["symbol"], "company": p.get("company"),
             "industry": p.get("industry"), "vetoed": vetoed,
             "news_verdict": (p.get("news") or {}).get("verdict"),
             "news_headline": ((p.get("news") or {}).get("key_headline") or "")[:200],
             "setup": p["setup"], "score": p["score"], "regime": regime["regime"],
             "size_mult": regime.get("size_mult"), "trigger": round(p["trigger"], 2),
             "max_entry": round(p["max_entry"], 2), "stop": round(p["stop"], 2),
             "t1": round(p["t1"], 2), "risk_per_share": round(p["risk_per_share"], 2),
             "qty": p.get("qty"), "status": "pending"}
            for p in picks if (session, p["symbol"]) not in have]
    if not rows:
        return df
    new = pd.DataFrame(rows, columns=COLUMNS).astype(object)
    return new if df.empty else pd.concat([df.astype(object), new], ignore_index=True)


def update(df: pd.DataFrame, feats: dict[str, pd.DataFrame], cfg: dict) -> pd.DataFrame:
    """Re-simulate every unfinished trade with the latest prices."""
    if df.empty:
        return df
    df = df.astype(object).copy()
    cache = {}
    now = datetime.now().isoformat(timespec="minutes")
    for ix, row in df.iterrows():
        if str(row["status"] if pd.notna(row["status"]) else "") not in OPEN_STATES:
            continue
        f = feats.get(row["symbol"])
        if f is None:
            continue
        pos = f.index.get_indexer([pd.Timestamp(row["session"])])[0]
        if pos < 0:
            continue
        A = cache.setdefault(row["symbol"], arrays(f))
        res = simulate(A, int(pos), {"trigger": float(row["trigger"]),
                                     "max_entry": float(row["max_entry"]),
                                     "stop": float(row["stop"])}, cfg)
        res.pop("exit_i", None)
        res.pop("partial_taken", None)
        for k, v in res.items():
            if k in df.columns:
                df.at[ix, k] = v
        df.at[ix, "updated"] = now
    return df


def summary(df: pd.DataFrame) -> dict:
    if df.empty:
        return {"overall": {"trades": 0}, "by_setup": {}, "counts": {}}
    vet = df["vetoed"].map(_is_true)
    live = df[~vet]
    closed = live[live["status"] == "closed"].copy()
    closed["r_multiple"] = closed["r_multiple"].astype(float)
    vclosed = df[vet & (df["status"] == "closed")].copy()
    vclosed["r_multiple"] = vclosed["r_multiple"].astype(float)
    return {"overall": trade_stats(closed),
            "by_setup": {k: trade_stats(g) for k, g in closed.groupby("setup")},
            "counts": live["status"].value_counts().to_dict(),
            # what the news veto avoided: would-be results of vetoed trades
            "news_vetoed": trade_stats(vclosed),
            "open": live[live["status"] == "open"][["symbol", "session", "fill_price", "exit_price",
                                                    "r_multiple"]].to_dict("records")}


def read_state(state_path: str) -> dict:
    try:
        return json.loads(Path(state_path).read_text())
    except (OSError, ValueError):
        return {}


def write_state(state_path: str, **updates) -> None:
    st = read_state(state_path)
    st.update(updates)
    Path(state_path).parent.mkdir(parents=True, exist_ok=True)
    Path(state_path).write_text(json.dumps(st))


def last_session(state_path: str) -> str | None:
    return read_state(state_path).get("last_session")


def mark_session(state_path: str, session: str) -> None:
    write_state(state_path, last_session=session)
