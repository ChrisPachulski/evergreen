#!/usr/bin/env python3
"""Verify exact public Java source witnesses without assigning oracle labels."""

import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
import tempfile
from urllib.parse import urlsplit


REPOSITORY_ROOT = Path(__file__).resolve().parents[4]
if str(REPOSITORY_ROOT) not in sys.path:
    sys.path.insert(0, str(REPOSITORY_ROOT))

from eval.oracle.oracle import (  # noqa: E402
    CONTROL_PATH, LANGUAGE_ADAPTERS, MUTATION_OPERATORS, ORACLE_KINDS,
)


LANGUAGE = "java"
SOURCE_DIRECTORY = Path(__file__).resolve().parent
TOOLCHAIN = {
    "toolchain_id": "temurin-21.0.7+6",
    "identity_sha256": "1fd86aa5958aa36d57f3d34748c762a1c6aed3067e549523c3d5d3f70e26f51c",
}
ALLOWED_LICENSES = {
    "0BSD", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "MIT", "Python-2.0",
}
HEX = re.compile(r"[0-9a-f]{64}")
COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
NAME = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
PROJECT = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
MAX_BLOB_BYTES = 1024 * 1024


class CatalogError(ValueError):
    """The public source catalog or pinned Git evidence is invalid."""


def canonical(value):
    return json.dumps(
        value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"),
    ).encode()


def _safe_path(value):
    if type(value) is not str:
        return False
    path = PurePosixPath(value)
    return (
        bool(value) and len(value.encode()) <= 4096 and not path.is_absolute()
        and path.as_posix() == value and "\\" not in value and "//" not in value
        and all(part not in ("", ".", "..") and not part.startswith(".") for part in path.parts)
    )


def _strict_load(path):
    def unique(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise CatalogError("catalog contains a duplicate JSON key")
            result[key] = value
        return result

    try:
        raw = Path(path).read_bytes()
        if len(raw) > MAX_BLOB_BYTES:
            raise CatalogError("catalog is too large")
        return json.loads(
            raw, parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)),
            object_pairs_hook=unique,
        )
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        if isinstance(error, CatalogError):
            raise
        raise CatalogError("catalog is unavailable or invalid") from None


def validate_catalog(document):
    if not isinstance(document, dict) or set(document) != {
            "schema_version", "kind", "language", "sources"}:
        raise CatalogError("catalog fields are invalid")
    if (type(document["schema_version"]) is not int or document["schema_version"] != 1
            or document["kind"] != "evergreen-oracle-language-source-catalog"
            or document["language"] != LANGUAGE or not isinstance(document["sources"], list)):
        raise CatalogError("catalog contract is invalid")
    seen_ids = set()
    seen_projects = set()
    for source in document["sources"]:
        if not isinstance(source, dict) or set(source) != {
                "source_id", "project", "lineage_id", "origin", "commit", "tree",
                "license", "source", "witnesses"}:
            raise CatalogError("catalog source fields are invalid")
        parsed = urlsplit(source["origin"]) if type(source["origin"]) is str else None
        if (type(source["source_id"]) is not str or not NAME.fullmatch(source["source_id"])
                or source["source_id"] in seen_ids
                or type(source["project"]) is not str or not PROJECT.fullmatch(source["project"])
                or source["project"] in seen_projects
                or type(source["lineage_id"]) is not str or not NAME.fullmatch(source["lineage_id"])
                or parsed is None or parsed.scheme != "https" or not parsed.hostname
                or parsed.username is not None or parsed.password is not None
                or parsed.query or parsed.fragment
                or type(source["commit"]) is not str or not COMMIT.fullmatch(source["commit"])
                or type(source["tree"]) is not str or not COMMIT.fullmatch(source["tree"])):
            raise CatalogError("catalog source identity is invalid")
        seen_ids.add(source["source_id"])
        seen_projects.add(source["project"])
        license_record = source["license"]
        if (not isinstance(license_record, dict) or set(license_record) != {
                "spdx", "path", "sha256"}
                or license_record["spdx"] not in ALLOWED_LICENSES
                or not _safe_path(license_record["path"])
                or type(license_record["sha256"]) is not str
                or not HEX.fullmatch(license_record["sha256"])):
            raise CatalogError("catalog license identity is invalid")
        blob = source["source"]
        if (not isinstance(blob, dict) or set(blob) != {"path", "blob_oid", "sha256"}
                or not _safe_path(blob["path"])
                or type(blob["blob_oid"]) is not str or not COMMIT.fullmatch(blob["blob_oid"])
                or type(blob["sha256"]) is not str or not HEX.fullmatch(blob["sha256"])):
            raise CatalogError("catalog source blob identity is invalid")
        witnesses = source["witnesses"]
        if not isinstance(witnesses, list):
            raise CatalogError("catalog witnesses are invalid")
        identities = set()
        for witness in witnesses:
            if not isinstance(witness, dict) or set(witness) != {"kind", "operator", "offset"}:
                raise CatalogError("catalog witness fields are invalid")
            contract = MUTATION_OPERATORS.get(witness["operator"])
            identity = (witness["operator"], witness["offset"])
            if (witness["kind"] not in ORACLE_KINDS or contract is None
                    or contract["kind"] != witness["kind"] or LANGUAGE not in contract["variants"]
                    or type(witness["offset"]) is not int or witness["offset"] < 0
                    or identity in identities):
                raise CatalogError("catalog witness is invalid")
            identities.add(identity)
    return document


