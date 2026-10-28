"""Offline tests on synthetic price data (no network)."""
import copy
import json
import math
from datetime import datetime, timedelta

import numpy as np
import pandas as pd
import pytest
import yaml

from scanner import journal as J
from scanner import pipeline
from scanner.filters import arrays, evaluate, features, metrics_at, select
from scanner.market import regime_at
from scanner.trade import simulate, size_position

CFG = yaml.safe_load(open("config.yaml"))
CFG_PB = copy.deepcopy(CFG)
CFG_PB["setups"]["pullback"]["enabled"] = True
END = "2026-10-23"


# ------------------------------------------------------------------ synthetic data
def ohlc(close, vol=2_000_000, rng_pct=0.012, seed=0, end=END):
    """OHLC around a close path: open = prev close, symmetric-ish range."""
    close = np.asarray(close, dtype=float)
    rng = np.random.default_rng(seed)
    open_ = np.r_[close[0], close[:-1]]
    spread = close * rng_pct * (0.7 + 0.6 * rng.random(len(close)))
    high = np.maximum(open_, close) + spread * 0.35
    low = np.minimum(open_, close) - spread * 0.35
    v = np.full(len(close), float(vol)) if np.isscalar(vol) else np.asarray(vol, dtype=float)
    idx = pd.bdate_range(end=end, periods=len(close))
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": v},
                        index=idx)


def uptrend(n=300, start=200.0, drift=0.0025, noise=0.006, seed=1):
    rng = np.random.default_rng(seed)
    return start * np.cumprod(1 + rng.normal(drift, noise, n))


def breakout_df(seed=1, bo_vol=2.5, bo_gain=0.02):
    up = uptrend(300, seed=seed)
    lvl = up[-1]
    rng = np.random.default_rng(seed)
    base = lvl * (1 + 0.015 * np.sin(np.arange(20) / 1.5) + rng.normal(0, 0.005, 20))  # 20-bar base
    close = np.r_[up, base, base.max() * (1 + bo_gain)]
    vol = np.r_[np.full(300, 2e6), np.full(20, 1.4e6), [2e6 * bo_vol]]
    df = ohlc(close, vol, seed=seed)
    df.iloc[-1, df.columns.get_loc("High")] = df["Close"].iloc[-1] * 1.002   # close near high
    df.iloc[-1, df.columns.get_loc("Low")] = df["Open"].iloc[-1] * 0.997
    return df


def pullback_df(seed=2, drop=(-0.006, -0.006, -0.005), rev=0.015, pb_vol=0.6):
    up = uptrend(300, seed=seed, noise=0.004)
    c = list(up)
    for r in drop:
        c.append(c[-1] * (1 + r))
    c.append(c[-1] * (1 + rev))
    n_pb = len(drop)
    vol = np.r_[np.full(300, 2e6), np.full(n_pb, 2e6 * pb_vol), [3e6]]
    df = ohlc(c, vol, seed=seed, rng_pct=0.01)
    df.iloc[-1, df.columns.get_loc("High")] = df["Close"].iloc[-1] * 1.001
    return df


def bench_df(n=330):
    return ohlc(uptrend(n, start=20000, drift=0.0008, seed=99))


def feats_for(df, cfg=CFG):
    return features(df, cfg, bench_df(len(df) + 10)["Close"])


def last_metrics(df, cfg=CFG, rank=90.0):
    f = feats_for(df, cfg)
    return metrics_at(arrays(f), len(f) - 1, cfg, rank), f


# ------------------------------------------------------------------ signals
def test_breakout_detected_and_passes():
    m, _ = last_metrics(breakout_df())
    assert m["setup"] == "breakout" and m["bo_day"] == 1
    assert m["base_depth_pct"] < 5 and m["setup_vol_ratio"] > 2
    assert m["stop"] < m["close"] < m["trigger"] < m["max_entry"]
    assert math.isclose(m["t1"] - m["trigger"], 2 * m["risk_per_share"], rel_tol=1e-9)
    ev = evaluate(m, CFG)
    assert not ev["reject_reasons"], ev["reject_reasons"]
    assert "blue_sky" in ev["flags"]


def test_breakout_low_volume_rejected():
    m, _ = last_metrics(breakout_df(bo_vol=1.0))
    assert m["setup"] == "breakout"
    assert "breakout_low_volume" in evaluate(m, CFG)["reject_reasons"]


def test_breakout_day3_is_not_a_setup():
    df = breakout_df()
    last = df["Close"].iloc[-1]
    extra = ohlc(np.r_[df["Close"].to_numpy(), last * 1.005, last * 1.01])
    m, _ = last_metrics(extra)
    assert m["setup"] != "breakout"


def test_pullback_detected():
    m, _ = last_metrics(pullback_df(), CFG_PB)
    assert m["setup"] == "pullback", m["setup_fail"]
    assert m["pb_bars"] >= 2 and m["setup_vol_ratio"] < 1
    assert -0.75 <= m["touch_offset_atr"] <= 0.5


def test_deep_undercut_is_not_a_pullback():
    m, _ = last_metrics(pullback_df(drop=(-0.02, -0.03, -0.03, -0.02), rev=0.01), CFG_PB)
    assert m["setup"] == "none"
    assert m["setup_fail"] in {"pb_broke_20dma", "pb_too_deep"}


def test_heavy_volume_pullback_rejected():
    m, _ = last_metrics(pullback_df(pb_vol=1.6), CFG_PB)
    assert m["setup"] == "pullback"
    assert "pullback_heavy_volume" in evaluate(m, CFG_PB)["reject_reasons"]


def test_pullbacks_disabled_by_default():
    m, _ = last_metrics(pullback_df())
    assert m["setup"] == "none" and m["setup_fail"] == "pullbacks_disabled"


def test_downtrend_rejected_by_trend_template():
    down = 500 * np.cumprod(1 + np.random.default_rng(3).normal(-0.002, 0.008, 320))
    down[-2:] = down[-3] * np.array([1.01, 1.025])
    m, _ = last_metrics(ohlc(down))
    r = evaluate(m, CFG)["reject_reasons"]
    assert "below_50dma" in r and "50dma_below_200dma" in r


def test_weak_rs_rank_rejected():
    m, _ = last_metrics(breakout_df(), rank=40)
    assert "weak_rs_rank" in evaluate(m, CFG)["reject_reasons"]


def test_resistance_just_above_entry_is_counted():
    """A swing high within 0.5 ATR of the trigger must still count as resistance."""
    df = pullback_df()
    m, f = last_metrics(df, CFG_PB)
    near = m["trigger"] + 0.3 * m["atr"]
    i_hi = len(df) - 1 - m["pb_bars"] - 1
    df.iloc[i_hi, df.columns.get_loc("High")] = near
    m2, _ = last_metrics(df, CFG_PB)
    assert m2["resistance"] is not None and m2["resistance"] <= near + 1e-9
    assert m2["room_r"] < 0.5
    assert "resistance_too_close" in evaluate(m2, CFG_PB)["reject_reasons"]


