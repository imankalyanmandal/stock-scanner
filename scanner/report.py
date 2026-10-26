"""Writes JSON (full detail), CSV (picks) and a readable HTML report."""
import html
import json
import math
from collections import Counter
from pathlib import Path

import pandas as pd

CSV_COLS = ["symbol", "company", "industry", "setup", "score", "close", "trigger", "max_entry",
            "stop", "t1", "risk_per_share", "stop_atr", "room_r", "qty", "position_value",
            "risk_amount", "rs_rank", "setup_vol_ratio", "setup_close_loc", "below_high_pct",
            "rsi", "delivery_pct", "flags", "events", "exit_plan"]


def _fmt(v, nd=2):
    if v is None:
        return "–"
    if isinstance(v, float):
        if math.isnan(v):
            return "–"
        if math.isinf(v):
            return "∞"
        return f"{v:,.{nd}f}"
    return html.escape(str(v))


def _events(p):
    return ", ".join(f"{e['type']} {e['date']}" for e in p.get("events", []))


def write(result: dict, out_dir: str) -> dict[str, Path]:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    stem = f"scan_{result['session']}"
    paths = {"json": d / f"{stem}.json", "csv": d / f"{stem}.csv", "html": d / f"{stem}.html"}
    paths["json"].write_text(json.dumps(result, indent=2, default=str))
    rows = [{**{k: p.get(k) for k in CSV_COLS}, "flags": ";".join(p.get("flags", [])),
             "events": _events(p)} for p in result["picks"]]
    pd.DataFrame(rows, columns=CSV_COLS).to_csv(paths["csv"], index=False)
    paths["html"].write_text(_html(result))
    return paths


def _indicator_line(p):
    if "macd_bull" not in p:
        return ""
    yes = lambda x: "yes" if x else "no"
    return (f"MACD bullish {yes(p['macd_bull'])} · Supertrend {'up' if p['supertrend_up'] else 'down'} · "
            f"ADX {_fmt(p.get('adx'), 0)} · BB squeeze before move {yes(p['bb_squeeze'])} · "
            f"RSI {_fmt(p.get('rsi'), 0)} · 20/50/200 DMA {_fmt(p['sma20'])} / {_fmt(p['sma50'])} / "
            f"{_fmt(p['sma200'])}")


def _setup_line(p):
    if p["setup"] == "breakout":
        return (f"Breakout day {p['bo_day']} over {_fmt(p['base_high'])} · base depth "
                f"{_fmt(p['base_depth_pct'], 1)}% · contraction {_fmt(p['contraction'])} · "
                f"breakout volume {_fmt(p['setup_vol_ratio'])}×")
    if p["setup"] == "pullback":
        return (f"Pullback {p['pb_bars']} bars from {_fmt(p['pb_high'])}, depth "
                f"{_fmt(p['pb_depth_atr'], 1)} ATR · 20 DMA touch {_fmt(p['touch_offset_atr'])} ATR · "
                f"pullback volume {_fmt(p['setup_vol_ratio'])}×")
    return ""


def _card(p, watch=False):
    flags = " ".join(f"<span class=flag>{html.escape(f)}</span>" for f in p["flags"])
    comps = " · ".join(f"{k} {v}" for k, v in p["components"].items())
    news = "".join(f"<li><a href='{html.escape(i['link'])}'>{html.escape(i['title'])}</a></li>"
                   for i in p.get("news", {}).get("items", []))
    size = "" if watch else (
        f"<td>{p.get('qty', 0)}</td><td>{_fmt(p.get('position_value'), 0)}</td>"
        f"<td>{_fmt(p.get('risk_amount'), 0)}</td>")
    size_h = "" if watch else "<th>Qty</th><th>Value ₹</th><th>Risk ₹</th>"
    return f"""
<div class=card>
  <div class=hd><b>{html.escape(p['symbol'])}</b> <span class=m>{html.escape(str(p['company']))} ·
  {html.escape(str(p['industry']))}</span><span class=score>{p['score']}</span></div>
  <div class=row><span class=tag>{p['setup']}</span> {flags}</div>
  <table><tr><th>Close</th><th>Buy-stop</th><th>Skip above</th><th>Stop</th><th>T1</th>
  <th>Risk/sh</th><th>Stop ATR</th><th>Room R</th>{size_h}<th>RS rank</th></tr>
  <tr><td>{_fmt(p['close'])}</td><td><b>{_fmt(p['trigger'])}</b></td><td>{_fmt(p['max_entry'])}</td>
  <td>{_fmt(p['stop'])}</td><td>{_fmt(p['t1'])}</td><td>{_fmt(p['risk_per_share'])}</td>
  <td>{_fmt(p['stop_atr'], 1)}</td><td>{_fmt(p['room_r'], 1)}</td>{size}<td>{_fmt(p['rs_rank'], 0)}</td></tr></table>
  <div class=m>{_setup_line(p)}</div>
  <div class=m>{_indicator_line(p)}</div>
  {'' if watch else f"<div class=plan>{html.escape(p.get('exit_plan', ''))}</div>"}
  <div class=m>Score parts: {comps}</div>
  <div class=m>Events: {html.escape(_events(p)) or 'none in window'}</div>
  {f'<ul class=news>{news}</ul>' if news else ''}
</div>"""


