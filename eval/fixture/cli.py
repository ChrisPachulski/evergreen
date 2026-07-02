"""shipit — a tiny release uploader."""
import argparse
import os
import sys

from config import load_config
from helpers import format_row, sort_releases


def build_parser():
    p = argparse.ArgumentParser(prog="shipit", description="Ship releases.")
    p.add_argument("--concurrency", type=int, default=4,
                   help="parallel upload streams")
    p.add_argument("--output", choices=["table", "json"], default="table",
                   help="render results as a table or as JSON")
    p.add_argument("--retries", type=int, default=3,
                   help="upload retry attempts before giving up")
    p.add_argument("-v", "--verbose", action="store_true")
    p.add_argument("--config", default="shipit.json")
    return p


def main(argv=None):
    args = build_parser().parse_args(argv)
    token = os.environ.get("SHIPIT_TOKEN")
    if not token:
        print("SHIPIT_TOKEN is not set", file=sys.stderr)
        return 2
    cfg = load_config(args.config)
    releases = sort_releases(cfg.get("releases", []))
    for r in releases:
        print(format_row(r, as_json=args.output == "json"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
