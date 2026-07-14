"""Derive a Rust oracle wrapper from an exact, hash-bound source span."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import re
import sys

from . import generate


GENERATOR_ID = "rust-const-usize-return-v1"
MAXIMUM_SOURCE_BYTES = 1024 * 1024
SPEC_KEYS = {
    "schema_version", "kind", "generator_id", "source_id", "repository_path",
    "input_path", "blob_oid", "blob_sha256", "span", "symbol", "oracle_kind",
    "documentation_template",
}
SPAN_KEYS = {"start", "end", "sha256"}
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
NAME = re.compile(r"[A-Za-z][A-Za-z0-9_]*")


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode()


def _safe_path(value):
    if not isinstance(value, str):
        return False
    pure = PurePosixPath(value)
    return (
        value == pure.as_posix()
        and not pure.is_absolute()
        and all(part not in ("", ".", "..") and not part.startswith(".") for part in pure.parts)
    )


def _validate_spec(spec):
    span = spec.get("span") if isinstance(spec, dict) else None
    if (
        not isinstance(spec, dict)
        or set(spec) != SPEC_KEYS
        or spec["schema_version"] != 1
        or spec["kind"] != "evergreen-rust-derivation-spec"
        or spec["generator_id"] != GENERATOR_ID
        or not isinstance(spec["source_id"], str)
        or not generate.NAME.fullmatch(spec["source_id"])
        or not _safe_path(spec["repository_path"])
        or not spec["repository_path"].endswith(".rs")
        or not _safe_path(spec["input_path"])
        or not spec["input_path"].endswith(".rs")
        or not isinstance(spec["blob_oid"], str)
        or not HEX40.fullmatch(spec["blob_oid"])
        or not isinstance(spec["blob_sha256"], str)
        or not HEX64.fullmatch(spec["blob_sha256"])
        or not isinstance(span, dict)
        or set(span) != SPAN_KEYS
        or type(span["start"]) is not int
        or type(span["end"]) is not int
        or span["start"] < 0
        or span["end"] <= span["start"]
        or not isinstance(span["sha256"], str)
        or not HEX64.fullmatch(span["sha256"])
        or not isinstance(spec["symbol"], str)
        or not NAME.fullmatch(spec["symbol"])
        or spec["oracle_kind"] != "return-value"
        or not isinstance(spec["documentation_template"], str)
        or spec["documentation_template"]
        != f"The {spec['symbol']} state constant has value 1."
    ):
        raise ValueError("Rust derivation spec is invalid")


def derive(spec, source_bytes):
    """Recompute private wrapper material and a disclosure-safe receipt."""
    _validate_spec(spec)
    if (
        type(source_bytes) is not bytes
        or not source_bytes
        or len(source_bytes) > MAXIMUM_SOURCE_BYTES
        or hashlib.sha256(source_bytes).hexdigest() != spec["blob_sha256"]
        or spec["span"]["end"] > len(source_bytes)
    ):
        raise ValueError("Rust derivation source blob is invalid")
    span = source_bytes[spec["span"]["start"]:spec["span"]["end"]]
    if hashlib.sha256(span).hexdigest() != spec["span"]["sha256"]:
        raise ValueError("Rust derivation span is invalid")
    try:
        declaration = span.decode("ascii")
    except UnicodeError:
        raise ValueError("Rust derivation span is invalid") from None
    match = re.fullmatch(r"const ([A-Z][A-Z0-9_]*): usize = ([0-9]+);", declaration)
    if match is None or match.group(1) != spec["symbol"] or match.group(2) != "1":
        raise ValueError("Rust derivation span is invalid")
    wrapper = (
        declaration + "\n"
        f"fn value() -> i32 {{ return {spec['symbol']} as i32; }}\n"
        "fn main() { println!(\"{}\", value()); }\n"
    )
    documentation = spec["documentation_template"]
    receipt = {
        "schema_version": 1,
        "kind": "evergreen-rust-derivation-receipt",
        "generator_id": GENERATOR_ID,
        "generator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "receipt_schema_sha256": hashlib.sha256(
            Path(__file__).with_name("derivation-receipt-schema-v1.json").read_bytes()
        ).hexdigest(),
        "source_id": spec["source_id"],
        "repository_path": spec["repository_path"],
        "input_path": spec["input_path"],
        "blob_oid": spec["blob_oid"],
        "blob_sha256": spec["blob_sha256"],
        "span_start": spec["span"]["start"],
        "span_end": spec["span"]["end"],
        "span_sha256": spec["span"]["sha256"],
        "oracle_kind": spec["oracle_kind"],
        "observed_value": 1,
        "wrapper_sha256": hashlib.sha256(wrapper.encode()).hexdigest(),
        "documentation_sha256": hashlib.sha256(documentation.encode()).hexdigest(),
    }
    receipt["derivation_sha256"] = hashlib.sha256(canonical(receipt)).hexdigest()
    return {
        "schema_version": 1,
        "kind": "evergreen-rust-private-derivation",
        "private_material": {"code": wrapper, "documentation_template": documentation},
        "receipt": receipt,
    }


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def load_spec(path):
    try:
        spec = json.loads(
            Path(path).read_bytes(),
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        raise ValueError("Rust derivation spec is invalid") from None
    _validate_spec(spec)
    return spec


def derive_from_repository(spec, catalog, repository):
    records = [item for item in catalog["sources"] if item["source_id"] == spec["source_id"]]
    if len(records) != 1:
        raise ValueError("Rust derivation source is not in the catalog")
    record = records[0]
    generate.verify_sources([record], lambda _source_id: repository, minimum_sources=1)
    if (
        record["source"]["path"] != spec["repository_path"]
        or record["source"]["blob_oid"] != spec["blob_oid"]
        or record["source"]["sha256"] != spec["blob_sha256"]
    ):
        raise ValueError("Rust derivation source is not in the catalog")
    source_bytes = generate._blob(
        repository, record["commit"], record["source"]["path"], record["source"]["bytes"]
    )
    return derive(spec, source_bytes)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=Path(__file__).with_name("catalog.json"))
    parser.add_argument(
        "--spec", type=Path, default=Path(__file__).with_name("prototype-return-value.json")
    )
    parser.add_argument("--repository", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        result = derive_from_repository(
            load_spec(arguments.spec), generate.load_catalog(arguments.catalog), arguments.repository
        )
    except ValueError as error:
        parser.exit(2, f"Rust derivation invalid: {error}\n")
    sys.stdout.buffer.write(canonical(result) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
