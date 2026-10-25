"""Offline tests on synthetic price data (no network)."""
import numpy as np
import pandas as pd
import pytest
import yaml

from scanner import pipeline
from scanner.filters import compute_metrics, evaluate

CFG = yaml.safe_load(open("config.yaml"))


def make_df(n=260, drift=0.0015, last_moves=(0.012, 0.015), vol=2_000_000, price=500, seed=1):
    rng = np.random.default_rng(seed)
    rets = rng.normal(drift, 0.012, n)
    rets[-len(last_moves):] = last_moves
    close = price * np.cumprod(1 + rets)
    idx = pd.bdate_range(end="2026-10-23", periods=n)
    open_ = close / (1 + rets * 0.4)
    high = np.maximum(open_, close) * 1.004
    low = np.minimum(open_, close) * 0.992
    high[-1] = close[-1] * 1.002  # close near the high
    v = np.full(n, vol, dtype=float)
    v[-1] = vol * 2
    return pd.DataFrame({"Open": open_, "High": high, "Low": low, "Close": close, "Volume": v},
                        index=idx)


def bench(n=260):
    return make_df(n, drift=0.0005, last_moves=(0.002,), seed=99)


def test_metrics_green_streak_and_setup():
    m = compute_metrics(make_df(), bench()["Close"])
    assert m["green_prev_streak"] >= 2
    assert m["setup"] in {"breakout", "pullback", "none"}
    assert m["stop"] < m["entry"] < m["target"]
    assert 0 <= m["close_location"] <= 1


def test_red_day_rejected():
    m = compute_metrics(make_df(last_moves=(0.01, -0.01)), bench()["Close"])
    ev = evaluate(m, CFG, {}, None)
    assert "not_2_green_days" in ev["reject_reasons"]


def test_penny_and_illiquid_rejected():
    m = compute_metrics(make_df(price=20, vol=1000), bench()["Close"])
    ev = evaluate(m, CFG, {}, None)
    assert "price<50" in ev["reject_reasons"]
    assert "low_turnover" in ev["reject_reasons"]


def test_extended_move_rejected():
    m = compute_metrics(make_df(last_moves=(0.06, 0.06)), bench()["Close"])
    ev = evaluate(m, CFG, {}, None)
    assert "extended_2day_gain" in ev["reject_reasons"]


def test_score_bounds():
    m = compute_metrics(make_df(), bench()["Close"])
    ev = evaluate(m, CFG, {"breadth_pct": 70, "ret5_med": 0, "ret20_med": 0}, 55.0)
    assert 0 <= ev["score"] <= 100


def test_pipeline_end_to_end(monkeypatch, tmp_path):
    syms = [f"S{i}" for i in range(30)]
    uni = pd.DataFrame({"symbol": syms, "company": [f"Co {s}" for s in syms],
                        "industry": ["IT" if i % 2 else "Banks" for i in range(30)],
                        "indexes": "nifty50"})
    data = {s: make_df(seed=i, last_moves=(0.012, 0.015) if i % 3 else (0.01, -0.01))
            for i, s in enumerate(syms)}
    data["^NSEI"] = bench()
    data["^CRSLDX"] = bench()
    monkeypatch.setattr(pipeline, "load_universe", lambda *a, **k: uni)
    monkeypatch.setattr(pipeline, "download_prices", lambda s: {k: data[k] for k in s})
    monkeypatch.setattr(pipeline, "fetch_delivery", lambda d: {s: 50.0 for s in syms})
    monkeypatch.setattr(pipeline, "upcoming_events", lambda *a: [])
    cfg = {**CFG, "output": {**CFG["output"], "dir": str(tmp_path)}}
    res = pipeline.run(cfg, with_news=False)
    assert res["session"] == "2026-10-23"
    assert res["regime"]["regime"] in {"strong", "neutral", "weak"}
    assert len(res["picks"]) + len(res["rejected"]) == 30
    for p in res["picks"]:
        assert not p["reject_reasons"]
    assert (tmp_path / "scan_2026-10-23.html").exists()
    assert (tmp_path / "scan_2026-10-23.csv").exists()


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
    assert n["items"][0]["source"] == "Example News"


def test_telegram_message_and_send(monkeypatch, tmp_path):
    from scanner import notify
    pick = {"symbol": "ACME", "setup": "breakout", "score": 78.5, "close": 512.3,
            "stop": 498.0, "target": 545.0, "reward_risk": 2.3,
            "flags": ["event_soon", "abnormal_gap"],
            "news": {"has_news": True, "fresh": True,
                     "items": [{"title": "Acme <wins> order & more"}]}}
    result = {"session": "2026-10-23", "evaluated": 248,
              "regime": {"regime": "neutral", "breadth_pct": 52.0,
                         "benchmarks": {"nifty50": {"above_20": True, "above_50": False}}},
              "picks": [pick] * 20}
    text = notify.format_summary(result, max_picks=15)
    assert "ACME" in text and "&lt;wins&gt;" in text and "5 more" in text
    assert len(text) <= notify.MAX_LEN

    calls = []

    class R:
        ok = True
    monkeypatch.setenv("TELEGRAM_BOT_TOKEN", "t")
    monkeypatch.setenv("TELEGRAM_CHAT_ID", "1")
    monkeypatch.setattr(notify.requests, "post",
                        lambda url, **k: calls.append(url.rsplit("/", 1)[1]) or R())
    report = tmp_path / "scan.html"
    report.write_text("<html></html>")
    notify.send_scan(result, {"notify": {"telegram": {}}}, report)
    assert calls == ["sendMessage", "sendDocument"]


def test_telegram_skipped_without_creds(monkeypatch):
    from scanner import notify
    monkeypatch.delenv("TELEGRAM_BOT_TOKEN", raising=False)
    monkeypatch.setattr(notify.requests, "post", lambda *a, **k: pytest.fail("should not post"))
    assert notify._call("sendMessage", {"text": "x"}) is False
