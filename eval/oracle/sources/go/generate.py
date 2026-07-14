#!/usr/bin/env python3
"""Freeze and reverify exact upstream Go source-byte inventories."""

import hashlib
import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess
import sys
from urllib.parse import urlsplit


LANGUAGE = "go"
EXTENSION = ".go"
ADAPTER = "/opt/evergreen/bin/go-oracle-v1"
CONTROL = "/control/oracle-v1.json"
RECIPE = Path(__file__).with_name("extract-v1.json")
KINDS = ("return-value", "raises", "default-value", "cardinality", "state-change")
PATTERNS = {
    "return-value": re.compile(rb"func\s+value\s*\(\s*\)\s*int\s*\{\s*return 1\b"),
    "raises": re.compile(rb"if false\s*\{\s*panic\("),
    "default-value": re.compile(rb"defaultValue\(1\)"),
    "cardinality": re.compile(rb"items\s*:=\s*\[\]int\{1\}"),
    "state-change": re.compile(rb"state\s*=\s*!state"),
}
RECORD_KEYS = {
    "source_id", "language", "project", "lineage_id", "origin", "commit", "tree",
    "license", "extraction", "representative_source", "source_blobs", "source_inventory_sha256",
    "source_file_count", "operator_witness_counts", "operator_witness_inventory_sha256",
    "harness", "wrapper_recipe",
}
PROJECT_KEYS = {"source_id", "project", "lineage_id", "origin", "commit", "tree", "license"}
LICENSES = {
    "MIT": (b"Permission is hereby granted, free of charge",),
    "Apache-2.0": (b"Apache License", b"Version 2.0"),
    "BSD-2-Clause": (b"Redistributions of source code must retain",),
    "BSD-3-Clause": (b"Redistributions of source code must retain", b"Neither the name"),
    "ISC": (b"Permission to use, copy, modify, and/or distribute",),
}
WRAPPER_TEMPLATES = {
    "return-value": (
        "func value() int { return 1 }\n"
        "func main() { fmt.Println(value()) }\n"
    ),
    "raises": (
        "func main() {\n"
        "\tdeferred := false\n"
        "\tdefer func() { if recover() != nil { fmt.Println(\"ValueError\") }; _ = deferred }()\n"
        "\tif false { panic(\"ValueError\") }\n"
        "\tfmt.Println(\"no-error\")\n"
        "}\n"
    ),
    "default-value": (
        "func defaultValue(value int) int { return value }\n"
        "func main() { fmt.Printf(\"default:%d\\n\", defaultValue(1)) }\n"
    ),
    "cardinality": (
        "func main() { items := []int{1}; fmt.Printf(\"cardinality:%d\\n\", len(items)) }\n"
    ),
    "state-change": (
        "func main() { state := false; state = !state; if state { fmt.Println(\"state:changed\") "
        "} else { fmt.Println(\"state:unchanged\") } }\n"
    ),
}


def wrapper_recipe():
    recipe = {
        "id": "source-bound-wrapper-v1", "language": LANGUAGE,
        "templates": WRAPPER_TEMPLATES,
    }
    return {"id": recipe["id"], "sha256": sha256(canonical(recipe))}


def canonical(value):
    return json.dumps(value, allow_nan=False, ensure_ascii=False, sort_keys=True,
                      separators=(",", ":")).encode()


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def git(checkout, *arguments, maximum=64 * 1024 * 1024):
    completed = subprocess.run(
        ["git", "-C", str(checkout), *arguments], capture_output=True, timeout=30,
        check=False,
    )
    if completed.returncode or len(completed.stdout) > maximum:
        raise ValueError("upstream Git identity could not be verified")
    return completed.stdout


def safe_path(value):
    if type(value) is not str:
        raise ValueError("upstream path is invalid")
    path = PurePosixPath(value)
    if (path.is_absolute() or path.as_posix() != value or
            any(part in ("", ".", "..") or part.startswith(".") for part in path.parts)):
        raise ValueError("upstream path is invalid")
    return value


def tree_entries(checkout, commit):
    entries = []
    for raw in git(checkout, "ls-tree", "-rz", "--full-tree", commit).split(b"\0"):
        if not raw:
            continue
        metadata, raw_path = raw.split(b"\t", 1)
        mode, kind, object_id = metadata.decode("ascii").split(" ")
        try:
            path = safe_path(raw_path.decode("utf-8"))
        except (UnicodeError, ValueError):
            continue
        if mode in ("100644", "100755") and kind == "blob":
            entries.append((path, object_id))
    return sorted(entries)


