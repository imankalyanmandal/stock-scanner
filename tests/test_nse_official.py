"""Offline tests for the light NSE MCP integration (no network)."""
import copy
from datetime import date

import pandas as pd
import pytest
import yaml

from scanner import enrich as E
from scanner import nse_mcp as M
from scanner import nse_official as NO
from scanner import pipeline

CFG = yaml.safe_load(open("config.yaml"))


def frame(closes, end="2026-11-06"):
    idx = pd.bdate_range(end=end, periods=len(closes))
    return pd.DataFrame({"Open": closes, "High": closes, "Low": closes, "Close": closes,
                         "Volume": [1000.0] * len(closes)}, index=idx)


def bar(close, d="2026-11-09"):
    return {"date": pd.Timestamp(d), "Open": close, "High": close, "Low": close, "Close": close,
            "Volume": 5000.0}


class FakeBudget:
    """Stands in for nse_official.Budget, answering by tool name."""

    def __init__(self, answers, fail_on=None):
        self.answers, self.fail_on = answers, fail_on or {}
        self.calls, self.blocked, self.log = 0, False, []

    def call(self, name, **args):
        self.calls += 1
        self.log.append((name, args))
        if name in self.fail_on:
            err = self.fail_on[name]
            if "403" in err:
                self.blocked = True
            raise M.MCPError(err)
        a = self.answers[name]
        return a(**args) if callable(a) else a


def test_patch_appends_missing_session_and_corrects_bad_close():
    prices = {"AAA": frame([100.0] * 5), "BBB": frame([50.0] * 5, end="2026-11-09"),
              "CCC": frame([20.0] * 5, end="2026-11-09"), "^NSEI": frame([1.0] * 5)}
    bars = {"AAA": bar(101.0), "BBB": bar(55.0), "CCC": bar(20.05)}
    st = NO.patch_last_bar(prices, bars, date(2026, 11, 9))
    assert prices["AAA"].index[-1] == pd.Timestamp("2026-11-09")
    assert prices["AAA"]["Close"].iloc[-1] == 101.0 and len(prices["AAA"]) == 6
    assert prices["BBB"]["Close"].iloc[-1] == 55.0 and len(prices["BBB"]) == 5
    assert prices["CCC"]["Close"].iloc[-1] == 20.0          # within 0.5%: Yahoo kept
    assert st["appended"] == ["AAA"]
    assert [c["symbol"] for c in st["corrected"]] == ["BBB"]
    assert st["matched"] == 1 and st["nse_missing"] == 0     # benchmarks are skipped


def test_patch_does_not_append_a_session_other_than_expected():
    prices = {"AAA": frame([100.0] * 5)}
    NO.patch_last_bar(prices, {"AAA": bar(101.0, "2026-11-10")}, date(2026, 11, 9))
    assert prices["AAA"].index[-1] == pd.Timestamp("2026-11-06")


def test_official_bars_chunks_and_halves_on_error():
    seen = []

    def quotes(symbols):
        seen.append(len(symbols))
        if len(symbols) > 12:
            raise M.MCPError("too many")
        return {"quotes": [{"symbol": s, "close": 10.0, "open": 9, "high": 11, "low": 8,
                            "volume": 7, "date": "2026-11-09"} for s in symbols]}
    b = FakeBudget({"get_bulk_quote": quotes})
    got = NO.official_bars(b, [f"S{i}" for i in range(30)], chunk=24)
    assert set(got) == {f"S{i}" for i in range(30)}
    assert seen[0] == 24 and max(seen[1:]) <= 12
    assert got["S0"]["Close"] == 10.0 and got["S0"]["date"] == pd.Timestamp("2026-11-09")


def test_official_bars_stops_when_blocked():
    b = FakeBudget({}, fail_on={"get_bulk_quote": "HTTP 403: Access Denied"})
    assert NO.official_bars(b, ["A", "B"], chunk=50) == {} and b.calls == 1


def test_index_history_chains_chunks():
    def hist(indexName, months, endDate):
        end = pd.Timestamp(endDate)
        idx = pd.bdate_range(end=end, periods=250)
        return {"data": [{"date": str(d.date()), "open": 1, "high": 2, "low": 0.5, "close": 1.5}
                         for d in idx],
                "next_end_date": str((idx[0] - pd.Timedelta(days=1)).date())}
    b = FakeBudget({"get_index_history": hist})
    df = NO.index_history(b, "India VIX", "2y", end=date(2026, 11, 9))
    assert b.calls == 3 and len(df) > 480
    assert df.index.is_monotonic_increasing and df["Volume"].eq(0).all()


def test_budget_stops_after_403_and_at_cap(monkeypatch):
    class Client:
        def __init__(self, *a, **k):
            self.n = 0

        def call(self, name, **a):
            self.n += 1
            if name == "deny":
                raise M.MCPError("HTTP 403: Access Denied")
            return 1
    monkeypatch.setattr(NO, "NSEMCP", Client)
    cfg = copy.deepcopy(CFG)
    cfg["data"]["nse_mcp"]["max_calls"] = 2
    b = NO.Budget(cfg)
    assert b.call("ok") == 1 and b.call("ok") == 1
    with pytest.raises(M.MCPError, match="budget"):
        b.call("ok")
    b = NO.Budget(cfg)
    with pytest.raises(M.MCPError):
        b.call("deny")
    assert b.blocked
    with pytest.raises(M.MCPError, match="blocked"):
        b.call("ok")
    assert b.client.n == 1


