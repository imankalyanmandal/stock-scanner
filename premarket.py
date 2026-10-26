"""Pre-market news recheck for the latest picks (runs ~8:20-8:50 AM IST, Mon-Fri).

Looks only at news published in the last `news.premarket_lookback_hours` hours (i.e. after the
3 AM scan), classifies it like the scan does, and sends one Telegram message:
all clear, or which picks to cancel and why. Usage: python premarket.py [--config config.yaml]
"""
import argparse
import html
import json
import logging
from datetime import datetime
from pathlib import Path

import yaml

from scanner import journal as J
from scanner import macro as M
from scanner import notify
from scanner.news import bse_code_map, check_stock

log = logging.getLogger("premarket")


def run(cfg: dict, send: bool = True) -> dict:
    jdf = J.load(cfg["journal"]["path"])
    if jdf.empty:
        return {"checked": [], "note": "journal empty"}
    live = jdf[~jdf["vetoed"].map(J._is_true) & (jdf["status"] == "pending")]
    if live.empty:
        return {"checked": [], "note": "no pending picks"}
    session = max(live["session"])
    rows = live[live["session"] == session]
    isins = {}
    try:   # ISINs from the cached constituent files (no download needed)
        from scanner.universe import load_universe
        u = load_universe(cfg["universe"]["indexes"], cfg["output"]["data_dir"])
        isins = dict(zip(u["symbol"], u.get("isin", [None] * len(u))))
    except Exception as e:
        log.warning("universe unavailable: %s", e)
    codes = bse_code_map(cfg["output"]["data_dir"])
    hours = cfg["news"].get("premarket_lookback_hours", 8)
    checked = []
    for ix, r in rows.iterrows():
        company = r["company"] if isinstance(r["company"], str) and r["company"] else r["symbol"]
        res = check_stock(r["symbol"], company, isins.get(r["symbol"]), cfg, codes, max_age_hours=hours)
        checked.append({"symbol": r["symbol"], "verdict": res["verdict"], "method": res["method"],
                        "headline": res.get("key_headline") or "", "reason": res.get("reason", ""),
                        "keyword_alert": res["keyword_alert"], "items": len(res["items"])})
        jdf.at[ix, "premarket_news"] = f"{res['verdict']}: {(res.get('key_headline') or '')[:150]}"
    J.save(jdf, cfg["journal"]["path"])

    gm = None
    if cfg.get("macro", {}).get("enabled"):
        try:
            sectors = sorted(set(u["industry"].astype(str))) if isins else sorted(
                set(rows["industry"].dropna().astype(str)))
            gm = M.assess(cfg, sectors, max_age_hours=hours)
        except Exception as ex:
            log.warning("global check failed: %s", ex)
    neg = M.negative_sectors(gm)

    e = html.escape
    bad = [c for c in checked if c["verdict"] == "negative" or c["keyword_alert"]]
    lines = [f"🌅 <b>Pre-market news check</b> · picks from session {e(session)}",
             f"News in the last {hours}h, before the 9:15 open."]
    lines += notify.macro_lines(gm)
    if gm and gm.get("risk") == "high":
        lines.append("🔴 <b>Global risk is HIGH: consider not placing today's buy-stops.</b>")
        bad.append({"symbol": "*global*"})
    ind = dict(zip(rows["symbol"], rows["industry"]))
    for c in checked:
        if ind.get(c["symbol"]) in neg:
            lines.append(f"🟠 <b>{e(c['symbol'])}</b>: sector headwind ({e(ind[c['symbol']])}): "
                         f"{e(neg[ind[c['symbol']]])}")
            bad.append(c)
    lines.append("")
    for c in checked:
        if c["verdict"] == "negative":
            lines.append(f"🔴 <b>{e(c['symbol'])}: consider cancelling the buy-stop</b>\n"
                         f"   <i>{e(c['headline'][:140] or c['reason'])}</i>")
        elif c["keyword_alert"]:
            lines.append(f"🟠 <b>{e(c['symbol'])}</b>: risk keywords in fresh news, check before entry")
        elif c["verdict"] == "positive":
            lines.append(f"🟢 {e(c['symbol'])}: positive news, <i>{e(c['headline'][:110])}</i>")
        else:
            lines.append(f"✅ {e(c['symbol'])}: {'no new news' if c['verdict'] == 'none' else 'nothing material'}")
    if not bad:
        lines.append("\nAll clear. Orders as planned.")
    status = {}
    if send:
        notify.LAST.clear()
        notify._call("sendMessage", {"text": "\n".join(lines), "parse_mode": "HTML",
                                     "disable_web_page_preview": "true"})
        status = dict(notify.LAST)
    out = {"at": datetime.now().isoformat(timespec="seconds"), "session": session,
           "checked": checked, "global": gm, "telegram": status}
    p = Path(cfg["output"]["dir"]) / "premarket_last.json"
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(json.dumps(out, indent=2, ensure_ascii=False) + "\n")
    return out


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--no-notify", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    res = run(yaml.safe_load(open(args.config)), send=not args.no_notify)
    print(json.dumps(res, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