def test_no_lookahead():
    """Metrics at bar i are identical whether or not later bars exist."""
    df = pullback_df()
    full = feats_for(df)
    i = len(df) - 8
    trunc = features(df.iloc[:i + 1], CFG, bench_df(len(df) + 10)["Close"])
    a = metrics_at(arrays(full), i, CFG, 80)
    b = metrics_at(arrays(trunc), i, CFG, 80)
    for k, v in a.items():
        if isinstance(v, float):
            assert (math.isnan(v) and math.isnan(b[k])) or math.isclose(v, b[k], rel_tol=1e-9, abs_tol=1e-12), k
        else:
            assert v == b[k], k


def test_locked_circuit_rejected():
    df = breakout_df()
    k = len(df) - 5
    for col in ("Open", "High", "Low"):
        df.iloc[k, df.columns.get_loc(col)] = df["Close"].iloc[k]
    m, _ = last_metrics(df)
    assert "circuit_history" in evaluate(m, CFG)["reject_reasons"]


def test_select_caps_sector_and_top_n():
    rows = [{"symbol": f"S{i}", "industry": "Banks" if i < 4 else f"I{i}", "score": 90 - i}
            for i in range(10)]
    picks, dropped = select(rows, CFG)
    assert len(picks) == CFG["selection"]["top_n"]
    assert sum(p["industry"] == "Banks" for p in picks) == CFG["selection"]["max_per_sector"]
    assert any("sector_cap" in d["reasons"] for d in dropped)


# ------------------------------------------------------------------ regime + sizing
def _b(close, s20, s50):
    return {"close": close, "sma20": s20, "sma50": s50}


def test_regime_rules():
    up = {"nifty50": _b(110, 105, 100), "nifty500": _b(110, 105, 100)}
    assert regime_at(up, 70, 14, CFG)["size_mult"] == 1.0
    assert regime_at(up, 50, 14, CFG)["size_mult"] == CFG["market"]["neutral_size"]
    weak = regime_at({"nifty50": _b(95, 98, 100)}, 70, 14, CFG)
    assert weak["regime"] == "weak" and not weak["allow_new_longs"]
    assert regime_at(up, 70, 22, CFG)["size_mult"] == CFG["market"]["vix_high_size"]
    assert not regime_at(up, 70, 30, CFG)["allow_new_longs"]


def test_position_sizing():
    s = size_position(100.0, 95.0, CFG, 1.0)     # 1% of 10L = 10,000 / 5 = 2000 sh
    assert s["qty"] == 2000 and s["risk_amount"] == 10000
    s = size_position(100.0, 99.5, CFG, 1.0)     # would be 20,000 sh; capped at 20% = 2000 sh
    assert s["qty"] == 2000 and s["capped"]
    assert size_position(100.0, 95.0, CFG, 0.5)["qty"] == 1000


# ------------------------------------------------------------------ trade simulation
def _bars(rows, trail=None):
    """rows: list of (open, high, low, close); bar 0 is the signal bar."""
    df = pd.DataFrame(rows, columns=["open", "high", "low", "close"],
                      index=pd.bdate_range("2026-01-01", periods=len(rows)))
    df["ema20"] = trail if trail is not None else df["close"] * 0.9
    df["ema10"] = df["ema20"]
    return arrays(df)


PLAN = {"trigger": 100.0, "max_entry": 101.5, "stop": 95.0}


def test_sim_partial_then_trail():
    A = _bars([(98, 99, 97, 98.5), (99.5, 101, 99, 100.5), (101, 106, 100.5, 105),
               (105, 111, 104, 110), (110, 112, 108, 109)],
              trail=[0, 0, 0, 0, 109.5])
    r = simulate(A, 0, PLAN, CFG)
    assert r["status"] == "closed" and r["exit_reason"] == "trail"
    assert r["partial_taken"] and r["fill_price"] == 100.0
    # half at 110 (2R), half at 109 -> +1.9R gross, minus costs
    assert math.isclose(r["r_multiple"], 1.9 - 0.25 / 100 * 100 / 5, abs_tol=1e-3)


def test_sim_gap_above_max_entry_skipped():
    A = _bars([(98, 99, 97, 98.5), (102, 104, 101.8, 103)])
    assert simulate(A, 0, PLAN, CFG)["status"] == "skipped_gap"


def test_sim_not_triggered_and_pending():
    A = _bars([(98, 99, 97, 98.5), (98, 99.5, 97, 99)])
    assert simulate(A, 0, PLAN, CFG)["status"] == "not_triggered"
    assert simulate(_bars([(98, 99, 97, 98.5)]), 0, PLAN, CFG)["status"] == "pending"


def test_sim_stop_on_fill_day_and_gap_stop():
    A = _bars([(98, 99, 97, 98.5), (99, 100.5, 94, 95.5)])
    r = simulate(A, 0, PLAN, CFG)
    assert r["exit_reason"] == "stop" and r["r_multiple"] < -1
    A = _bars([(98, 99, 97, 98.5), (99, 100.5, 99, 100), (93, 94, 92, 93)])
    r = simulate(A, 0, PLAN, CFG)
    assert r["exit_reason"] == "gap_stop" and math.isclose(r["exit_price"], 93)


def test_sim_time_stop():
    rows = [(98, 99, 97, 98.5), (99.5, 100.5, 99, 100.2)] + [(100, 101, 99.5, 100.3)] * 9
    r = simulate(_bars(rows), 0, PLAN, CFG)
    assert r["exit_reason"] == "time_stop" and r["bars_held"] == CFG["exits"]["time_stop_days"]


# ------------------------------------------------------------------ journal + pipeline
def _universe(n):
    syms = [f"S{i}" for i in range(n)]
    return syms, pd.DataFrame({"symbol": syms, "company": [f"Co {s}" for s in syms],
                               "industry": [f"Ind{i % 6}" for i in range(n)],
                               "indexes": "nifty50"})


def _market(syms):
    data = {}
    for i, s in enumerate(syms):
        if i % 3 == 0:
            data[s] = breakout_df(seed=i + 10)
        elif i % 3 == 1:
            data[s] = pullback_df(seed=i + 10)
        else:
            data[s] = ohlc(500 * np.cumprod(1 + np.random.default_rng(i).normal(-0.001, 0.01, 321)))
    data["^NSEI"] = data["^CRSLDX"] = bench_df(321)
    data["^INDIAVIX"] = ohlc(np.full(321, 14.0))
    return data