def discover_witnesses(code):
    witnesses = []
    for operator, contract in MUTATION_OPERATORS.items():
        variant = contract["variants"][LANGUAGE]
        for match in re.finditer(variant["source_pattern"], code):
            cursor = match.start()
            while True:
                offset = code.find(variant["before"], cursor, match.end())
                if offset < 0:
                    break
                witnesses.append({
                    "kind": contract["kind"], "operator": operator, "offset": offset,
                })
                cursor = offset + len(variant["before"])
    order = {kind: index for index, kind in enumerate(ORACLE_KINDS)}
    return sorted(witnesses, key=lambda item: (order[item["kind"]], item["offset"], item["operator"]))


_OPERATOR_BY_KIND = {
    contract["kind"]: operator for operator, contract in MUTATION_OPERATORS.items()
}
_SOURCE_SPANS = {
    "java": {
        "return-value": b"return 1",
        "raises": b"throw new IllegalStateException",
        "default-value": b"orElse(1)",
        "cardinality": b"{1}",
        "state-change": b"!state",
    },
    "typescript": {
        "return-value": b"return 1",
        "raises": b"throw new Error",
        "default-value": b"item = 1",
        "cardinality": b"[1]",
        "state-change": b"!state",
    },
}
_SOURCE_CONTEXT_PATTERNS = {
    "java": {
        "return-value": rb"\breturn 1\b",
        "raises": rb"\bthrow new IllegalStateException\b",
        "default-value": rb"\borElse\(1\)",
        "cardinality": rb"\bint\s*\[\s*\]\s+[A-Za-z_$][A-Za-z0-9_$]*\s*=\s*\{1\}",
        "state-change": rb"\bstate\s*=\s*!state\b",
    },
    "typescript": {
        "return-value": rb"\breturn 1\b",
        "raises": rb"\bthrow new Error\b",
        "default-value": rb"\b(?:function\s+[A-Za-z_$][A-Za-z0-9_$]*\s*\([^)]*\bitem = 1\b|\([^)]*\bitem = 1\b)",
        "cardinality": rb"\b(?:const|let)\s+[A-Za-z_$][A-Za-z0-9_$]*\s*=\s*\[1\]",
        "state-change": rb"\bstate\s*=\s*!state\b",
    },
}


