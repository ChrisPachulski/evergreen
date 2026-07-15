#!/usr/bin/env python3
"""Verify exact public TypeScript source witnesses without assigning oracle labels."""

from functools import partial
from pathlib import Path
import sys
from types import MappingProxyType

REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))
from eval.oracle.sources.java import generate as _implementation  # noqa: E402


CONFIG = _implementation.LanguageConfig(
    language="typescript",
    source_directory=Path(__file__).resolve().parent,
    toolchain_id="node-22.17.0-typescript-5.8.3",
    toolchain_identity_sha256=(
        "637ad921e96fdf5f64fffae36823babc780e404b6f3b06a9f511daea5ee45f64"
    ),
)
LANGUAGE = CONFIG.language
SOURCE_DIRECTORY = CONFIG.source_directory
TOOLCHAIN = MappingProxyType(
    {
        "toolchain_id": CONFIG.toolchain_id,
        "identity_sha256": CONFIG.toolchain_identity_sha256,
    }
)

CatalogError = _implementation.CatalogError
MUTATION_OPERATORS = _implementation.MUTATION_OPERATORS
validate_catalog = partial(_implementation.validate_catalog, _config=CONFIG)
discover_witnesses = partial(_implementation.discover_witnesses, _config=CONFIG)
discover_source_witnesses = partial(
    _implementation.discover_source_witnesses,
    _config=CONFIG,
)
generate_wrapper = partial(_implementation.generate_wrapper, _config=CONFIG)
verify_checkout = partial(_implementation.verify_checkout, _config=CONFIG)
build_report = partial(_implementation.build_report, _config=CONFIG)
canonical = _implementation.canonical
main = partial(_implementation.main, _config=CONFIG)


if __name__ == "__main__":
    raise SystemExit(main())