def _cfg(tmp_path):
    cfg = copy.deepcopy(CFG)
    cfg["output"]["dir"] = str(tmp_path / "reports")
    cfg["journal"] = {"path": str(tmp_path / "journal.csv"), "state_path": str(tmp_path / "state.json")}
    cfg["universe"]["exclude_file"] = str(tmp_path / "none.csv")
    cfg["macro"]["enabled"] = False      # network; tested separately with mocks
    return cfg


def test_pipeline_end_to_end_and_journal(monkeypatch, tmp_path):
    syms, uni = _universe(30)
    data = _market(syms)
    monkeypatch.setattr(pipeline, "load_universe", lambda *a, **k: uni)
    monkeypatch.setattr(pipeline, "download_prices", lambda s, **k: {x: data[x] for x in s if x in data})
    monkeypatch.setattr(pipeline, "fetch_delivery", lambda d: {s: 50.0 for s in syms})
    monkeypatch.setattr(pipeline, "upcoming_events", lambda *a: [])
    cfg = _cfg(tmp_path)
    res = pipeline.run(cfg, with_news=False)
    assert res["session"] == END
    assert len(res["picks"]) <= cfg["selection"]["top_n"]
    assert res["picks"], "synthetic breakouts should produce picks"
    for p in res["picks"]:
        assert not p["reject_reasons"] and p["qty"] > 0 and "Buy-stop" in p["exit_plan"]
    assert (tmp_path / "reports" / f"scan_{END}.html").exists()
    j = J.load(cfg["journal"]["path"])
    assert len(j) == len(res["picks"]) and set(j["status"]) == {"pending"}

    res2 = pipeline.run(cfg, with_news=False)            # same session again
    assert res2["duplicate"]
    assert len(J.load(cfg["journal"]["path"])) == len(j)


def test_earnings_in_window_rejected(monkeypatch, tmp_path):
    syms, uni = _universe(12)
    data = _market(syms)
    monkeypatch.setattr(pipeline, "load_universe", lambda *a, **k: uni)
    monkeypatch.setattr(pipeline, "download_prices", lambda s, **k: {x: data[x] for x in s if x in data})
    monkeypatch.setattr(pipeline, "fetch_delivery", lambda d: {})
    monkeypatch.setattr(pipeline, "upcoming_events",
                        lambda *a: [{"type": "earnings", "date": "2026-10-28"}])
    res = pipeline.run(_cfg(tmp_path), with_news=False)
    assert not res["picks"]
    assert any("earnings_in_hold_window" in r["reasons"] for r in res["rejected"])


def test_weak_market_blocks_new_longs(monkeypatch, tmp_path):
    syms, uni = _universe(12)
    data = _market(syms)
    falling = ohlc(20000 * np.cumprod(np.full(321, 0.998)))
    data["^NSEI"] = data["^CRSLDX"] = falling
    monkeypatch.setattr(pipeline, "load_universe", lambda *a, **k: uni)
    monkeypatch.setattr(pipeline, "download_prices", lambda s, **k: {x: data[x] for x in s if x in data})
    monkeypatch.setattr(pipeline, "fetch_delivery", lambda d: {})
    monkeypatch.setattr(pipeline, "upcoming_events", lambda *a: [])
    res = pipeline.run(_cfg(tmp_path), with_news=False)
    assert res["regime"]["regime"] == "weak" and not res["picks"]
    assert "No new longs" in res["note"]


def test_journal_update_closes_trades():
    df = breakout_df()
    f = feats_for(df)
    i = len(f) - 1
    m = metrics_at(arrays(f), i, CFG, 90)
    jdf = J.add_picks(J.load("/nonexistent.csv"), [{"symbol": "X", "setup": "breakout", "score": 70,
                                                     **m, "qty": 10}], m["last_date"],
                      {"regime": "strong", "size_mult": 1})
    trig = m["trigger"]
    later = [(trig * 0.999, trig * 1.01, trig * 0.995, trig * 1.008)] + \
            [(trig * (1 + 0.02 * k), trig * (1 + 0.02 * k + 0.01), trig * (1 + 0.02 * k - 0.005),
              trig * (1 + 0.02 * k + 0.008)) for k in range(1, 12)] + \
            [(trig * 1.15, trig * 1.15, trig * 0.9, trig * 0.95)]
    ext = pd.concat([df, pd.DataFrame(later, columns=["Open", "High", "Low", "Close"],
                                      index=pd.bdate_range(df.index[-1] + pd.offsets.BDay(),
                                                           periods=len(later)))
                     .assign(Volume=2e6)])
    jdf = J.update(jdf, {"X": feats_for(ext)}, CFG)
    row = jdf.iloc[0]
    assert row["status"] == "closed" and float(row["r_multiple"]) > 0
    s = J.summary(jdf)
    assert s["overall"]["trades"] == 1 and s["overall"]["win_rate_pct"] == 100.0


def test_backtest_runs(monkeypatch, tmp_path):
    from scanner import backtest
    syms, uni = _universe(18)
    data = {}
    for i, s in enumerate(syms):   # long trending series with several bases/breakouts
        rng = np.random.default_rng(i)
        c = 100 * np.cumprod(1 + rng.normal(0.0012 if i % 2 else 0.0, 0.013, 700))
        data[s] = ohlc(c, vol=2e6 * (0.6 + rng.random(700)), seed=i)
    data["^NSEI"] = data["^CRSLDX"] = ohlc(uptrend(700, start=20000, drift=0.0006, seed=5))
    data["^INDIAVIX"] = ohlc(np.full(700, 15.0))
    monkeypatch.setattr(pipeline, "download_prices", lambda s, **k: {x: data[x] for x in s if x in data})
    cfg = _cfg(tmp_path)
    res = backtest.run_backtest(cfg, universe=uni)
    assert res["days"] > 300
    assert "overall" in res and (tmp_path / "reports").exists()
    if res["overall"]["trades"]:
        o = res["overall"]
        assert 0 <= o["win_rate_pct"] <= 100
        assert o["avg_loss_r"] <= 0 <= o["avg_win_r"]


# ------------------------------------------------------------------ news + telegram
def test_news_parsing(monkeypatch):
    from datetime import datetime, timedelta, timezone
    from email.utils import format_datetime
    from scanner import enrich
    pub = format_datetime(datetime.now(timezone.utc) - timedelta(hours=5))
    rss = f"""<rss><channel><item><title>Acme wins big order</title>
    <link>https://example.com/a</link><pubDate>{pub}</pubDate>
    <source url="https://x">Example News</source></item></channel></rss>""".encode()

    class R:
        content = rss
        def raise_for_status(self): pass
    monkeypatch.setattr(enrich.requests, "get", lambda *a, **k: R())
    n = enrich.fetch_news("Acme Industries Limited", "ACME", 3, 48, 5)
    assert n["has_news"] and n["fresh"]