def _mask_comments_and_literals(code):
    """Preserve byte offsets while hiding non-code Java/TypeScript regions."""
    masked = bytearray(code)
    index = 0
    state = "code"
    quote = None
    while index < len(code):
        pair = code[index:index + 2]
        if state == "code":
            if pair == b"//":
                masked[index:index + 2] = b"  "
                state = "line-comment"
                index += 2
                continue
            if pair == b"/*":
                masked[index:index + 2] = b"  "
                state = "block-comment"
                index += 2
                continue
            if code[index:index + 3] == b'"""':
                masked[index:index + 3] = b"   "
                state = "triple-string"
                index += 3
                continue
            if code[index] in (ord("'"), ord('"')) or (
                    LANGUAGE == "typescript" and code[index] == ord("`")):
                quote = code[index]
                masked[index] = 32
                state = "string"
                index += 1
                continue
            index += 1
            continue
        if state == "line-comment":
            if code[index] in (10, 13):
                state = "code"
            else:
                masked[index] = 32
            index += 1
            continue
        if state == "block-comment":
            if pair == b"*/":
                masked[index:index + 2] = b"  "
                state = "code"
                index += 2
            else:
                if code[index] not in (10, 13):
                    masked[index] = 32
                index += 1
            continue
        if state == "triple-string":
            if code[index:index + 3] == b'"""':
                masked[index:index + 3] = b"   "
                state = "code"
                index += 3
            else:
                if code[index] not in (10, 13):
                    masked[index] = 32
                index += 1
            continue
        if code[index] == ord("\\"):
            masked[index] = 32
            if index + 1 < len(code):
                if code[index + 1] not in (10, 13):
                    masked[index + 1] = 32
                index += 2
            else:
                index += 1
            continue
        current = code[index]
        masked[index] = 32 if current not in (10, 13) else current
        index += 1
        if current == quote:
            state = "code"
            quote = None
    return bytes(masked)


def discover_source_witnesses(code):
    """Find exact source spans that the wrapper recipe can consume."""
    if not isinstance(code, bytes):
        raise CatalogError("source witness input is invalid")
    searchable = _mask_comments_and_literals(code)
    witnesses = []
    for kind in ORACLE_KINDS:
        span = _SOURCE_SPANS[LANGUAGE][kind]
        for match in re.finditer(_SOURCE_CONTEXT_PATTERNS[LANGUAGE][kind], searchable):
            offset = searchable.find(span, match.start(), match.end())
            if offset < 0:
                raise CatalogError("source wrapper context omitted its bound span")
            witnesses.append({
                "kind": kind, "operator": _OPERATOR_BY_KIND[kind], "offset": offset,
            })
    return witnesses


_WRAPPER_TEMPLATES = {
    "java": {
        "return-value": """public final class OracleCase {
    static int value() { %(fragment)s; }
    public static void main(String[] args) { System.out.println(value()); }
}
""",
        "raises": """public final class OracleCase {
    static void value() { if (false) %(fragment)s("expected"); }
    public static void main(String[] args) {
        try { value(); System.out.println("no-error"); }
        catch (IllegalStateException error) { System.out.println("ValueError"); }
    }
}
""",
        "default-value": """import java.util.Optional;
public final class OracleCase {
    static int value() { return (int) Optional.ofNullable(null).%(fragment)s; }
    public static void main(String[] args) { System.out.println("default:" + value()); }
}
""",
        "cardinality": """public final class OracleCase {
    public static void main(String[] args) {
        int[] items = %(fragment)s;
        System.out.println("cardinality:" + items.length);
    }
}
""",
        "state-change": """public final class OracleCase {
    public static void main(String[] args) {
        boolean state = false;
        state = %(fragment)s;
        System.out.println("state:" + (state ? "changed" : "unchanged"));
    }
}
""",
    },
    "typescript": {
        "return-value": """function value() { %(fragment)s; }
console.log(value());
""",
        "raises": """function value() { if (false) { %(fragment)s("expected"); } }
try { value(); console.log("no-error"); }
catch (error) { console.log("ValueError"); }
""",
        "default-value": """function value(%(fragment)s) { return item; }
console.log(`default:${value()}`);
""",
        "cardinality": """const items = %(fragment)s;
console.log(`cardinality:${items.length}`);
""",
        "state-change": """let state = false;
state = %(fragment)s;
console.log(`state:${state ? "changed" : "unchanged"}`);
""",
    },
}


