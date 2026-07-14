#!/usr/bin/env python3
"""Freeze and reverify exact upstream Python source-byte inventories."""

from functools import partial
from pathlib import Path
import sys


REPOSITORY = Path(__file__).resolve().parents[4]
if str(REPOSITORY) not in sys.path:
    sys.path.insert(0, str(REPOSITORY))

from eval.oracle.sources import _catalog  # noqa: E402


LANGUAGE = "python"
EXTENSION = ".py"
CONFIG = _catalog.CatalogConfig(
    language=LANGUAGE,
    extension=EXTENSION,
    recipe=Path(__file__).with_name("extract-v1.json"),
)
RECORD_KEYS = _catalog.RECORD_KEYS
canonical = _catalog.canonical
sha256 = _catalog.sha256
freeze_project = partial(_catalog.freeze_project, CONFIG)
verify_project = partial(_catalog.verify_project, CONFIG)
catalog = partial(_catalog.catalog, CONFIG)
validate_catalog = partial(_catalog.validate_catalog, CONFIG)
freeze_catalog = partial(_catalog.freeze_catalog, CONFIG)
provenance_record = _catalog.provenance_record


def main(argv=None):
    return _catalog.main(CONFIG, argv)


if __name__ == "__main__":
    sys.exit(main())
