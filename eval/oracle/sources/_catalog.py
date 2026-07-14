#!/usr/bin/env python3
"""Shared verifier for exact upstream source-byte inventories."""

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import subprocess
from types import MappingProxyType
from urllib.parse import urlsplit


RECORD_KEYS = frozenset({
    "source_id",
    "language",
    "project",
    "lineage_id",
    "origin",
    "commit",
    "tree",
    "license",
    "extraction",
    "representative_source",
    "source_blobs",
    "source_inventory_sha256",
    "source_file_count",
})
PROJECT_KEYS = frozenset({
    "source_id", "project", "lineage_id", "origin", "commit", "tree", "license",
})
LICENSES = MappingProxyType({
    "MIT": (b"Permission is hereby granted, free of charge",),
    "Apache-2.0": (b"Apache License", b"Version 2.0"),
    "BSD-2-Clause": (b"Redistributions of source code must retain",),
    "BSD-3-Clause": (
        b"Redistributions of source code must retain",
        b"Neither the name",
    ),
    "ISC": (b"Permission to use, copy, modify, and/or distribute",),
})


@dataclass(frozen=True)
class CatalogConfig:
    """Immutable language-specific inventory settings."""

    language: str
    extension: str
    recipe: Path

    def __post_init__(self):
        if (
            not re.fullmatch(r"[a-z][a-z0-9-]{0,31}", self.language)
            or not re.fullmatch(r"\.[A-Za-z0-9][A-Za-z0-9._-]{0,15}", self.extension)
            or not isinstance(self.recipe, Path)
        ):
            raise ValueError("catalog configuration is invalid")


