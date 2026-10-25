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


def format_summary(result: dict, max_picks: int = 15, report_url: str | None = None) -> str:
    e = html.escape
    reg = result["regime"]
    icon = {"strong": "🟢", "neutral": "🟡", "weak": "🔴"}.get(reg["regime"], "⚪")
    lines = [f"📊 <b>Scan {e(result['session'])}</b>",
             f"{icon} Market: <b>{reg['regime'].upper()}</b> · breadth {reg['breadth_pct']}%"]
    for name, b in reg.get("benchmarks", {}).items():
        lines.append(f"   {e(name)}: {'↑' if b['above_20'] else '↓'}20DMA "
                     f"{'↑' if b['above_50'] else '↓'}50DMA")
    if result.get("note"):
        lines.append(f"\n⚠️ {e(result['note'])}")

    picks = result["picks"]
    lines.append(f"\n<b>{len(picks)} picks</b> from {result.get('evaluated', '–')} stocks")
    for i, p in enumerate(picks[:max_picks], 1):
        n = p.get("news", {})
        tags = []
        if n.get("fresh"):
            tags.append("📰 fresh news")
        elif n.get("has_news"):
            tags.append("📰 news")
        if "event_soon" in p["flags"]:
            tags.append("⚠️ event soon")
        other = [f for f in p["flags"] if f not in ("event_soon", "no_overhead_resistance")]
        if other:
            tags.append("🚩 " + ", ".join(other))
        lines.append(
            f"\n{i}. <b>{e(p['symbol'])}</b> · {p['setup']} · score {p['score']}\n"
            f"   ₹{p['close']:.2f} | SL {p['stop']:.2f} | T {p['target']:.2f} | "
            f"R:R {p['reward_risk']:.1f}"
            + (f"\n   {' · '.join(tags)}" if tags else ""))
        if n.get("items"):
            top = n["items"][0]
            lines.append(f"   ↳ <i>{e(top['title'][:110])}</i>")
    if len(picks) > max_picks:
        lines.append(f"\n…and {len(picks) - max_picks} more in the full report")
    if not picks:
        lines.append("No stocks passed today.")
    if report_url:
        lines.append(f'\n<a href="{e(report_url)}">Full report</a>')

    text = "\n".join(lines)
    return text if len(text) <= MAX_LEN else text[:MAX_LEN - 20] + "\n…(truncated)"


def send_scan(result: dict, cfg: dict, report_html: Path | None = None) -> None:
    tcfg = cfg.get("notify", {}).get("telegram", {})
    if not tcfg.get("enabled", True):
        return
    text = format_summary(result, tcfg.get("max_picks", 15), os.getenv("REPORT_URL"))
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
