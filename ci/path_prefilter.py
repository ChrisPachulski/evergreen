#!/usr/bin/env python3
"""Classify bounded NUL-delimited Git paths without per-path subprocesses."""

import argparse
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import time


DOC_SUFFIXES = {".md", ".markdown", ".rst"}
EXEMPT_DOC_PARTS = {
    "adr", "adrs", "archive", "archives", "audit", "audits", "plans", "readiness",
    "roadmaps", "specs",
}
DATED_DOC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[-_.]|$)")


def _living_doc(path: str) -> bool:
    pure = PurePosixPath(path)
    lower_parts = {part.casefold() for part in pure.parts}
    return (
        pure.suffix.casefold() in DOC_SUFFIXES
        and not lower_parts & EXEMPT_DOC_PARTS
        and not pure.name.casefold().startswith("changelog")
        and DATED_DOC_RE.match(pure.name) is None
    )


def classify(
    path: Path, mode: str, *, max_bytes: int, max_paths: int, timeout_seconds: float
) -> tuple[bool | None, str | None]:
    deadline = time.monotonic() + timeout_seconds
    try:
        metadata = path.lstat()
        if not stat.S_ISREG(metadata.st_mode) or metadata.st_size > max_bytes:
            return None, "path prefilter input is missing, unsafe, or oversized"
        payload = path.read_bytes()
    except OSError as error:
        return None, f"path prefilter input could not be read: {error}"
    if len(payload) > max_bytes:
        return None, "path prefilter input is oversized"
    for index, raw in enumerate(payload.split(b"\0")):
        if not raw:
            continue
        if index >= max_paths:
            return None, f"path prefilter exceeded {max_paths} paths"
        if time.monotonic() > deadline:
            return None, "path prefilter exceeded wall-clock limit"
        try:
            value = raw.decode("utf-8")
        except UnicodeDecodeError:
            return None, "path prefilter encountered invalid UTF-8"
        is_doc = PurePosixPath(value).suffix.casefold() in DOC_SUFFIXES
        if (mode == "code" and not is_doc) or (mode == "docs" and _living_doc(value)):
            return True, None
    return False, None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=("code", "docs"), required=True)
    parser.add_argument("--max-bytes", type=int, required=True)
    parser.add_argument("--max-paths", type=int, default=100_000)
    parser.add_argument("--timeout-seconds", type=float, required=True)
    parser.add_argument("path", type=Path)
    args = parser.parse_args()
    if args.max_bytes <= 0 or args.max_paths <= 0 or args.timeout_seconds <= 0:
        parser.error("bounds must be positive")
    matched, error = classify(
        args.path, args.mode, max_bytes=args.max_bytes,
        max_paths=args.max_paths, timeout_seconds=args.timeout_seconds,
    )
    if error:
        print(f"evergreen: {error}.", file=sys.stderr)
        return 2
    print("yes" if matched else "no")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