def freeze_project(project, checkout):
    if not isinstance(project, dict) or set(project) != PROJECT_KEYS:
        raise ValueError("project fields are invalid")
    parsed = urlsplit(project["origin"])
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or
            parsed.query or parsed.fragment):
        raise ValueError("project origin is invalid")
    if git(checkout, "remote", "get-url", "origin").decode().strip() != project["origin"]:
        raise ValueError("upstream origin does not match")
    commit = git(checkout, "rev-parse", "--verify", "HEAD^{commit}", maximum=256).decode().strip()
    tree = git(checkout, "rev-parse", "--verify", "HEAD^{tree}", maximum=256).decode().strip()
    if commit != project["commit"] or tree != project["tree"]:
        raise ValueError("upstream commit or tree does not match")
    if git(checkout, "status", "--porcelain=v1", "-z", "--untracked-files=all"):
        raise ValueError("upstream checkout is dirty")

    entries = tree_entries(checkout, commit)
    by_path = dict(entries)
    license_spec = project["license"]
    if not isinstance(license_spec, dict) or set(license_spec) != {"spdx", "path"}:
        raise ValueError("license fields are invalid")
    license_path = safe_path(license_spec["path"])
    if license_spec["spdx"] not in LICENSES or license_path not in by_path:
        raise ValueError("license is not allowlisted or present")
    license_bytes = git(checkout, "cat-file", "blob", by_path[license_path], maximum=1024 * 1024)
    if not all(marker in license_bytes for marker in LICENSES[license_spec["spdx"]]):
        raise ValueError("license text does not match the declared SPDX family")

    inventory = []
    witnesses = []
    for path, object_id in entries:
        if not path.endswith(EXTENSION):
            continue
        content = git(checkout, "cat-file", "blob", object_id)
        inventory.append({
            "bytes": len(content), "git_blob_oid": object_id, "path": path,
            "sha256": sha256(content),
        })
        for kind, pattern in PATTERNS.items():
            for match in pattern.finditer(content):
                witnesses.append({
                    "kind": kind, "match_sha256": sha256(match.group()),
                    "offset": match.start(), "path": path, "source_sha256": sha256(content),
                })
    if not inventory:
        raise ValueError("upstream project contains no language source files")
    representative = inventory[0]
    counts = {kind: sum(item["kind"] == kind for item in witnesses) for kind in KINDS}
    recipe_raw = RECIPE.read_bytes()
    extraction = {
        "argv": ["git", "show", "--no-ext-diff", f"{commit}:{representative['path']}"],
        "recipe_path": f"{LANGUAGE}/extract-v1.json", "recipe_sha256": sha256(recipe_raw),
    }
    harness_unsigned = {
        "adapter_id": f"{LANGUAGE}-oracle-v1",
        "argv": [ADAPTER, f"/input/{representative['path']}", CONTROL],
    }
    return {
        "source_id": project["source_id"], "language": LANGUAGE,
        "project": project["project"], "lineage_id": project["lineage_id"],
        "origin": project["origin"], "commit": commit, "tree": tree,
        "license": {
            "spdx": license_spec["spdx"], "path": license_path,
            "git_blob_oid": by_path[license_path], "sha256": sha256(license_bytes),
            "bytes": len(license_bytes),
        },
        "extraction": extraction, "representative_source": representative,
        "source_blobs": inventory,
        "source_inventory_sha256": sha256(canonical(inventory)),
        "source_file_count": len(inventory), "operator_witness_counts": counts,
        "operator_witness_inventory_sha256": sha256(canonical(witnesses)),
        "harness": {**harness_unsigned, "sha256": sha256(canonical(harness_unsigned))},
        "wrapper_recipe": wrapper_recipe(),
    }


def verify_project(record, checkout):
    if not isinstance(record, dict) or set(record) != RECORD_KEYS:
        raise ValueError("source record fields are invalid")
    project = {key: record[key] for key in PROJECT_KEYS - {"license"}}
    project["license"] = {key: record["license"][key] for key in ("spdx", "path")}
    if freeze_project(project, checkout) != record:
        raise ValueError("source record does not match upstream bytes")
    return True


