"""Run one scan now.  Usage: python run_scan.py [--config config.yaml] [--no-news] [--limit N] [--no-notify]"""
import argparse
import logging
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
                    send: bool = True) -> dict:
    try:
        res = run(cfg, with_news=with_news, limit=limit)
    except Exception as e:
        if send:
            notify.send_failure(f"{type(e).__name__}: {e}\n{traceback.format_exc(limit=3)}")
        raise
    if send:
        html_path = Path(cfg["output"]["dir"]) / f"scan_{res['session']}.html"
        notify.send_scan(res, cfg, html_path)
    return res


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--no-news", action="store_true")
    ap.add_argument("--no-notify", action="store_true", help="don't send Telegram messages")
    ap.add_argument("--limit", type=int, help="scan only the first N stocks (debugging)")
    args = ap.parse_args()
    cfg = yaml.safe_load(open(args.config))
    setup_logging(cfg["output"]["log_dir"])
    res = scan_and_notify(cfg, with_news=not args.no_news, limit=args.limit,
                          send=not args.no_notify)
    print(f"\nSession {res['session']} | regime {res['regime']['regime']} | "
          f"{len(res['picks'])} picks")
    for p in res["picks"]:
        n = p["news"]
        print(f"  {p['symbol']:<12} {p['setup']:<9} score {p['score']:>5}  "
              f"R:R {p['reward_risk']:.1f}  news={'yes' if n.get('has_news') else 'no'}"
              f"{' (fresh)' if n.get('fresh') else ''}  {' '.join(p['flags'])}")
    print(f"Reports in {cfg['output']['dir']}/scan_{res['session']}.*")


if __name__ == "__main__":
    main()