def test_telegram_message_and_send(monkeypatch, tmp_path):
    from scanner import notify
    pick = {"symbol": "ACME", "setup": "breakout", "score": 78.5, "rs_rank": 91.0,
            "trigger": 512.3, "max_entry": 520.0, "stop": 498.0, "t1": 540.9, "qty": 120,
            "flags": ["abnormal_gap", "blue_sky"],
            "news": {"verdict": "positive", "key_headline": "Acme <wins> order & more",
                     "items": [{"title": "Acme <wins> order & more"}]}}
    result = {"session": END, "evaluated": 248, "passed_gates": 9,
              "regime": {"regime": "neutral", "breadth_pct": 52.0, "vix": 14.2, "size_mult": 0.5,
                         "benchmarks": {"nifty50": {"above_20": True, "above_50": False}}},
              "picks": [pick] * 8,
              "news_vetoed": [{"symbol": "BADCO", "headline": "SEBI order against BadCo", "reason": "x"}],
              "journal": {"overall": {"trades": 12, "win_rate_pct": 50.0, "avg_win_r": 2.1,
                                      "avg_loss_r": -0.9, "expectancy_r": 0.6}}}
    text = notify.format_summary(result, max_picks=5)
    assert "ACME" in text and "&lt;wins&gt;" in text and "3 more" in text
    assert "Journal: 12 closed" in text and "Buy &gt; 512.30" in text
    assert "BADCO</b> rejected on news" in text and "📰 🟢 positive" in text
    assert len(text) <= notify.MAX_LEN

    calls = []

    class R:
        ok = True
        status_code = 200
        def json(self): return {"ok": True}
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(notify.requests, "post",
                        lambda url, **k: calls.append(url.rsplit("/", 1)[1]) or R())
    report = tmp_path / "scan.html"
    report.write_text("<html></html>")
    status = notify.send_scan(result, {"notify": {"telegram": {}}}, report)
    assert calls == ["sendMessage", "sendDocument"]
    assert status == {"sendMessage": "ok", "sendDocument": "ok"}

    class Bad:
        ok = False
        status_code = 400
        text = "{}"
        def json(self): return {"ok": False, "description": "Bad Request: chat not found"}
    monkeypatch.setattr(notify.requests, "post", lambda url, **k: Bad())
    status = notify.send_scan(result, {"notify": {"telegram": {"send_report_file": False}}})
    assert status == {"sendMessage": "error 400: Bad Request: chat not found"}


def test_telegram_skipped_without_creds(monkeypatch):
    from scanner import notify
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setattr(notify.requests, "post", lambda *a, **k: pytest.fail("should not post"))
    assert notify._call("sendMessage", {"text": "x"}) is False


# ------------------------------------------------------------------ experiments + momentum
def test_undercut_base_flag_and_gate():
    df = breakout_df()
    m, _ = last_metrics(df)
    assert m["base_undercut"] in (True, False)
    # force the second half of the base to undercut the first half's low
    k = len(df) - 4
    df.iloc[k, df.columns.get_loc("Low")] = df["Low"].iloc[-21:-1].min() * 0.97
    m2, _ = last_metrics(df)
    assert m2["base_undercut"]
    cfg = copy.deepcopy(CFG)
    cfg["setups"]["breakout"]["reject_undercut_base"] = True
    assert "base_undercut" in evaluate(m2, cfg)["reject_reasons"]
    assert "base_undercut" not in evaluate(m2, CFG)["reject_reasons"]


def test_momentum_rank_and_leader_gate():
    from scanner.filters import momentum_rank_table
    feats = {}
    for i in range(8):
        c = 100 * np.cumprod(np.full(320, 1 + 0.0005 * i) * (1 + np.random.default_rng(i).normal(0, 0.005, 320)))
        feats[f"S{i}"] = feats_for(ohlc(c, seed=i))
    ranks = momentum_rank_table(feats, CFG).iloc[-1]
    assert ranks["S7"] == 1 and ranks["S0"] == 8
    m, _ = last_metrics(breakout_df())
    cfg = copy.deepcopy(CFG)
    cfg["rs"]["momentum_top_n"] = 3
    m["mom_rank"] = 5
    assert "not_momentum_leader" in evaluate(m, cfg)["reject_reasons"]
    m["mom_rank"] = 2
    assert "not_momentum_leader" not in evaluate(m, cfg)["reject_reasons"]


def test_momentum_rotation_math():
    """Strongest stock held; equity tracks it minus costs; regime filter moves to cash."""
    from scanner.momentum import run_momentum
    n = 700
    feats = {}
    for i in range(6):
        g = 1 + 0.0002 * i
        c = 100 * g ** np.arange(n) * (1 + np.random.default_rng(i).normal(0, 0.002, n))
        feats[f"S{i}"] = feats_for(ohlc(c, seed=i, end=END))
    bench = {"nifty50": ohlc(20000 * 1.0003 ** np.arange(n))}
    cfg = copy.deepcopy(CFG)
    cfg["momentum"].update({"top_n": 1, "exit_rank": 1, "cash_yield_pct": 0, "regime_filter": True})
    res = run_momentum(feats, bench, cfg)
    assert res["rebalances"] > 10 and res["months_in_cash_pct"] == 0
    s5 = feats["S5"]["close"]
    days = res["period"]
    ref = s5.loc[days[1]] / s5.loc[:days[0]].iloc[-1] - 1
    assert abs(res["strategy"]["total_return_pct"] / 100 - ref) < 0.03

    falling = {"nifty50": ohlc(20000 * 0.9995 ** np.arange(n))}
    cfg["momentum"]["cash_yield_pct"] = 6.0
    res2 = run_momentum(feats, falling, cfg)
    assert res2["months_in_cash_pct"] == 100
    assert 5 < res2["strategy"]["cagr_pct"] < 7


def test_experiments_end_to_end(tmp_path):
    from scanner.experiments import deep_merge, run_experiments
    assert deep_merge({"a": {"b": 1, "c": 2}}, {"a": {"b": 3}}) == {"a": {"b": 3, "c": 2}}
    syms, uni = _universe(12)
    data = {}
    for i, s in enumerate(syms):
        rng = np.random.default_rng(i)
        c = 100 * np.cumprod(1 + rng.normal(0.0012 if i % 2 else 0.0, 0.013, 700))
        data[s] = ohlc(c, vol=2e6 * (0.6 + rng.random(700)), seed=i)
    data["^NSEI"] = data["^CRSLDX"] = ohlc(uptrend(700, start=20000, drift=0.0006, seed=5))
    data["^INDIAVIX"] = ohlc(np.full(700, 15.0))
    spec = {"period": "3y",
            "breakout_variants": {"base": {}, "undercut": {"setups": {"breakout": {"reject_undercut_base": True}}}},
            "momentum_variants": {"m": {"momentum": {"top_n": 3, "exit_rank": 5}}}}
    res = run_experiments(_cfg(tmp_path), spec, universe=uni, prices=data, out_dir=str(tmp_path))
    assert set(res["breakout"]) == {"base", "undercut"}
    assert res["momentum"]["m"]["strategy"]["cagr_pct"] is not None
    md = open(res["files"]["md"]).read()
    assert "Swing variants" in md and "Monthly momentum rotation" in md and "nifty50 (buy & hold)" in md