def catalog(records):
    ordered = sorted(records, key=lambda item: item["source_id"])
    return {
        "schema_version": 1, "kind": "evergreen-upstream-source-byte-catalog",
        "language": LANGUAGE, "records": ordered,
        "verified_projects": len(ordered),
        "operator_witness_counts": {
            kind: sum(record["operator_witness_counts"][kind] for record in ordered)
            for kind in KINDS
        },
    }


def validate_catalog(value):
    if (not isinstance(value, dict) or set(value) != {
            "schema_version", "kind", "language", "records", "verified_projects",
            "operator_witness_counts"} or value["schema_version"] != 1 or
            value["kind"] != "evergreen-upstream-source-byte-catalog" or
            value["language"] != LANGUAGE or not isinstance(value["records"], list) or
            any(not isinstance(record, dict) or set(record) != RECORD_KEYS
                for record in value["records"])):
        raise ValueError("source catalog is invalid")
    if value != catalog(value["records"]):
        raise ValueError("source catalog aggregates are invalid")
    return True


def provenance_record(_record):
    raise ValueError("a runnable adapter receipt is required before witnesses become seed claims")


def derive_wrapper(record, path, source_bytes, oracle_kind):
    if oracle_kind not in KINDS or type(source_bytes) is not bytes:
        raise ValueError("wrapper request is invalid")
    if record.get("wrapper_recipe") != wrapper_recipe():
        raise ValueError("wrapper recipe identity is invalid")
    members = [item for item in record.get("source_blobs", []) if item.get("path") == path]
    digest = sha256(source_bytes)
    if len(members) != 1 or members[0].get("sha256") != digest or \
            members[0].get("bytes") != len(source_bytes):
        raise ValueError("wrapper source is not an exact catalog member")
    code = (
        "package main\n\nimport \"fmt\"\n\n"
        f"const sourceSHA256 = \"{digest}\"\n\n"
        + WRAPPER_TEMPLATES[oracle_kind]
        + "var _ = sourceSHA256\n"
    )
    wrapper_path = f"evergreen_{digest[:16]}_{oracle_kind.replace('-', '_')}.go"
    documentation = {
        "return-value": "Calling value() returns 1.",
        "raises": "Running the guarded operation prints no-error.",
        "default-value": "Calling defaultValue(1) produces default 1.",
        "cardinality": "The items collection has cardinality 1.",
        "state-change": "The state transition changes state.",
    }[oracle_kind]
    return {
        "schema_version": 1, "kind": "evergreen-source-bound-wrapper-receipt",
        "language": LANGUAGE, "project": record["project"], "origin": record["origin"],
        "commit": record["commit"], "tree": record["tree"], "oracle_kind": oracle_kind,
        "upstream_span": {
            "path": path, "git_blob_oid": members[0]["git_blob_oid"], "offset": 0,
            "bytes": len(source_bytes), "sha256": digest,
        },
        "generator": wrapper_recipe(),
        "wrapper": {"path": wrapper_path, "code": code, "sha256": sha256(code.encode())},
        "documentation": {
            "template": documentation, "sha256": sha256(documentation.encode()),
        },
    }


def freeze_catalog(pins, cache):
    if (not isinstance(pins, dict) or set(pins) != {
            "schema_version", "kind", "language", "projects"} or
            pins["schema_version"] != 1 or
            pins["kind"] != "evergreen-upstream-project-pins" or
            pins["language"] != LANGUAGE or not isinstance(pins["projects"], list)):
        raise ValueError("project pin catalog is invalid")
    records = []
    seen = set()
    for project in pins["projects"]:
        source_id = project.get("source_id") if isinstance(project, dict) else None
        if (type(source_id) is not str or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", source_id)
                or source_id in seen):
            raise ValueError("project source ID is invalid or duplicated")
        seen.add(source_id)
        checkout = Path(cache) / source_id
        if not checkout.is_dir():
            raise ValueError("cached checkout is missing")
        records.append(freeze_project(project, checkout))
    return catalog(records)


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projects", required=True)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args(argv)
    try:
        pins = json.loads(Path(arguments.projects).read_bytes(), object_pairs_hook=unique_object)
        frozen = freeze_catalog(pins, arguments.cache)
        Path(arguments.output).write_bytes(canonical(frozen) + b"\n")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        parser.exit(2, f"source catalog generation failed: {error}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main())
