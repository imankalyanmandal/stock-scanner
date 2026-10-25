"""Writes JSON (full detail), CSV (picks) and a readable HTML report."""
import html
import json
from collections import Counter
from pathlib import Path

import pandas as pd

CSV_COLS = ["symbol", "company", "industry", "setup", "score", "close", "entry", "stop",
            "target", "reward_risk", "gain_2d_pct", "vol_ratio", "close_location",
            "delivery_pct", "rsi", "rs5_idx", "rs20_idx", "rs5_sec", "flags", "events",
            "has_news", "fresh_news"]


def _fmt(v, nd=2):
    if v is None:
        return "–"
    if isinstance(v, float):
        return f"{v:.{nd}f}"
    return str(v)


def write(result: dict, out_dir: str) -> dict[str, Path]:
    d = Path(out_dir)
    d.mkdir(parents=True, exist_ok=True)
    stem = f"scan_{result['session']}"
    paths = {"json": d / f"{stem}.json", "csv": d / f"{stem}.csv", "html": d / f"{stem}.html"}

    paths["json"].write_text(json.dumps(result, indent=2, default=str))

    rows = []
    for p in result["picks"]:
        n = p.get("news", {})
        rows.append({**{k: p.get(k) for k in CSV_COLS},
                     "flags": ";".join(p.get("flags", [])),
                     "events": ";".join(p.get("events", [])),
                     "has_news": n.get("has_news"), "fresh_news": n.get("fresh")})
    pd.DataFrame(rows, columns=CSV_COLS).to_csv(paths["csv"], index=False)

    paths["html"].write_text(_html(result))
    return paths


def _html(r: dict) -> str:
    reg = r["regime"]
    bench = "".join(
        f"<li><b>{html.escape(k)}</b>: {_fmt(v['close'])} — "
        f"{'above' if v['above_20'] else 'below'} 20 DMA, "
        f"{'above' if v['above_50'] else 'below'} 50 DMA</li>"
        for k, v in reg["benchmarks"].items())
    rej = Counter(reason for x in r.get("rejected", []) for reason in x["reasons"])
    rej_html = "".join(f"<li>{html.escape(k)}: {v}</li>" for k, v in rej.most_common(12))

    cards = []
    for p in r["picks"]:
        n = p.get("news", {})
        news = "".join(
            f"<li><a href='{html.escape(i['link'])}'>{html.escape(i['title'])}</a> "
            f"<span class=m>{html.escape(i['source'])} · {_fmt(i['age_hours'], 0)}h ago</span></li>"
            for i in n.get("items", [])) or "<li class=m>No news found</li>"
        flags = " ".join(f"<span class=flag>{html.escape(f)}</span>" for f in p["flags"])
        comps = " · ".join(f"{k} {v}" for k, v in p["components"].items())
        cards.append(f"""
<div class=card>
  <div class=hd><b>{html.escape(p['symbol'])}</b> <span class=m>{html.escape(p['company'])} ·
  {html.escape(p['industry'])}</span><span class=score>{p['score']}</span></div>
  <div class=row><span class=tag>{p['setup']}</span> {flags}</div>
  <table><tr><th>Close</th><th>Entry</th><th>Stop</th><th>Target</th><th>R:R</th>
  <th>2d %</th><th>Vol×</th><th>Deliv %</th><th>RSI</th><th>RS5 idx</th><th>RS20 idx</th></tr>
  <tr><td>{_fmt(p['close'])}</td><td>{_fmt(p['entry'])}</td><td>{_fmt(p['stop'])}</td>
  <td>{_fmt(p['target'])}</td><td>{_fmt(p['reward_risk'])}</td><td>{_fmt(p['gain_2d_pct'])}</td>
  <td>{_fmt(p['vol_ratio'])}</td><td>{_fmt(p['delivery_pct'], 1)}</td><td>{_fmt(p['rsi'], 1)}</td>
  <td>{_fmt(p['rs5_idx'] * 100, 1)}%</td><td>{_fmt(p['rs20_idx'] * 100, 1)}%</td></tr></table>
  <div class=m>Score parts: {comps}</div>
  <div class=m>Events: {', '.join(p.get('events', [])) or 'none in window'}</div>
  <ul class=news>{news}</ul>
</div>""")

    note = f"<p class=warn>{html.escape(r['note'])}</p>" if r.get("note") else ""
    return f"""<!doctype html><html><head><meta charset=utf-8>
<meta name=viewport content="width=device-width,initial-scale=1">
<title>Scan {r['session']}</title><style>
:root{{--bg:#fafaf8;--fg:#1d1d1b;--m:#6b6b66;--card:#fff;--line:#e4e2dc;--acc:#1f6f50;--warn:#a3471c}}
@media (prefers-color-scheme:dark){{:root{{--bg:#151514;--fg:#ecebe6;--m:#9a9a93;--card:#1f1f1d;--line:#33332f;--acc:#5fc29a;--warn:#e8925f}}}}
body{{background:var(--bg);color:var(--fg);font:14px/1.5 system-ui,sans-serif;margin:0;padding:16px;max-width:1000px;margin:auto}}
.card{{background:var(--card);border:1px solid var(--line);border-radius:10px;padding:14px;margin:12px 0;overflow-x:auto}}
.hd{{display:flex;gap:8px;align-items:baseline}} .score{{margin-left:auto;font-weight:700;color:var(--acc);font-size:18px}}
.m{{color:var(--m);font-size:12px}} .tag{{background:var(--acc);color:var(--bg);border-radius:4px;padding:1px 6px;font-size:12px}}
.flag{{border:1px solid var(--warn);color:var(--warn);border-radius:4px;padding:0 5px;font-size:11px}}
table{{border-collapse:collapse;margin:8px 0;font-size:12px}} th,td{{padding:3px 8px;border-bottom:1px solid var(--line);text-align:right}}
.warn{{color:var(--warn);font-weight:600}} a{{color:var(--acc)}} .news{{margin:6px 0 0;padding-left:18px}}
</style></head><body>
<h1>Stock scan — session {r['session']}</h1>
{note}
<p><b>Market regime:</b> {reg['regime'].upper()} · breadth {reg['breadth_pct']}% of universe above 20 DMA
· min score {r.get('min_score', '–')} · {len(r['picks'])} picks from {r.get('evaluated', '–')} evaluated</p>
<ul>{bench}</ul>
<details><summary>Why stocks were rejected</summary><ul>{rej_html}</ul></details>
{''.join(cards) or '<p>No stocks passed today.</p>'}
<p class=m>Screening output only, not investment advice. Entry/stop/target are mechanical estimates.</p>
</body></html>"""
