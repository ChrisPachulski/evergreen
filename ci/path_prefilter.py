#!/usr/bin/env python3
"""Classify bounded NUL-delimited Git paths without per-path subprocesses."""

import argparse
import os
from pathlib import Path, PurePosixPath
import re
import stat
import sys
import time

try:
    from .path_policy import is_protocol_path
except ImportError:  # Direct script execution.
    from path_policy import is_protocol_path


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


def _read_input(path: Path, max_bytes: int, deadline: float) -> tuple[bytes | None, str | None]:
    nonblocking = getattr(os, "O_NONBLOCK", None)
    nofollow = getattr(os, "O_NOFOLLOW", None)
    if nonblocking is None or nofollow is None:
        return None, "path prefilter requires nonblocking no-follow file reads"
    descriptor = None
    try:
        before = path.lstat()
        if (not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or
                before.st_size > max_bytes):
            return None, "path prefilter input is missing, unsafe, or oversized"
        if time.monotonic() > deadline:
            return None, "path prefilter exceeded wall-clock limit"
        descriptor = os.open(path, os.O_RDONLY | nonblocking | nofollow)
        opened = os.fstat(descriptor)
        if (not stat.S_ISREG(opened.st_mode) or opened.st_nlink != 1 or
                opened.st_size > max_bytes or
                (opened.st_dev, opened.st_ino) != (before.st_dev, before.st_ino)):
            return None, "path prefilter input changed or became unsafe"
        chunks = []
        remaining = max_bytes + 1
        while remaining:
            if time.monotonic() > deadline:
                return None, "path prefilter exceeded wall-clock limit"
            chunk = os.read(descriptor, min(65_536, remaining))
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        payload = b"".join(chunks)
        after_open = os.fstat(descriptor)
        if len(payload) > max_bytes:
            return None, "path prefilter input is oversized"
    except OSError as error:
        return None, f"path prefilter input could not be read: {error}"
    finally:
        if descriptor is not None:
            os.close(descriptor)
    try:
        after = path.lstat()
    except OSError as error:
        return None, f"path prefilter input could not be revalidated: {error}"
    identity = lambda item: (
        item.st_dev, item.st_ino, item.st_mode, item.st_nlink, item.st_size,
        item.st_mtime_ns, item.st_ctime_ns,
    )
    if identity(before) != identity(opened) or identity(opened) != identity(after_open) or \
            identity(opened) != identity(after):
        return None, "path prefilter input changed while it was read"
    if time.monotonic() > deadline:
        return None, "path prefilter exceeded wall-clock limit"
    return payload, None


def classify(
    path: Path, mode: str, *, max_bytes: int, max_paths: int, timeout_seconds: float
) -> tuple[bool | None, str | None]:
    deadline = time.monotonic() + timeout_seconds
    payload, error = _read_input(path, max_bytes, deadline)
    if error or payload is None:
        return None, error
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
        if not is_protocol_path(value):
            return None, "path prefilter encountered a path not citable by the result protocol"
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