def _journal(j):
    if not j:
        return ""
    o = j.get("overall", {})
    if not o.get("trades"):
        counts = ", ".join(f"{k} {v}" for k, v in j.get("counts", {}).items()) or "no trades yet"
        return f"<h2>Live journal</h2><p class=m>No closed trades yet ({html.escape(counts)}).</p>"
    rows = "".join(
        f"<tr><td>{html.escape(k)}</td><td>{v['trades']}</td><td>{v['win_rate_pct']}%</td>"
        f"<td>{v['avg_win_r']}</td><td>{v['avg_loss_r']}</td><td>{v['expectancy_r']}</td>"
        f"<td>{v['total_r']}</td></tr>"
        for k, v in {"all": o, **j.get("by_setup", {})}.items() if v.get("trades"))
    opens = "".join(f"<li>{html.escape(t['symbol'])} (from {t['session']}): "
                    f"{_fmt(t.get('r_multiple'))}R open</li>" for t in j.get("open", []))
    return (f"<h2>Live journal</h2><table><tr><th></th><th>Trades</th><th>Win</th><th>Avg win R</th>"
            f"<th>Avg loss R</th><th>Expectancy R</th><th>Total R</th></tr>{rows}</table>"
            f"{f'<ul>{opens}</ul>' if opens else ''}")


def _html(r: dict) -> str:
    reg = r["regime"]
    bench = "".join(
        f"<li><b>{html.escape(k)}</b>: {_fmt(v['close'])} · "
        f"{'above' if v['above_20'] else 'below'} 20 DMA · "
        f"{'above' if v['above_50'] else 'below'} 50 DMA</li>"
        for k, v in reg["benchmarks"].items())
    rej = Counter(reason.split(":")[0] if reason.startswith("excluded") else reason
                  for x in r.get("rejected", []) for reason in x["reasons"])
    rej_html = "".join(f"<li>{html.escape(k)}: {v}</li>" for k, v in rej.most_common(20))
    cards = "".join(_card(p) for p in r["picks"])
    watch = "".join(_card(p, watch=True) for p in r.get("watchlist", []))
    note = f"<p class=warn>{html.escape(r['note'])}</p>" if r.get("note") else ""
    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Scan {r['session']}</title><style>
:root{{--bg:#fafaf8;--fg:#1d1d1b;--m:#6b6b66;--card:#fff;--line:#e4e2dc;--acc:#1f6f50;--warn:#a3471c}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151514;--fg:#ecebe6;--m:#9a9a93;--card:#1f1f1d;--line:#33332f;--acc:#5fc29a;--warn:#e8925f}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,sans-serif;padding:16px;max-width:1000px;margin:auto}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin:12px 0;overflow-x:auto}}
.hd{{display:flex;gap:8px;align-items:baseline}} .score{{margin-left:auto;font-weight:700;color:var(--acc);font-size:18px}}
.m{{color:var(--m);font-size:12px}} .tag{{background:var(--acc);color:var(--bg);border-radius:4px;padding:1px 6px;font-size:12px}}
.flag{{border:1px solid var(--warn);color:var(--warn);border-radius:4px;padding:0 5px;font-size:11px}}
.plan{{margin:6px 0;padding:6px 8px;border-left:3px solid var(--acc);font-size:13px}}
table{{border-collapse:collapse;margin:8px 0;font-size:12px}} th,td{{padding:3px 8px;border-bottom:1px solid var(--line);text-align:right}}
.warn{{color:var(--warn);font-weight:600}} a{{color:var(--acc)}} .news{{margin:6px 0 0;padding-left:18px}}
</style></head><body>
<h1>Stock scan · session {r['session']}</h1>
{note}
<p><b>Market:</b> {reg['regime'].upper()} · breadth {reg['breadth_pct']}% above 50 DMA ·
India VIX {_fmt(reg.get('vix'), 1)} · position size ×{reg['size_mult']}
· {len(r['picks'])} picks · {r.get('passed_gates', '–')} passed all gates of {r.get('evaluated', '–')} evaluated</p>
<ul>{bench}</ul>
{cards or '<p>No trades today.</p>'}
{f'<h2>Watchlist (not tradeable today)</h2>{watch}' if watch else ''}
{_journal(r.get('journal'))}
<details><summary>Why stocks were rejected</summary><ul>{rej_html}</ul></details>
<p class=m>Screening output only, not investment advice. Levels are mechanical estimates.</p>
</body></html>"""