# ------------------------------------------------------------------ extra indicators
def test_indicators_on_known_patterns():
    from scanner.indicators import adx, bb_width, macd, supertrend
    n = 300
    up = ohlc(100 * 1.004 ** np.arange(n) * (1 + np.random.default_rng(1).normal(0, 0.003, n)))
    down = ohlc(100 * 0.996 ** np.arange(n) * (1 + np.random.default_rng(2).normal(0, 0.003, n)))
    flat = ohlc(100 * (1 + np.random.default_rng(3).normal(0, 0.006, n)))
    for name, df, st, m_pos in (("up", up, 1, True), ("down", down, -1, False)):
        line, sig, hist = macd(df["Close"])
        assert (line.iloc[-1] > 0) == m_pos, name
        assert supertrend(df["High"], df["Low"], df["Close"]).iloc[-1] == st, name
    a_up = adx(up["High"], up["Low"], up["Close"]).iloc[-1]
    a_flat = adx(flat["High"], flat["Low"], flat["Close"]).iloc[-1]
    assert a_up > 25 > a_flat
    # Bollinger width shrinks when volatility shrinks
    c = pd.Series(np.r_[100 * (1 + np.random.default_rng(4).normal(0, 0.02, 200)),
                        100 * (1 + np.random.default_rng(5).normal(0, 0.003, 40))])
    w = bb_width(c)
    assert w.iloc[-1] < w.iloc[150] / 3


def test_indicator_gates_only_when_enabled():
    m, _ = last_metrics(breakout_df())
    for k in ("macd_bull", "supertrend_up", "adx", "bb_squeeze"):
        assert k in m
    base = evaluate(m, CFG)["reject_reasons"]
    assert not {"macd_not_bullish", "supertrend_down", "adx_too_low", "no_bb_squeeze"} & set(base)
    cfg = copy.deepcopy(CFG)
    cfg["indicators"].update({"require_macd_bullish": True, "require_supertrend_up": True,
                              "min_adx": 99, "require_bb_squeeze": True})
    m2 = {**m, "macd_bull": False, "supertrend_up": False, "bb_squeeze": False}
    r = evaluate(m2, cfg)["reject_reasons"]
    assert {"macd_not_bullish", "supertrend_down", "adx_too_low", "no_bb_squeeze"} <= set(r)


# ------------------------------------------------------------------ news veto
class _Resp:
    def __init__(self, payload, ok=True, status=200, text=""):
        self._p, self.ok, self.status_code, self.text = payload, ok, status, text
    def json(self):
        return self._p
    def raise_for_status(self):
        if not self.ok:
            raise __import__("requests").HTTPError(str(self.status_code))


def _news_cfg():
    cfg = copy.deepcopy(CFG)
    cfg["news"]["sources"] = {"bse": True, "google": False}
    return cfg


def test_bse_parsing_and_llm_chain(monkeypatch):
    from scanner import news
    now = datetime.now(news.IST).replace(microsecond=0)
    bse = {"Table": [{"HEADLINE": "SEBI order imposing penalty on the company", "CATEGORYNAME": "Company Update",
                      "NEWS_DT": (now - timedelta(hours=5)).isoformat()[:19], "ATTACHMENTNAME": "a.pdf"}]}
    calls = []

    def fake_get(url, **k):
        calls.append(url)
        return _Resp(bse)

    def fake_post(url, **k):
        calls.append(url)
        if "googleapis" in url:
            assert "key=" not in url and k["headers"]["x-goog-api-key"] == "g"
            return _Resp({}, ok=False, status=429, text="quota")
        return _Resp({"choices": [{"message": {"content": json.dumps(
            {"verdict": "negative", "reason": "SEBI penalty", "key_headline": "SEBI order"})}}]})
    monkeypatch.setattr(news.requests, "get", fake_get)
    monkeypatch.setattr(news.requests, "post", fake_post)
    monkeypatch.setenv("GEMINI_API_KEY", "g")
    monkeypatch.setenv("GROQ_API_KEY", "q")
    res = news.check_stock("ACME", "Acme Ltd", "INE000A01010", _news_cfg(), {"isin:INE000A01010": "500001"})
    assert res["sources"]["bse"] == "ok (1)"
    assert res["items"][0]["source"] == "BSE filing" and 4.5 < res["items"][0]["age_hours"] < 5.5
    assert res["verdict"] == "negative" and res["method"] == "groq"      # Gemini failed -> Groq


def test_keyword_fallback_without_keys(monkeypatch):
    from scanner import news
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    items = [{"title": "Board meeting intimation", "source": "BSE filing", "age_hours": 2.0},
             {"title": "Promoter offloads stake via block deal", "source": "ET", "age_hours": 3.0}]
    v = news.classify("Acme Ltd", "ACME", items, _news_cfg())
    assert v["method"] == "keywords" and v["verdict"] == "negative"
    assert len(v["llm_errors"]) == 2
    assert news.classify("Acme", "ACME", [], _news_cfg())["verdict"] == "none"


def test_pipeline_vetoes_negative_news_and_refills(monkeypatch, tmp_path):
    syms, uni = _universe(30)
    data = _market(syms)
    monkeypatch.setattr(pipeline, "load_universe", lambda *a, **k: uni)
    monkeypatch.setattr(pipeline, "download_prices", lambda s, **k: {x: data[x] for x in s if x in data})
    monkeypatch.setattr(pipeline, "fetch_delivery", lambda d: {})
    monkeypatch.setattr(pipeline, "upcoming_events", lambda *a: [])
    monkeypatch.setattr(pipeline, "bse_code_map", lambda *a: {})
    cfg = _cfg(tmp_path)
    base = pipeline.run(copy.deepcopy(cfg), with_news=False)
    assert base["picks"]
    bad = base["picks"][0]["symbol"]

    def fake_check(symbol, company, isin, cfg, codes, max_age_hours=None):
        if symbol == bad:
            return {"verdict": "negative", "reason": "fraud probe", "key_headline": f"{symbol} fraud probe",
                    "method": "keywords", "items": [], "sources": {}, "keyword_alert": False}
        return {"verdict": "neutral", "reason": "", "key_headline": "", "method": "keywords",
                "items": [], "sources": {}, "keyword_alert": False}
    monkeypatch.setattr(pipeline, "check_stock", fake_check)
    cfg2 = _cfg(tmp_path / "second")
    res = pipeline.run(cfg2, with_news=True)
    syms_picked = [p["symbol"] for p in res["picks"]]
    assert bad not in syms_picked
    assert res["news_vetoed"][0]["symbol"] == bad
    assert any("negative_news" in r["reasons"] for r in res["rejected"] if r["symbol"] == bad)
    assert all(p["news"]["verdict"] == "neutral" for p in res["picks"])
    j = J.load(cfg2["journal"]["path"])
    assert set(j[j["vetoed"].map(J._is_true)]["symbol"]) == {bad}
    assert J.summary(j)["counts"].get("pending") == len(res["picks"])


