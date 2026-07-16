"""logloom command line: parse log files, filter by level, print a summary line."""

import argparse
import sys

from .config import load_config
from .parser import PARSERS


def build_arg_parser():
    ap = argparse.ArgumentParser(prog="logloom")
    ap.add_argument("files", nargs="+", help="log files to read")
    ap.add_argument("--format", choices=sorted(PARSERS), help="wire format (default from config)")
    ap.add_argument("--level", help="keep only records at this level; empty keeps everything")
    # Parsed so old wrapper scripts passing the flag don't crash; refused in main().
    ap.add_argument("--parallel", action="store_true", help=argparse.SUPPRESS)
    return ap


def main(argv=None):
    args = build_arg_parser().parse_args(argv)
    if args.parallel:
        print("logloom: --parallel is not implemented; running nothing", file=sys.stderr)
        return 2
    cfg = load_config()
    parse = PARSERS[args.format or cfg["format"]]
    level = args.level if args.level is not None else cfg["level"]
    kept = 0
    malformed = 0
    for path in args.files:
        with open(path, encoding="utf-8") as handle:
            for raw in handle:
                line = raw.strip()
                if not line:
                    continue
                try:
                    record = parse(line)
                except ValueError:
                    malformed += 1
                    continue
                if level and record.get("level") != level:
                    continue
                kept += 1
    print(f"kept={kept} malformed={malformed}")
    return 1 if malformed else 0


if __name__ == "__main__":
    sys.exit(main())