def test_apply_nse_official_swaps_benchmarks_and_patches(monkeypatch):
    cfg = copy.deepcopy(CFG)
    bench = frame([25000.0] * 300, end="2026-11-09")
    monkeypatch.setattr(NO, "Budget", lambda c: FakeBudget({}))
    monkeypatch.setattr(NO, "benchmarks", lambda b, t, p: {"^NSEI": bench, "^INDIAVIX": bench})
    monkeypatch.setattr(NO, "official_bars", lambda b, s, chunk: {"AAA": bar(101.0)})
    prices = {"AAA": frame([100.0] * 5), "^NSEI": frame([1.0] * 5), "^INDIAVIX": frame([1.0] * 5)}
    b, st = pipeline.apply_nse_official(cfg, prices, ["AAA"], pd.Timestamp("2026-11-09"))
    assert prices["^NSEI"] is bench and st["benchmarks_from_nse"] == ["^INDIAVIX", "^NSEI"]
    assert st["official_close"]["appended"] == ["AAA"]
    assert prices["AAA"].index[-1] == pd.Timestamp("2026-11-09")
    assert b is not None


def test_apply_nse_official_failure_keeps_yfinance(monkeypatch):
    cfg = copy.deepcopy(CFG)
    monkeypatch.setattr(NO, "Budget", lambda c: FakeBudget({}))

    def boom(*a, **k):
        raise RuntimeError("down")
    monkeypatch.setattr(NO, "benchmarks", boom)
    orig = frame([1.0] * 5)
    prices = {"^NSEI": orig}
    b, st = pipeline.apply_nse_official(cfg, prices, [], None)
    assert prices["^NSEI"] is orig and "error" in st
    cfg["data"]["nse_mcp"]["enabled"] = False
    assert pipeline.apply_nse_official(cfg, prices, [], None) == (None, {"enabled": False})


def test_upcoming_events_nse_dividends_and_fallback(monkeypatch):
    acts = {"actions": [{"exDate": "2026-11-20", "actionType": "DIVIDEND"},
                        {"exDate": "2026-11-21", "actionType": "BONUS"},
                        {"exDate": "2027-03-01", "actionType": "DIVIDEND"}]}
    monkeypatch.setattr(E, "_yf_events", lambda *a: [{"type": "earnings", "date": "2026-11-15"},
                                                     {"type": "ex_dividend", "date": "2026-11-12"}])
    b = FakeBudget({"get_corporate_actions": acts})
    ev = E.upcoming_events("ABC", date(2026, 11, 10), 21, b, True)
    assert {"type": "ex_dividend", "date": "2026-11-20", "source": "nse"} in ev
    assert {"type": "earnings", "date": "2026-11-15"} in ev
    assert not any(e["date"] == "2026-11-12" for e in ev)   # Yahoo dividends not mixed in
    assert all(e["type"] == "ex_dividend" for e in E.upcoming_events("ABC", date(2026, 11, 10), 21, b, False))
    broken = FakeBudget({}, fail_on={"get_corporate_actions": "HTTP 403"})
    assert len(E.upcoming_events("ABC", date(2026, 11, 10), 21, broken, False)) == 2
    assert len(E.upcoming_events("ABC", date(2026, 11, 10), 21)) == 2       # no NSE: Yahoo


def test_mcp_client_parses_sse_and_structured(monkeypatch):
    class R:
        def __init__(self, body, ctype="text/event-stream", status=200, headers=None):
            self.text, self.content, self.status_code = body, body.encode(), status
            self.headers = {"Content-Type": ctype, **(headers or {})}

        def json(self):
            import json
            return json.loads(self.text)
    replies = iter([
        R('data: {"jsonrpc":"2.0","id":1,"result":{}}\n\n', headers={"Mcp-Session-Id": "s1"}),
        R("", status=202),
        R('event: message\ndata: {"jsonrpc":"2.0","id":2,"result":{"content":[{"type":"text",'
          '"text":"{\\"quotes\\": [{\\"symbol\\": \\"TCS\\", \\"close\\": 1}]}"}]}}\n\n'),
        R('{"jsonrpc":"2.0","id":3,"result":{"structuredContent":{"result":1324.4}}}',
          ctype="application/json"),
        R('{"jsonrpc":"2.0","id":4,"result":{"isError":true,"content":[{"type":"text","text":"bad"}]}}',
          ctype="application/json"),
    ])
    c = M.NSEMCP("bhavcopy", delay=0, retries=0)
    monkeypatch.setattr(c.s, "post", lambda *a, **k: next(replies))
    assert M.rows(c.call("get_bulk_quote", symbols=["TCS"]), "quotes")[0]["symbol"] == "TCS"
    assert c.s.headers["Mcp-Session-Id"] == "s1"
    assert c.call("get_ltp_by_date", symbol="TCS", date="2026-11-09") == 1324.4
    with pytest.raises(M.MCPError):
        c.call("get_bulk_quote", symbols=["X"])