def test_premarket_recheck(monkeypatch, tmp_path):
    import premarket
    cfg = _cfg(tmp_path)
    jdf = J.add_picks(J.load("/nonexistent.csv"),
                      [{"symbol": s, "company": f"{s} Ltd", "setup": "breakout", "score": 60, "trigger": 100.0,
                        "max_entry": 101.5, "stop": 95.0, "t1": 110.0, "risk_per_share": 5.0, "qty": 10}
                       for s in ("GOOD", "BAD")], "2026-10-23", {"regime": "strong", "size_mult": 1})
    J.save(jdf, cfg["journal"]["path"])
    monkeypatch.setattr(premarket, "bse_code_map", lambda *a: {})

    def fake_check(symbol, company, isin, cfg, codes, max_age_hours=None):
        assert max_age_hours == cfg["news"]["premarket_lookback_hours"]
        neg = symbol == "BAD"
        return {"verdict": "negative" if neg else "none", "reason": "", "method": "keywords",
                "key_headline": "BAD Ltd faces SEBI probe" if neg else "", "items": [], "sources": {},
                "keyword_alert": False}
    monkeypatch.setattr(premarket, "check_stock", fake_check)
    sent = []
    monkeypatch.setattr(premarket.notify, "_call", lambda m, d, **k: sent.append(d["text"]) or True)
    res = premarket.run(cfg)
    assert {c["symbol"]: c["verdict"] for c in res["checked"]} == {"GOOD": "none", "BAD": "negative"}
    assert "BAD: consider cancelling" in sent[0] and "GOOD" in sent[0]
    j = J.load(cfg["journal"]["path"])
    assert j.set_index("symbol").at["BAD", "premarket_news"].startswith("negative")



# ------------------------------------------------------------------ global (macro) check
def _moves(**chg):
    base = {k: 0.1 for k in ("sp500", "nasdaq", "brent", "usdinr", "nikkei", "hangseng")}
    base.update(chg)
    out = {k: {"close": 100.0, "chg1_pct": v, "chg5_pct": v, "chg1_abs": 0.0, "date": END}
           for k, v in base.items()}
    out["us10y"] = {"close": 4.2, "chg1_pct": 0.1, "chg5_pct": 0.1, "chg1_abs": 0.01, "date": END}
    return out


def test_macro_rule_levels():
    from scanner import macro as M
    assert M.rule_level(_moves(), [], CFG)[0] == "low"
    lvl, why = M.rule_level(_moves(sp500=-1.8), [], CFG)
    assert lvl == "elevated" and "S&P 500" in why[0]
    assert M.rule_level(_moves(sp500=-3.5), [], CFG)[0] == "high"            # severe
    assert M.rule_level(_moves(brent=7.0), [], CFG)[0] == "high"
    shocks = [{"title": t, "age_hours": 2} for t in ("Missile strike on port", "Oil spikes 8%",
                                                    "US slaps tariff on India", "Global selloff deepens")]
    assert M.rule_level(_moves(), shocks, CFG)[0] == "elevated"


def test_macro_assess_llm_with_rule_floor_and_apply(monkeypatch):
    from scanner import macro as M
    monkeypatch.setattr(M, "headlines", lambda cfg, h=None: ([{"title": "Fed holds rates", "age_hours": 3,
                                                               "source": "X"}], {}))
    reply = {"risk": "low", "summary": "calm", "sector_impacts": [
        {"sector": "Information Technology", "direction": "negative", "reason": "US slowdown"},
        {"sector": "Made Up Sector", "direction": "negative", "reason": "x"}]}
    monkeypatch.setattr(M, "gemini_text", lambda p, c: json.dumps(reply))
    sectors = ["Information Technology", "Oil Gas & Consumable Fuels"]
    g = M.assess(CFG, sectors, moves=_moves())
    assert g["risk"] == "low" and g["method"] == "gemini"
    assert [x["sector"] for x in g["sector_impacts"]] == ["Information Technology"]   # unknown dropped
    assert M.negative_sectors(g) == {"Information Technology": "US slowdown"}
    # numbers say high; the LLM's "low" can't override that
    g2 = M.assess(CFG, sectors, moves=_moves(sp500=-4.0))
    assert g2["risk"] == "high" and g2["llm_risk"] == "low" and g2["rule_risk"] == "high"
    reg = {"regime": "strong", "size_mult": 1.0, "allow_new_longs": True, "block_reason": ""}
    assert not M.apply(reg, g2, CFG)["allow_new_longs"]
    assert M.apply(reg, {**g, "risk": "elevated"}, CFG)["size_mult"] == CFG["macro"]["elevated_size"]
    # both LLMs fail -> rules
    monkeypatch.setattr(M, "gemini_text", lambda p, c: (_ for _ in ()).throw(RuntimeError("down")))
    monkeypatch.setattr(M, "groq_text", lambda p, c: (_ for _ in ()).throw(RuntimeError("down")))
    g3 = M.assess(CFG, sectors, moves=_moves(sp500=-1.8))
    assert g3["method"] == "rules" and g3["risk"] == "elevated"


def test_pipeline_applies_global_risk(monkeypatch, tmp_path):
    from scanner import macro as M
    syms, uni = _universe(30)
    data = _market(syms)
    monkeypatch.setattr(pipeline, "load_universe", lambda *a, **k: uni)
    monkeypatch.setattr(pipeline, "download_prices", lambda s, **k: {x: data[x] for x in s if x in data})
    monkeypatch.setattr(pipeline, "fetch_delivery", lambda d: {})
    monkeypatch.setattr(pipeline, "upcoming_events", lambda *a: [])
    cfg = _cfg(tmp_path)
    base = pipeline.run(copy.deepcopy(cfg), with_news=False)
    hit = base["picks"][0]["industry"]
    cfg2 = _cfg(tmp_path / "g")
    cfg2["macro"]["enabled"] = True
    cfg2["news"]["enabled"] = False
    gm = {"risk": "elevated", "summary": "crude spike", "method": "gemini", "moves": _moves(brent=4.0),
          "sector_impacts": [{"sector": hit, "direction": "negative", "reason": "input costs"}]}
    monkeypatch.setattr(M, "assess", lambda cfg, sectors, **k: gm)
    res = pipeline.run(cfg2, with_news=True)
    assert res["regime"]["size_mult"] == base["regime"]["size_mult"] * CFG["macro"]["elevated_size"]
    assert all(p["industry"] != hit for p in res["picks"])
    assert any("global_sector_headwind" in r["reasons"] for r in res["rejected"])
    from scanner import notify
    text = notify.format_summary(res)
    assert "Global: 🟠 <b>ELEVATED</b>" in text and f"Headwind: {hit}" in text



