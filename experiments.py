"""Compare strategy variants.  Usage: python experiments.py [--config config.yaml] [--spec experiments.yaml] [--limit N]"""
import argparse
import logging

import yaml

from scanner.experiments import run_experiments


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default="config.yaml")
    ap.add_argument("--spec", default="experiments.yaml")
    ap.add_argument("--limit", type=int, help="first N stocks only (quick check)")
    args = ap.parse_args()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    res = run_experiments(yaml.safe_load(open(args.config)), yaml.safe_load(open(args.spec)),
                          limit=args.limit)
    print(res["markdown"])
    print(f"Report: {res['files']['md']}")


if __name__ == "__main__":
    main()
