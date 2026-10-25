"""Backtest the scan rules.  Usage: python backtest.py [--config config.yaml] [--limit N] [--start YYYY-MM-DD] [--end YYYY-MM-DD]"""
import argparse
import json
import logging

import yaml

from scanner.backtest import run_backtest


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--limit", type=int, help="first N stocks only (quick check)")
    ap.add_argument("--start")
    ap.add_argument("--end")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    cfg = yaml.safe_load(open(args.config))
    res = run_backtest(cfg, limit=args.limit, start=args.start, end=args.end)
    print(f"\nBacktest {res['period'][0]} -> {res['period'][1]} on {res['symbols']} symbols")
    print(json.dumps(res["overall"], indent=2))
    for name in ("by_setup", "by_regime"):
        for k, v in res[name].items():
            print(f"  {name[3:]:<7} {k:<9} trades {v['trades']:>4}  win {v.get('win_rate_pct')}%  "
                  f"exp {v.get('expectancy_r')}R  PF {v.get('profit_factor')}")
    print(f"Report: {res['files']['html']}")


if __name__ == "__main__":
    main()