# ------------------------------------------------------------------ low-vol / blend
def test_score_tables_and_sleeves():
    from scanner.momentum import score_tables, sleeves_from
    feats = {}
    for i in range(8):
        rng = np.random.default_rng(i)
        c = 100 * np.cumprod(1 + 0.0004 * i + rng.normal(0, 0.004 + 0.003 * i, 320))
        feats[f"S{i}"] = feats_for(ohlc(c, seed=i))
    t = score_tables(feats, CFG, {"momentum", "low_vol", "blend"})
    last = {k: v.iloc[-1].dropna() for k, v in t.items()}
    assert last["low_vol"].idxmax() == "S0"           # calmest stock ranks first on low-vol
    assert set(last["blend"].index) == set(last["momentum"].index)
    sl = sleeves_from({**CFG["momentum"], "sleeves": [{"score": "momentum", "weight": 3},
                                                      {"score": "low_vol", "weight": 1, "regime_filter": False}]})
    assert [round(x["weight"], 2) for x in sl] == [0.75, 0.25]
    assert sl[1]["regime_filter"] is False and sl[0]["regime_filter"] is CFG["momentum"]["regime_filter"]


def test_sleeves_portfolio_runs_and_lowvol_sleeve_stays_invested():
    from scanner.momentum import run_momentum
    n = 700
    feats = {}
    for i in range(12):
        rng = np.random.default_rng(i)
        c = 100 * np.cumprod(1 + 0.0003 * (i % 4) + rng.normal(0, 0.005 + 0.002 * i, n))
        feats[f"S{i}"] = feats_for(ohlc(c, seed=i, end=END))
    falling = {"nifty50": ohlc(20000 * 0.9995 ** np.arange(n))}   # always below 200 DMA
    cfg = copy.deepcopy(CFG)
    cfg["momentum"].update({"top_n": 4, "exit_rank": 6, "sleeves": [
        {"score": "momentum", "weight": 0.5}, {"score": "low_vol", "weight": 0.5, "regime_filter": False}]})
    res = run_momentum(feats, falling, cfg)
    assert res["months_in_cash_pct"] == 0          # low-vol sleeve never goes to cash
    assert res["strategy"]["cagr_pct"] is not None


# ------------------------------------------------------------------ small caps
def test_smallcap_liquidity_is_stricter():
    from scanner.filters import liquidity_requirements, set_liquidity
    uni = pd.DataFrame({"symbol": ["BIG", "SMALL"], "indexes": ["nifty50", "niftysmallcap250"]})
    req = liquidity_requirements(uni, CFG)
    assert req["BIG"] == (CFG["gates"]["min_median_turnover_cr"], CFG["gates"]["min_avg_volume"])
    assert req["SMALL"] == (25.0, 300000.0)
    df = breakout_df()                       # ~2M shares x ~Rs 500 = ~Rs 100 Cr/day: passes both
    f = feats_for(df)
    set_liquidity({"SMALL": f}, {"SMALL": (500.0, 300000.0)})   # demand Rs 500 Cr/day
    m = metrics_at(arrays(f), len(f) - 1, CFG, 90)
    assert "low_turnover" in evaluate(m, CFG)["reject_reasons"]


# ------------------------------------------------------------------ stale data
def test_expected_session_calendar():
    from scanner.market_calendar import IST, expected_session
    at = lambda s: datetime.fromisoformat(s).replace(tzinfo=IST)
    assert expected_session(CFG, at("2026-10-27T03:00")).isoformat() == "2026-10-26"   # Tue 3 AM -> Mon
    assert expected_session(CFG, at("2026-10-24T03:00")).isoformat() == "2026-10-23"   # Sat -> Fri
    assert expected_session(CFG, at("2026-10-26T03:00")).isoformat() == "2026-10-23"   # Mon 3 AM -> Fri
    assert expected_session(CFG, at("2026-10-21T03:00")).isoformat() == "2026-10-19"   # Dussehra Tue 20
    assert expected_session(CFG, at("2026-10-26T17:00")).isoformat() == "2026-10-26"   # after close


def test_stale_data_sends_one_warning(monkeypatch, tmp_path):
    import run_scan
    cfg = _cfg(tmp_path)
    J.mark_session(cfg["journal"]["state_path"], "2026-10-23")
    fake = {"session": "2026-10-23", "duplicate": True,
            "stale": {"session": "2026-10-23", "expected": "2026-10-26"}}
    monkeypatch.setattr(run_scan, "run", lambda *a, **k: dict(fake))
    sent = []
    monkeypatch.setattr(run_scan.notify, "_call",
                        lambda m, d, **k: sent.append(d["text"]) or run_scan.notify.LAST.update({m: "ok"}) or True)
    r1 = run_scan.scan_and_notify(cfg)
    r2 = run_scan.scan_and_notify(cfg)          # backup run, data still behind: no second warning
    assert len(sent) == 1 and "price data is behind" in sent[0]
    assert r1["notify"]["sendMessage"].startswith("stale-data warning")
    assert r2["notify"]["sendMessage"].startswith("skipped: data still behind")
    # duplicate but NOT stale (already alerted this session): stays silent
    monkeypatch.setattr(run_scan, "run", lambda *a, **k: {"session": "2026-10-26", "duplicate": True, "stale": None})
    r3 = run_scan.scan_and_notify(cfg)
    assert len(sent) == 1 and r3["notify"]["sendMessage"].startswith("skipped: session already alerted")