def canonical(value):
    return json.dumps(
        value,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


def sha256(value):
    return hashlib.sha256(value).hexdigest()


def git(checkout, *arguments, maximum=64 * 1024 * 1024):
    environment = os.environ.copy()
    environment.update({
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
    })
    try:
        completed = subprocess.run(
            ["git", "--no-replace-objects", "-C", str(checkout), *arguments],
            capture_output=True,
            stdin=subprocess.DEVNULL,
            timeout=30,
            check=False,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        raise ValueError("upstream Git identity could not be verified") from None
    if (
        completed.returncode
        or len(completed.stdout) > maximum
        or len(completed.stderr) > 64 * 1024
    ):
        raise ValueError("upstream Git identity could not be verified")
    return completed.stdout


def safe_path(value):
    if type(value) is not str:
        raise ValueError("upstream path is invalid")
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or path.as_posix() != value
        or "\\" in value
        or "\0" in value
        or any(part in ("", ".", "..") for part in path.parts)
    ):
        raise ValueError("upstream path is invalid")
    return value


def tree_entries(checkout, commit):
    entries = []
    for raw in git(checkout, "ls-tree", "-rz", "--full-tree", commit).split(b"\0"):
        if not raw:
            continue
        try:
            metadata, raw_path = raw.split(b"\t", 1)
            mode, kind, object_id = metadata.decode("ascii").split(" ")
            path = safe_path(raw_path.decode("utf-8"))
        except (UnicodeError, ValueError):
            raise ValueError("upstream Git tree entry is invalid") from None
        if mode in ("100644", "100755") and kind == "blob":
            entries.append((path, object_id))
    return sorted(entries)


def freeze_project(config, project, checkout):
    if not isinstance(project, dict) or set(project) != PROJECT_KEYS:
        raise ValueError("project fields are invalid")
    parsed = urlsplit(project["origin"])
    if (
        parsed.scheme != "https"
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("project origin is invalid")
    if git(checkout, "remote", "get-url", "origin").decode().strip() != project["origin"]:
        raise ValueError("upstream origin does not match")
    commit = git(
        checkout, "rev-parse", "--verify", "HEAD^{commit}", maximum=256,
    ).decode().strip()
    tree = git(
        checkout, "rev-parse", "--verify", "HEAD^{tree}", maximum=256,
    ).decode().strip()
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
    license_bytes = git(
        checkout,
        "cat-file",
        "blob",
        by_path[license_path],
        maximum=1024 * 1024,
    )
    if not all(marker in license_bytes for marker in LICENSES[license_spec["spdx"]]):
        raise ValueError("license text does not match the declared SPDX family")

    inventory = []
    for path, object_id in entries:
        if not path.endswith(config.extension):
            continue
        content = git(checkout, "cat-file", "blob", object_id)
        inventory.append({
            "bytes": len(content),
            "git_blob_oid": object_id,
            "path": path,
            "sha256": sha256(content),
        })
    if not inventory:
        raise ValueError("upstream project contains no language source files")
    representative = inventory[0]
    recipe_raw = config.recipe.read_bytes()
    return {
        "source_id": project["source_id"],
        "language": config.language,
        "project": project["project"],
        "lineage_id": project["lineage_id"],
        "origin": project["origin"],
        "commit": commit,
        "tree": tree,
        "license": {
            "spdx": license_spec["spdx"],
            "path": license_path,
            "git_blob_oid": by_path[license_path],
            "sha256": sha256(license_bytes),
            "bytes": len(license_bytes),
        },
        "extraction": {
            "argv": [
                "git",
                "show",
                "--no-ext-diff",
                f"{commit}:{representative['path']}",
            ],
            "recipe_path": f"{config.language}/extract-v1.json",
            "recipe_sha256": sha256(recipe_raw),
        },
        "representative_source": representative,
        "source_blobs": inventory,
        "source_inventory_sha256": sha256(canonical(inventory)),
        "source_file_count": len(inventory),
    }


def verify_project(config, record, checkout):
    if not isinstance(record, dict) or set(record) != RECORD_KEYS:
        raise ValueError("source record fields are invalid")
    project = {key: record[key] for key in PROJECT_KEYS - {"license"}}
    project["license"] = {
        key: record["license"][key] for key in ("spdx", "path")
    }
    if freeze_project(config, project, checkout) != record:
        raise ValueError("source record does not match upstream bytes")
    return True


def catalog(config, records):
    ordered = sorted(records, key=lambda item: item["source_id"])
    return {
        "schema_version": 1,
        "kind": "evergreen-upstream-source-byte-catalog",
        "language": config.language,
        "records": ordered,
        "verified_projects": len(ordered),
    }


def validate_catalog(config, value):
    if (
        not isinstance(value, dict)
        or set(value) != {
            "schema_version", "kind", "language", "records", "verified_projects",
        }
        or value["schema_version"] != 1
        or value["kind"] != "evergreen-upstream-source-byte-catalog"
        or value["language"] != config.language
        or not isinstance(value["records"], list)
        or any(
            not isinstance(record, dict) or set(record) != RECORD_KEYS
            for record in value["records"]
        )
    ):
        raise ValueError("source catalog is invalid")
    if value != catalog(config, value["records"]):
        raise ValueError("source catalog aggregates are invalid")
    return True


def provenance_record(_record):
    raise ValueError("a runnable adapter receipt is required before source bytes become seed claims")


def freeze_catalog(config, pins, cache):
    if (
        not isinstance(pins, dict)
        or set(pins) != {"schema_version", "kind", "language", "projects"}
        or pins["schema_version"] != 1
        or pins["kind"] != "evergreen-upstream-project-pins"
        or pins["language"] != config.language
        or not isinstance(pins["projects"], list)
    ):
        raise ValueError("project pin catalog is invalid")
    records = []
    seen = set()
    for project in pins["projects"]:
        source_id = project.get("source_id") if isinstance(project, dict) else None
        if (
            type(source_id) is not str
            or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", source_id)
            or source_id in seen
        ):
            raise ValueError("project source ID is invalid or duplicated")
        seen.add(source_id)
        checkout = Path(cache) / source_id
        if not checkout.is_dir() or checkout.is_symlink():
            raise ValueError("cached checkout is missing")
        records.append(freeze_project(config, project, checkout))
    return catalog(config, records)


def unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def main(config, argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--projects", required=True)
    parser.add_argument("--cache", required=True)
    parser.add_argument("--output", required=True)
    arguments = parser.parse_args(argv)
    try:
        pins = json.loads(
            Path(arguments.projects).read_bytes(),
            object_pairs_hook=unique_object,
        )
        frozen = freeze_catalog(config, pins, arguments.cache)
        Path(arguments.output).write_bytes(canonical(frozen) + b"\n")
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError) as error:
        parser.exit(2, f"source catalog generation failed: {error}\n")
    return 0