def generate_wrapper(code, witness):
    """Derive one deterministic wrapper from an exact pinned source span."""
    if not isinstance(code, bytes) or not isinstance(witness, dict):
        raise CatalogError("wrapper input is invalid")
    operator = MUTATION_OPERATORS.get(witness.get("operator"))
    if (operator is None or witness.get("kind") != operator["kind"]
            or type(witness.get("offset")) is not int or witness["offset"] < 0):
        raise CatalogError("wrapper witness is invalid")
    if LANGUAGE not in operator["variants"]:
        raise CatalogError("wrapper language is unsupported")
    offset = witness["offset"]
    expected_span = _SOURCE_SPANS[LANGUAGE][operator["kind"]]
    span = code[offset:offset + len(expected_span)]
    if span != expected_span or witness not in discover_source_witnesses(code):
        raise CatalogError("wrapper witness is not an exact pinned source span")
    try:
        fragment = span.decode("ascii")
    except UnicodeError:
        raise CatalogError("wrapper source span is not ASCII") from None
    template = _WRAPPER_TEMPLATES[LANGUAGE][operator["kind"]]
    wrapper_code = template % {"fragment": fragment}
    return {
        "schema_version": 1,
        "kind": "evergreen-source-bound-oracle-wrapper",
        "language": LANGUAGE,
        "oracle_kind": operator["kind"],
        "operator": witness["operator"],
        "recipe_id": f"{LANGUAGE}-exact-span-wrapper-v1",
        "source_binding": {
            "offset": offset,
            "length": len(span),
            "span_sha256": hashlib.sha256(span).hexdigest(),
            "source_blob_sha256": hashlib.sha256(code).hexdigest(),
        },
        "code": wrapper_code,
        "code_sha256": hashlib.sha256(wrapper_code.encode()).hexdigest(),
    }