# ------------------------------------------------------------------ quarterly results
XBRL_SAMPLE = """<?xml version="1.0" encoding="UTF-8"?>
<xbrli:xbrl xmlns:xbrli="http://www.xbrl.org/2003/instance" xmlns:in-bse-fin="http://www.bseindia.com/xbrl/fin/2020-03-31/in-bse-fin" xmlns:xbrldi="http://xbrl.org/2006/xbrldi">
<xbrli:context id="OneD"><xbrli:entity><xbrli:identifier scheme="x">ACME</xbrli:identifier></xbrli:entity>
<xbrli:period><xbrli:startDate>2026-07-01</xbrli:startDate><xbrli:endDate>2026-09-30</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="Seg1"><xbrli:entity><xbrli:identifier scheme="x">ACME</xbrli:identifier><xbrli:segment><xbrldi:explicitMember dimension="a">b</xbrldi:explicitMember></xbrli:segment></xbrli:entity>
<xbrli:period><xbrli:startDate>2026-07-01</xbrli:startDate><xbrli:endDate>2026-09-30</xbrli:endDate></xbrli:period></xbrli:context>
<xbrli:context id="HalfYear"><xbrli:entity><xbrli:identifier scheme="x">ACME</xbrli:identifier></xbrli:entity>
<xbrli:period><xbrli:startDate>2026-04-01</xbrli:startDate><xbrli:endDate>2026-09-30</xbrli:endDate></xbrli:period></xbrli:context>
<in-bse-fin:RevenueFromOperations contextRef="OneD" unitRef="INR" decimals="-5">{rev}</in-bse-fin:RevenueFromOperations>
<in-bse-fin:RevenueFromOperations contextRef="Seg1" unitRef="INR" decimals="-5">999</in-bse-fin:RevenueFromOperations>
<in-bse-fin:RevenueFromOperations contextRef="HalfYear" unitRef="INR" decimals="-5">888</in-bse-fin:RevenueFromOperations>
<in-bse-fin:ProfitLossForPeriod contextRef="OneD" unitRef="INR" decimals="-5">{np}</in-bse-fin:ProfitLossForPeriod>
<in-bse-fin:ProfitBeforeTax contextRef="OneD" unitRef="INR" decimals="-5">{pbt}</in-bse-fin:ProfitBeforeTax>
<in-bse-fin:FinanceCosts contextRef="OneD" unitRef="INR" decimals="-5">1000000000</in-bse-fin:FinanceCosts>
<in-bse-fin:DepreciationDepletionAndAmortisationExpense contextRef="OneD" unitRef="INR" decimals="-5">2000000000</in-bse-fin:DepreciationDepletionAndAmortisationExpense>
<in-bse-fin:OtherIncome contextRef="OneD" unitRef="INR" decimals="-5">500000000</in-bse-fin:OtherIncome>
</xbrli:xbrl>"""


def test_parse_xbrl_picks_quarter_totals_only():
    from datetime import date
    from scanner.results import parse_xbrl
    x = XBRL_SAMPLE.format(rev=100000000000, np=12000000000, pbt=16000000000)
    m = parse_xbrl(x, date(2026, 7, 1), date(2026, 9, 30))
    assert m["revenue"] == 1e11 and m["net_profit"] == 1.2e10   # not the segment or half-year values
    assert "RevenueFromOperations" in m["_tags"]


def test_pick_filings_and_snapshot(monkeypatch, tmp_path):
    from scanner import results as R

    def row(frm, to, cons, seq, url):
        return {"period": "Quarterly", "fromDate": frm, "toDate": to, "xbrl": url, "seqNumber": seq,
                "consolidated": "Consolidated" if cons else "Non-Consolidated",
                "filingDate": "20-Oct-2026 18:00", "audited": "Un-Audited"}
    rows = [row("01-Apr-2026", "30-Jun-2026", True, "2", "u_prev"),
            row("01-Jul-2026", "30-Sep-2026", False, "3", "u_latest_sa"),
            row("01-Jul-2026", "30-Sep-2026", True, "4", "u_latest"),
            row("01-Jul-2025", "30-Sep-2025", True, "1", "u_yago"),
            row("01-Apr-2026", "30-Sep-2026", True, "5", "u_halfyear")]          # 6-month filing ignored
    sel = R.pick_filings(rows)
    assert sel["basis"] == "consolidated"
    assert (sel["latest"]["xbrl"], sel["prev"]["xbrl"], sel["year_ago"]["xbrl"]) == ("u_latest", "u_prev", "u_yago")
    docs = {"u_latest": XBRL_SAMPLE.format(rev=110e9, np=13.2e9, pbt=17e9),
            "u_yago": XBRL_SAMPLE.format(rev=100e9, np=12e9, pbt=16e9).replace("2026-07-01", "2025-07-01").replace("2026-09-30", "2025-09-30").replace("2026-04-01", "2025-04-01"),
            "u_prev": XBRL_SAMPLE.format(rev=105e9, np=11e9, pbt=15e9).replace("2026-07-01", "2026-04-01").replace("2026-09-30", "2026-06-30")}

    class Fake:
        calls = 0
        def json(self, path):
            return {"data": [], "totalCount": 0} if "integrated" in path else rows
        def get(self, url):
            Fake.calls += 1
            class Resp:
                content = docs[url].encode()
            return Resp()
    s = R.snapshot(Fake(), "ACME", str(tmp_path))
    assert s["quarter"] == "Q2 FY27" and s["basis"] == "consolidated"
    assert s["revenue_cr"] == 11000.0 and s["revenue_yoy_pct"] == 10.0 and s["profit_yoy_pct"] == 10.0
    assert s["revenue_qoq_pct"] == round((110 / 105 - 1) * 100, 1)
    assert s["op_margin_pct"] == round((17e9 + 1e9 + 2e9 - 0.5e9) / 110e9 * 100, 1)
    n = Fake.calls
    R.snapshot(Fake(), "ACME", str(tmp_path))         # same filing -> cached, no XBRL downloads
    assert Fake.calls == n
    assert "Q2 FY27 · Rev +10% YoY · PAT +10% YoY" in R.summary_line(s)



def test_normalize_integrated_rows():
    from scanner.results import normalize_integrated, pick_filings
    rows = [{"qe_Date": "30-SEP-2026", "consolidated": "Consolidated", "xbrl": "https://x/a.xml",
             "seq_Id": "9", "broadcast_Date": "08-Oct-2026 16:00:00"},
            {"qe_Date": "30-SEP-2026", "consolidated": "Standalone", "xbrl": "https://x/b.xml", "seq_Id": "8"},
            {"qe_Date": "30-JUN-2026", "consolidated": "Consolidated", "xbrl": "https://nsearchives/corporate/null"},
            {"qe_Date": "30-SEP-2025", "consolidated": "Consolidated", "xbrl": "https://x/c.xml", "seq_Id": "2"}]
    n = normalize_integrated(rows)
    assert [r["xbrl"] for r in n] == ["https://x/a.xml", "https://x/b.xml", "https://x/c.xml"]
    assert n[0]["fromDate"] == "01-Jul-2026" and n[0]["toDate"] == "30-Sep-2026"
    sel = pick_filings(n)
    assert sel["latest"]["xbrl"] == "https://x/a.xml" and sel["year_ago"]["xbrl"] == "https://x/c.xml"
    assert sel["prev"] is None
