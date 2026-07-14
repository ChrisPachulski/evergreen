#!/usr/bin/env python3
"""Verify exact public TypeScript source witnesses without assigning oracle labels."""

from pathlib import Path
import sys

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
from eval.oracle.sources.java import generate as _implementation  # noqa: E402


_implementation.LANGUAGE = "typescript"
_implementation.SOURCE_DIRECTORY = Path(__file__).resolve().parent
_implementation.TOOLCHAIN = {
    "toolchain_id": "node-22.17.0-typescript-5.8.3",
    "identity_sha256": "637ad921e96fdf5f64fffae36823babc780e404b6f3b06a9f511daea5ee45f64",
}

CatalogError = _implementation.CatalogError
MUTATION_OPERATORS = _implementation.MUTATION_OPERATORS
validate_catalog = _implementation.validate_catalog
discover_witnesses = _implementation.discover_witnesses
discover_source_witnesses = _implementation.discover_source_witnesses
generate_wrapper = _implementation.generate_wrapper
verify_checkout = _implementation.verify_checkout
build_report = _implementation.build_report
canonical = _implementation.canonical
main = _implementation.main


if __name__ == "__main__":
    raise SystemExit(main())
