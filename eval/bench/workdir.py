"""Canonical resolver for EVERGREEN_WORK_DIR — the one out-of-tree work root.

Benchmark sinks (probe artifacts, scratch datasets, run caches, ...) used to litter
$HOME with flat `evergreen-<purpose>` directories. This module resolves a single
work root instead: `$EVERGREEN_WORK_DIR` if set, else the XDG data home, else
`~/.local/share/evergreen`. `work_dir(purpose)` layers a legacy shim on top so an
existing `~/evergreen-<purpose>` directory keeps winning until callers migrate.

Never creates a directory — callers are responsible for that.

Python 3 stdlib only.
"""

import os
import re
from pathlib import Path

PURPOSE_RE = re.compile(r"^[a-z][a-z0-9-]*$")


def work_root(environ=os.environ, home=None):
    explicit = environ.get("EVERGREEN_WORK_DIR")
    if explicit:
        return Path(explicit)
    home = Path(home) if home is not None else Path.home()
    base = environ.get("XDG_DATA_HOME") or str(home / ".local" / "share")
    return Path(base) / "evergreen"


def work_dir(purpose, environ=os.environ, home=None):
    if not PURPOSE_RE.fullmatch(purpose):
        raise ValueError(f"invalid purpose {purpose!r}: must match {PURPOSE_RE.pattern}")
    home = Path(home) if home is not None else Path.home()
    legacy = home / f"evergreen-{purpose}"
    if legacy.exists():
        return legacy
    return work_root(environ, home) / purpose