def _git(repo, *arguments, maximum=MAX_BLOB_BYTES):
    environment = os.environ.copy()
    environment.update({
        "GIT_CONFIG_GLOBAL": os.devnull, "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0", "LC_ALL": "C",
    })
    try:
        completed = subprocess.run(
            ["git", "-C", str(repo), *arguments], capture_output=True,
            timeout=120, check=False, env=environment,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise CatalogError("Git verification failed") from None
    if completed.returncode or len(completed.stdout) > maximum or len(completed.stderr) > 64 * 1024:
        raise CatalogError("Git verification failed")
    return completed.stdout


def _regular_blob(repo, commit, path, label):
    listing = _git(repo, "ls-tree", "-z", commit, "--", path, maximum=4096)
    records = [record for record in listing.split(b"\0") if record]
    if len(records) != 1:
        raise CatalogError(f"{label} is not one exact Git blob")
    try:
        metadata, raw_path = records[0].split(b"\t", 1)
        mode, kind, object_id = metadata.decode("ascii").split(" ")
        actual_path = raw_path.decode("utf-8")
    except (UnicodeError, ValueError):
        raise CatalogError(f"{label} Git identity is invalid") from None
    if mode not in ("100644", "100755") or kind != "blob" or actual_path != path:
        raise CatalogError(f"{label} is not a regular Git blob")
    return object_id, _git(repo, "show", "--no-ext-diff", f"{commit}:{path}")


def verify_checkout(source, checkout):
    commit = _git(checkout, "rev-parse", "--verify", f'{source["commit"]}^{{commit}}', maximum=256).decode().strip()
    tree = _git(checkout, "rev-parse", "--verify", f'{source["commit"]}^{{tree}}', maximum=256).decode().strip()
    if commit != source["commit"]:
        raise CatalogError("source commit identity does not match")
    if tree != source["tree"]:
        raise CatalogError("source tree identity does not match")
    _license_oid, license_bytes = _regular_blob(
        checkout, source["commit"], source["license"]["path"], "license",
    )
    if hashlib.sha256(license_bytes).hexdigest() != source["license"]["sha256"]:
        raise CatalogError("license bytes do not match")
    blob_oid, code = _regular_blob(
        checkout, source["commit"], source["source"]["path"], "source blob",
    )
    if blob_oid != source["source"]["blob_oid"]:
        raise CatalogError("source blob Git object does not match")
    if hashlib.sha256(code).hexdigest() != source["source"]["sha256"]:
        raise CatalogError("source blob bytes do not match")
    discovered = discover_source_witnesses(code)
    if source["witnesses"] != discovered:
        raise CatalogError("catalog witnesses do not match exact source bytes")
    counts = {kind: 0 for kind in ORACLE_KINDS}
    for witness in discovered:
        counts[witness["kind"]] += 1
    extracted_identity = [{
        "repository_path": source["source"]["path"],
        "input_path": source["source"]["path"],
        "blob_oid": blob_oid,
        "sha256": source["source"]["sha256"],
        "oracle_kind": kind,
    } for kind in ORACLE_KINDS if counts[kind]]
    return {
        "source_id": source["source_id"], "project": source["project"],
        "lineage_id": source["lineage_id"], "origin": source["origin"],
        "commit": commit, "tree": tree, "license": source["license"],
        "source": source["source"], "witnesses": discovered,
        "source_blobs": extracted_identity,
        "oracle_kind_counts": counts,
        "extracted_tree_sha256": hashlib.sha256(canonical(extracted_identity)).hexdigest(),
    }


def build_report(catalog, verified_sources):
    expected_ids = [source["source_id"] for source in catalog["sources"]]
    if [source["source_id"] for source in verified_sources] != expected_ids:
        raise CatalogError("verified source set does not match catalog")
    recipe_path = SOURCE_DIRECTORY / "extract-v1.json"
    recipe_raw = recipe_path.read_bytes()
    recipe_sha256 = hashlib.sha256(recipe_raw).hexdigest()
    records = []
    for source in verified_sources:
        argv = ["git", "show", "--no-ext-diff", f'{source["commit"]}:{source["source"]["path"]}']
        harness_unsigned = {
            "adapter_id": f"{LANGUAGE}-oracle-v1",
            "argv": [LANGUAGE_ADAPTERS[LANGUAGE], f'/input/{source["source"]["path"]}', CONTROL_PATH],
        }
        records.append({
            **source,
            "extraction": {
                "recipe_path": f"{LANGUAGE}/extract-v1.json",
                "recipe_sha256": recipe_sha256,
                "argv": argv,
            },
            "harness": {
                **harness_unsigned,
                "sha256": hashlib.sha256(canonical(harness_unsigned)).hexdigest(),
            },
            "toolchain_id": TOOLCHAIN["toolchain_id"],
            "toolchain_identity_sha256": TOOLCHAIN["identity_sha256"],
            "execution_receipt": None,
        })
    byte_bound = sum(sum(item["oracle_kind_counts"].values()) for item in records)
    reasons = ["digest-addressed-adapter-execution-receipt-missing"]
    if len(records) < 20:
        reasons.append("projects-below-20")
    reasons.append("executable-seeds-below-250")
    return {
        "schema_version": 1,
        "kind": "evergreen-oracle-language-source-catalog-verification",
        "language": LANGUAGE,
        "projects": len(records),
        "byte_bound_candidates": byte_bound,
        "executable_seeds": 0,
        "project_shortfall": max(0, 20 - len(records)),
        "seed_shortfall": 250,
        "ready": False,
        "readiness_reasons": reasons,
        "sources": records,
    }


def _fetch_and_verify(source):
    with tempfile.TemporaryDirectory(prefix=f"evergreen-{LANGUAGE}-source-") as temporary:
        repo = Path(temporary) / "repo"
        _git(temporary, "init", "-q", str(repo), maximum=4096)
        _git(repo, "remote", "add", "origin", source["origin"], maximum=4096)
        _git(
            repo, "-c", "protocol.file.allow=never", "fetch", "--quiet", "--depth=1",
            "--filter=blob:none", "origin", source["commit"], maximum=4096,
        )
        return verify_checkout(source, repo)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--catalog", default=str(SOURCE_DIRECTORY / "catalog.json"))
    arguments = parser.parse_args(argv)
    try:
        catalog = validate_catalog(_strict_load(arguments.catalog))
        report = build_report(catalog, [_fetch_and_verify(source) for source in catalog["sources"]])
    except CatalogError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    sys.stdout.buffer.write(canonical(report) + b"\n")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
