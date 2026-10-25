"""Long-running scheduler: runs the scan at the time set in config.yaml (default 3:00 AM IST).

Usage:
  python scheduler.py            # wait for the schedule
  python scheduler.py --run-now  # run once immediately, then keep the schedule
"""
import argparse
import logging

import yaml
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from run_scan import scan_and_notify, setup_logging

log = logging.getLogger("scheduler")


def job(cfg_path: str) -> None:
    cfg = yaml.safe_load(open(cfg_path))  # re-read so config edits apply without restart
    try:
        res = scan_and_notify(cfg)  # sends Telegram summary, or a failure alert
        log.info("Scheduled scan finished: session %s, %d picks", res["session"], len(res["picks"]))
    except Exception:
        log.exception("Scheduled scan failed")


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--run-now", action="store_true")
    args = ap.parse_args()
    cfg = yaml.safe_load(open(args.config))
    setup_logging(cfg["output"]["log_dir"])
    s = cfg["schedule"]
    trig = CronTrigger(hour=s["hour"], minute=s["minute"], day_of_week=s["day_of_week"],
                       timezone=s["timezone"])
    sched = BlockingScheduler(timezone=s["timezone"])
    sched.add_job(job, trig, args=[args.config], id="stock_scan",
                  misfire_grace_time=3600, coalesce=True, max_instances=1)
    if args.run_now:
        job(args.config)
    log.info("Scheduler started: %02d:%02d %s (%s)", s["hour"], s["minute"], s["timezone"],
             s["day_of_week"])
    sched.start()


if __name__ == "__main__":
    main()
