"""Telegram notifications.

Needs two environment variables (GitHub repo secrets in Actions):
  TELEGRAM_BOT_TOKEN  - from @BotFather
  TELEGRAM_CHAT_ID    - your chat id (see README)
If either is missing, notifications are skipped silently.
"""
import html
import logging
import os
from pathlib import Path

import requests

log = logging.getLogger(__name__)
API = "https://api.telegram.org/bot{token}/{method}"
MAX_LEN = 4000  # Telegram limit is 4096 characters


def _creds() -> tuple[str, str] | None:
    token = os.getenv("TELEGRAM_BOT_TOKEN", "").strip()
    chat = os.getenv("TELEGRAM_CHAT_ID", "").strip()
    return (token, chat) if token and chat else None


def _call(method: str, data: dict, files: dict | None = None) -> bool:
    creds = _creds()
    if not creds:
        log.info("Telegram not configured; skipping %s", method)
        return False
    token, chat = creds
    try:
        r = requests.post(API.format(token=token, method=method),
                          data={"chat_id": chat, **data}, files=files, timeout=30)
        if not r.ok:
            log.warning("Telegram %s failed: %s %s", method, r.status_code, r.text[:200])
        return r.ok
    except requests.RequestException as e:
        log.warning("Telegram %s error: %s", method, e)
        return False


def format_summary(result: dict, max_picks: int = 5, report_url: str | None = None) -> str:
    e = html.escape
    reg = result["regime"]
    icon = {"strong": "🟢", "neutral": "🟡", "weak": "🔴"}.get(reg["regime"], "⚪")
    vix = f" · VIX {reg['vix']:.1f}" if reg.get("vix") is not None else ""
    lines = [f"📊 <b>Scan {e(result['session'])}</b>",
             f"{icon} Market: <b>{reg['regime'].upper()}</b> · breadth {reg['breadth_pct']}%{vix}"
             f" · size ×{reg.get('size_mult', 1)}"]
    for name, b in reg.get("benchmarks", {}).items():
        lines.append(f"   {e(name)}: {'↑' if b['above_20'] else '↓'}20DMA "
                     f"{'↑' if b['above_50'] else '↓'}50DMA")
    if result.get("note"):
        lines.append(f"\n⚠️ {e(result['note'])}")

    picks = result["picks"]
    lines.append(f"\n<b>{len(picks)} picks</b> ({result.get('passed_gates', '–')} passed gates, "
                 f"{result.get('evaluated', '–')} scanned)")
    for i, p in enumerate(picks[:max_picks], 1):
        flags = [f for f in p.get("flags", []) if f != "blue_sky"]
        lines.append(
            f"\n{i}. <b>{e(p['symbol'])}</b> · {p['setup']} · score {p['score']} · RS {p.get('rs_rank', 0):.0f}\n"
            f"   Buy &gt; {p['trigger']:.2f} (skip &gt; {p['max_entry']:.2f})\n"
            f"   SL {p['stop']:.2f} | T1 {p['t1']:.2f} | Qty {p.get('qty', '–')}"
            + (f"\n   🚩 {e(', '.join(flags))}" if flags else ""))
        items = p.get("news", {}).get("items") or []
        if items:
            lines.append(f"   ↳ <i>{e(items[0]['title'][:110])}</i>")
    if len(picks) > max_picks:
        lines.append(f"\n…and {len(picks) - max_picks} more in the full report")
    if not picks:
        watch = result.get("watchlist") or []
        lines.append("No trades today." + (
            " Watchlist: " + ", ".join(e(w["symbol"]) for w in watch) if watch else ""))
    o = (result.get("journal") or {}).get("overall", {})
    if o.get("trades"):
        lines.append(f"\n📒 Journal: {o['trades']} closed · win {o['win_rate_pct']}% · "
                     f"avg win {o['avg_win_r']}R / loss {o['avg_loss_r']}R · "
                     f"expectancy {o['expectancy_r']}R")
    if report_url:
        lines.append(f'\n<a href="{e(report_url)}">Full report</a>')

    text = "\n".join(lines)
    return text if len(text) <= MAX_LEN else text[:MAX_LEN - 20] + "\n…(truncated)"


def send_scan(result: dict, cfg: dict, report_html: Path | None = None) -> None:
    tcfg = cfg.get("notify", {}).get("telegram", {})
    if not tcfg.get("enabled", True):
        return
    text = format_summary(result, tcfg.get("max_picks", 5), os.getenv("REPORT_URL"))
    _call("sendMessage", {"text": text, "parse_mode": "HTML",
                          "disable_web_page_preview": "true"})
    if tcfg.get("send_report_file", True) and report_html and report_html.exists():
        with open(report_html, "rb") as fh:
            _call("sendDocument", {"caption": "Full report (open in browser)"},
                  files={"document": (report_html.name, fh, "text/html")})


def send_failure(error: str) -> None:
    run_url = os.getenv("RUN_URL", "")
    text = f"❌ <b>Stock scan failed</b>\n<code>{html.escape(error[:1500])}</code>"
    if run_url:
        text += f'\n<a href="{html.escape(run_url)}">View logs</a>'
    _call("sendMessage", {"text": text, "parse_mode": "HTML"})
