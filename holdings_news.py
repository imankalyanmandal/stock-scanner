"""News digest for the stocks/ETFs you hold (data/holdings.csv), tagged positive / negative.

Runs from the Holdings news workflow (morning and evening, Mon-Sat) and sends one Telegram
message with only news you haven't been sent before. Edit data/holdings.csv when you buy or sell.

Usage:
  python holdings_news.py                 # check and send
  python holdings_news.py --no-notify     # dry run: print the message, send nothing, remember nothing
"""
import argparse
import json
import logging
from datetime import datetime
from pathlib import Path

import yaml

from scanner import notify
from scanner.holdings import run
from scanner.market_calendar import IST


def _send(text: str) -> bool:
    return notify._call("sendMessage", {"text": text, "parse_mode": "HTML",
                                        "disable_web_page_preview": "true"})


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--no-notify", action="store_true")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = yaml.safe_load(open(args.config))
    if not cfg.get("holdings_news", {}).get("enabled", True):
        print("holdings_news disabled in config")
        return
    notify.LAST.clear()
    res = run(cfg, datetime.now(IST), send=None if args.no_notify else _send)
    res["telegram"] = dict(notify.LAST)
    out = Path(cfg["output"]["dir"]) / "holdings_news_last.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(res, indent=2, ensure_ascii=False) + "\n")
    print("\n\n".join(res["messages"]))
    print(json.dumps({"sent": res["sent"], "telegram": res["telegram"]}, indent=2))


if __name__ == "__main__":
    main()
