"""Run one scan now.  Usage: python run_scan.py [--config config.yaml] [--no-news] [--limit N] [--no-notify] [--force]"""
import argparse
import json
import logging
from datetime import datetime
import traceback
from pathlib import Path

import yaml

from scanner import notify
from scanner.pipeline import run


def setup_logging(log_dir: str) -> None:
    Path(log_dir).mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler(Path(log_dir) / "scanner.log")],
    )


def scan_and_notify(cfg: dict, with_news: bool = True, limit: int | None = None,
                    send: bool = True, force: bool = False) -> dict:
    try:
        res = run(cfg, with_news=with_news, limit=limit, force=force)
    except Exception as e:
        if send:
            notify.send_failure(f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}")
        raise
    if res.get("duplicate"):
        logging.getLogger("run_scan").info("Session %s already scanned; no alert sent "
                                           "(use --force to resend)", res["session"])
        status = {"sendMessage": "skipped: session already alerted (run with --force to resend)"}
    elif not send:
        status = {"sendMessage": "skipped: --no-notify"}
    else:
        html_path = Path(cfg["output"]["dir"]) / f"scan_{res['session']}.html"
        status = notify.send_scan(res, cfg, html_path)
    res["notify"] = status
    out = Path(cfg["output"]["dir"]) / "last_notify.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"session": res["session"], "at": datetime.now().isoformat(timespec="seconds"),
                               **status}, indent=2) + "\n")
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--no-news", action="store_true")
    ap.add_argument("--no-notify", action="store_true", help="don't send Telegram messages")
    ap.add_argument("--limit", type=int, help="scan only the first N stocks (debugging)")
    ap.add_argument("--force", action="store_true",
                    help="alert even if this session was already scanned")
    args = ap.parse_args()
    cfg = yaml.safe_load(open(args.config))
    setup_logging(cfg["output"]["log_dir"])
    res = scan_and_notify(cfg, with_news=not args.no_news, limit=args.limit,
                          send=not args.no_notify, force=args.force)
    print(f"\nSession {res['session']} | regime {res['regime']['regime']} | "
          f"{len(res['picks'])} picks")
    for p in res["picks"]:
        print(f"  {p['symbol']:<12} {p['setup']:<9} score {p['score']:>5}  RS {p['rs_rank']:>3.0f}  "
              f"buy>{p['trigger']:.2f} SL {p['stop']:.2f} T1 {p['t1']:.2f} qty {p['qty']}  "
              f"{' '.join(p['flags'])}")
    o = res["journal"]["overall"]
    if o.get("trades"):
        print(f"Journal: {o['trades']} closed, win {o['win_rate_pct']}%, "
              f"expectancy {o['expectancy_r']}R")
    print(f"Reports in {cfg['output']['dir']}/scan_{res['session']}.*")


if __name__ == "__main__":
    main()
