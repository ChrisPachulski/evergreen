"""Verify Rust source identities against exact local Git objects."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys


LANGUAGE = "rust"
MINIMUM_SOURCES = 20
MAXIMUM_BLOB_BYTES = 1024 * 1024
LICENSES = {"0BSD", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "MIT"}
HEX40 = re.compile(r"[0-9a-f]{40}")
HEX64 = re.compile(r"[0-9a-f]{64}")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
PROJECT = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
RECIPE_PATH = Path(__file__).with_name("extract-v1.json")

CATALOG_KEYS = {"schema_version", "kind", "language", "recipe_sha256", "sources"}
SOURCE_KEYS = {
    "source_id", "project", "lineage_id", "origin", "commit", "tree", "license",
    "source", "extracted_tree_sha256",
}
LICENSE_KEYS = {"spdx", "path", "sha256"}
BLOB_KEYS = {"path", "blob_oid", "sha256", "bytes"}


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":")
    ).encode()


def extracted_tree_sha256(source):
    witness = {
        "kind": "evergreen-source-tree-membership-v1",
        "schema_version": 1,
        "files": [{key: source[key] for key in ("path", "blob_oid", "sha256", "bytes")}],
    }
    return hashlib.sha256(canonical(witness)).hexdigest()


def _safe_path(value):
    if not isinstance(value, str):
        return False
    pure = PurePosixPath(value)
    return (
        value == pure.as_posix()
        and not pure.is_absolute()
        and all(part not in ("", ".", "..") and not part.startswith(".") for part in pure.parts)
        and all(re.fullmatch(r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*", part) for part in pure.parts)
    )


def _git(repository, *arguments, maximum=4096):
    environment = os.environ.copy()
    environment.update({
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
    })
    try:
        result = subprocess.run(
            ["git", "--no-replace-objects", "-C", str(repository), *arguments],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=15,
            env=environment,
        )
    except (OSError, subprocess.CalledProcessError, subprocess.TimeoutExpired):
        raise ValueError("source does not match pinned Git objects") from None
    if len(result.stdout) > maximum:
        raise ValueError("source does not match pinned Git objects")
    return result.stdout


def _blob(repository, commit, path, expected_size):
    object_name = f"{commit}:{path}"
    size_raw = _git(repository, "cat-file", "-s", object_name)
    try:
        size = int(size_raw)
    except ValueError:
        raise ValueError("source does not match pinned Git objects") from None
    if size != expected_size or not 0 < size <= MAXIMUM_BLOB_BYTES:
        raise ValueError("source does not match pinned Git objects")
    return _git(repository, "show", "--no-ext-diff", object_name, maximum=size)


def verified_source_blob(repository, commit, source):
    """Return bytes only for the exact regular blob named by the witness."""
    listing = _git(repository, "ls-tree", "-z", commit, "--", source["path"])
    records = [record for record in listing.split(b"\0") if record]
    if len(records) != 1 or b"\t" not in records[0]:
        raise ValueError("source does not match pinned Git objects")
    metadata, raw_path = records[0].split(b"\t", 1)
    fields = metadata.split()
    try:
        path = raw_path.decode()
    except UnicodeError:
        raise ValueError("source does not match pinned Git objects") from None
    if (
        path != source["path"]
        or len(fields) != 3
        or fields[0] not in (b"100644", b"100755")
        or fields[1] != b"blob"
        or fields[2].decode() != source["blob_oid"]
    ):
        raise ValueError("source does not match pinned Git objects")
    size_raw = _git(repository, "cat-file", "-s", source["blob_oid"], maximum=64)
    try:
        size = int(size_raw)
    except ValueError:
        raise ValueError("source does not match pinned Git objects") from None
    if size != source["bytes"] or not 0 < size <= MAXIMUM_BLOB_BYTES:
        raise ValueError("source does not match pinned Git objects")
    source_bytes = _git(
        repository, "cat-file", "blob", source["blob_oid"], maximum=size
    )
    if len(source_bytes) != size or hashlib.sha256(source_bytes).hexdigest() != source["sha256"]:
        raise ValueError("source does not match pinned Git objects")
    return source_bytes


def _validate_record(record):
    if not isinstance(record, dict) or set(record) != SOURCE_KEYS:
        raise ValueError("Rust source record fields are invalid")
    license_record = record["license"]
    source = record["source"]
    if (
        not isinstance(record["source_id"], str)
        or not NAME.fullmatch(record["source_id"])
        or not isinstance(record["project"], str)
        or not PROJECT.fullmatch(record["project"])
        or not isinstance(record["lineage_id"], str)
        or not NAME.fullmatch(record["lineage_id"])
        or not isinstance(record["origin"], str)
        or not record["origin"].startswith("https://")
        or not record["origin"].endswith(".git")
        or not isinstance(record["commit"], str)
        or not HEX40.fullmatch(record["commit"])
        or not isinstance(record["tree"], str)
        or not HEX40.fullmatch(record["tree"])
        or not isinstance(license_record, dict)
        or set(license_record) != LICENSE_KEYS
        or license_record["spdx"] not in LICENSES
        or not _safe_path(license_record["path"])
        or not isinstance(license_record["sha256"], str)
        or not HEX64.fullmatch(license_record["sha256"])
        or not isinstance(source, dict)
        or set(source) != BLOB_KEYS
        or not _safe_path(source["path"])
        or not source["path"].endswith(".rs")
        or not isinstance(source["blob_oid"], str)
        or not HEX40.fullmatch(source["blob_oid"])
        or not isinstance(source["sha256"], str)
        or not HEX64.fullmatch(source["sha256"])
        or type(source["bytes"]) is not int
        or not 0 < source["bytes"] <= MAXIMUM_BLOB_BYTES
        or not isinstance(record["extracted_tree_sha256"], str)
        or not HEX64.fullmatch(record["extracted_tree_sha256"])
        or record["extracted_tree_sha256"] != extracted_tree_sha256(source)
    ):
        raise ValueError("Rust source record is invalid")


def _verified_record(record, repository, recipe_sha256):
    _validate_record(record)
    try:
        remote = _git(repository, "remote", "get-url", "origin").decode().strip()
        commit = _git(repository, "rev-parse", f"{record['commit']}^{{commit}}").decode().strip()
        tree = _git(repository, "rev-parse", f"{record['commit']}^{{tree}}").decode().strip()
        license_bytes = _blob(
            repository,
            record["commit"],
            record["license"]["path"],
            int(_git(
                repository,
                "cat-file",
                "-s",
                f"{record['commit']}:{record['license']['path']}",
            )),
        )
        verified_source_blob(repository, record["commit"], record["source"])
    except (UnicodeError, ValueError):
        raise ValueError("source does not match pinned Git objects") from None
    if (
        remote != record["origin"]
        or commit != record["commit"]
        or tree != record["tree"]
        or hashlib.sha256(license_bytes).hexdigest() != record["license"]["sha256"]
    ):
        raise ValueError("source does not match pinned Git objects")
    return {
        **record,
        "language": LANGUAGE,
        "extraction": {
            "recipe_path": "rust/extract-v1.json",
            "recipe_sha256": recipe_sha256,
            "argv": [
                "git", "show", "--no-ext-diff",
                f"{record['commit']}:{record['source']['path']}",
            ],
        },
    }


def verify_sources(sources, repository_for, minimum_sources=MINIMUM_SOURCES):
    if not isinstance(sources, list) or len(sources) < minimum_sources:
        raise ValueError("Rust inventory requires at least 20 distinct sources")
    seen = {"source_id": set(), "project": set(), "lineage_id": set(),
            "extracted_tree_sha256": set()}
    for record in sources:
        _validate_record(record)
        for field, values in seen.items():
            if record[field] in values:
                raise ValueError("duplicate source identity")
            values.add(record[field])
    recipe_sha256 = hashlib.sha256(RECIPE_PATH.read_bytes()).hexdigest()
    return [
        _verified_record(record, repository_for(record["source_id"]), recipe_sha256)
        for record in sorted(sources, key=lambda item: item["source_id"])
    ]


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def load_catalog(path):
    try:
        document = json.loads(
            Path(path).read_bytes(),
            object_pairs_hook=_unique_object,
            parse_constant=lambda value: (_ for _ in ()).throw(ValueError(value)),
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        raise ValueError("Rust source catalog is invalid") from None
    recipe_sha256 = hashlib.sha256(RECIPE_PATH.read_bytes()).hexdigest()
    if (
        not isinstance(document, dict)
        or set(document) != CATALOG_KEYS
        or document["schema_version"] != 1
        or document["kind"] != "evergreen-rust-public-source-catalog"
        or document["language"] != LANGUAGE
        or document["recipe_sha256"] != recipe_sha256
        or not isinstance(document["sources"], list)
    ):
        raise ValueError("Rust source catalog is invalid")
    return document


def provenance_record(_record):
    raise ValueError("a runnable adapter receipt is required before candidates become seed claims")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", type=Path, default=Path(__file__).with_name("catalog.json"))
    parser.add_argument("--repositories", type=Path, required=True)
    arguments = parser.parse_args(argv)
    try:
        catalog = load_catalog(arguments.catalog)
        records = verify_sources(
            catalog["sources"], lambda source_id: arguments.repositories / source_id
        )
    except ValueError as error:
        parser.exit(2, f"Rust source inventory invalid: {error}\n")
    result = {
        "schema_version": 1,
        "kind": "evergreen-verified-rust-source-inventory",
        "language": LANGUAGE,
        "recipe_sha256": catalog["recipe_sha256"],
        "sources": records,
    }
    sys.stdout.buffer.write(canonical(result) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
