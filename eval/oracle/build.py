"""Deterministic, private oracle package construction."""

from dataclasses import dataclass
import argparse
import hashlib
import hmac
import json
import os
from pathlib import Path
import shutil
import stat
import tempfile
import sys

from .oracle import ORACLE_KINDS, run_seed
from .split import (
    LANGUAGES, REFERENCE_CATEGORIES, SimilarityError, assign_split, load_similarity_policy,
    validate_split_isolation,
)
from eval.bench.artifact import read_bytes


REPOSITORY_ROOT = Path(__file__).resolve().parents[2]


MINIMUM_PER_CLASS = 100
MINIMUM_REPOSITORIES = 10
MAXIMUM_CLASS_RATIO = 2.0
MAXIMUM_REPOSITORY_SHARE = 0.20
MINIMUM_KIND_INCONSISTENT = 20
MINIMUM_KIND_CONSISTENT = 40
MINIMUM_SOURCE_GROUPS = 20
MINIMUM_SOURCE_GROUPS_PER_SPLIT = 10


class PackageError(ValueError):
    """An oracle package is incomplete, unsafe, or non-reproducible."""


@dataclass(frozen=True)
class PackageLimits:
    minimum_per_class: int = MINIMUM_PER_CLASS
    minimum_repositories: int = MINIMUM_REPOSITORIES
    maximum_class_ratio: float = MAXIMUM_CLASS_RATIO
    maximum_repository_share: float = MAXIMUM_REPOSITORY_SHARE
    minimum_kind_inconsistent: int = MINIMUM_KIND_INCONSISTENT
    minimum_kind_consistent: int = MINIMUM_KIND_CONSISTENT

    def __post_init__(self):
        if (type(self.minimum_per_class) is not int or self.minimum_per_class < 1 or
                type(self.minimum_repositories) is not int or self.minimum_repositories < 1 or
                type(self.maximum_class_ratio) not in (int, float) or
                self.maximum_class_ratio < 1 or
                type(self.maximum_repository_share) not in (int, float) or
                not 0 < self.maximum_repository_share <= 1 or
                type(self.minimum_kind_inconsistent) is not int or
                self.minimum_kind_inconsistent < 1 or
                type(self.minimum_kind_consistent) is not int or
                self.minimum_kind_consistent < 1):
            raise PackageError("package limits are invalid")


DEFAULT_LIMITS = PackageLimits()


@dataclass(frozen=True)
class SourceGroupLimits:
    minimum_per_language: int = MINIMUM_SOURCE_GROUPS
    minimum_per_split: int = MINIMUM_SOURCE_GROUPS_PER_SPLIT

    def __post_init__(self):
        if (type(self.minimum_per_language) is not int or self.minimum_per_language < 1 or
                type(self.minimum_per_split) is not int or self.minimum_per_split < 1 or
                self.minimum_per_language < 2 * self.minimum_per_split):
            raise PackageError("source group limits are invalid")


DEFAULT_SOURCE_GROUP_LIMITS = SourceGroupLimits()


def _canonical(value):
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"),
        ).encode()
    except (TypeError, ValueError, RecursionError):
        raise PackageError("oracle package data is not canonical JSON") from None


def _load_json(path, label):
    try:
        raw = Path(path).read_bytes()
        if len(raw) > 64 * 1024 * 1024:
            raise PackageError(f"{label} exceeds the byte limit")
        return json.loads(raw)
    except (OSError, UnicodeError, json.JSONDecodeError):
        raise PackageError(f"{label} is unavailable or invalid") from None


def _manifest(path, policy_hash):
    document = _load_json(path, "oracle source manifest")
    if (not isinstance(document, dict) or set(document) != {
            "schema_version", "similarity_policy_sha256", "seeds"} or
            type(document["schema_version"]) is not int or document["schema_version"] != 1 or
            document["similarity_policy_sha256"] != policy_hash or
            not isinstance(document["seeds"], list) or not document["seeds"]):
        raise PackageError("oracle source manifest fields or policy hash are invalid")
    entries = []
    identities = set()
    projects = {}
    for entry in document["seeds"]:
        if not isinstance(entry, dict) or set(entry) != {"lineage_id", "seed"}:
            raise PackageError("oracle source manifest seed entry is invalid")
        lineage = entry["lineage_id"]
        seed = entry["seed"]
        if type(lineage) is not str or not lineage.strip() or not isinstance(seed, dict):
            raise PackageError("oracle source manifest lineage is missing")
        identity = seed.get("seed_sha256")
        project = seed.get("project")
        if (type(identity) is not str or len(identity) != 64 or identity in identities or
                type(project) is not str or not project):
            raise PackageError("oracle source manifest seed identity is invalid or duplicated")
        identities.add(identity)
        previous = projects.setdefault(project, lineage)
        if previous != lineage:
            raise PackageError("project declares inconsistent lineage identities")
        entries.append((identity, lineage, seed))
    return sorted(entries, key=lambda item: item[0])


def validate_package_rows(
    rows, *, languages=LANGUAGES, limits=DEFAULT_LIMITS, oracle_kinds=ORACLE_KINDS,
):
    """Enforce post-split language, class, repository, balance, and share gates."""
    if not isinstance(rows, list) or not rows:
        raise PackageError("package is below the language minimum")
    seen = set()
    for row in rows:
        if not isinstance(row, dict) or type(row.get("id")) is not str or not row["id"]:
            raise PackageError("package row is invalid")
        if row.get("label") not in ("consistent", "inconsistent"):
            raise PackageError("package row label is invalid")
        if row.get("language") not in languages:
            raise PackageError("package contains an unknown language")
        if row.get("oracle_kind") not in oracle_kinds:
            raise PackageError("package row oracle kind is invalid")
        if type(row.get("lineage_id")) is not str or not row["lineage_id"].strip():
            raise PackageError("package repository group identity is invalid")
        if row["id"] in seen:
            raise PackageError("package contains a duplicate id")
        seen.add(row["id"])
    for language in languages:
        selected = [row for row in rows if row.get("language") == language]
        if not selected:
            raise PackageError("package is below the language minimum")
        counts = {
            label: sum(row.get("label") == label for row in selected)
            for label in ("consistent", "inconsistent")
        }
        if min(counts.values()) < limits.minimum_per_class:
            raise PackageError("package is below a class minimum")
        if max(counts.values()) / min(counts.values()) > limits.maximum_class_ratio:
            raise PackageError("package class imbalance exceeds the maximum")
        for oracle_kind in oracle_kinds:
            cell = [row for row in selected if row["oracle_kind"] == oracle_kind]
            inconsistent = sum(row["label"] == "inconsistent" for row in cell)
            consistent = sum(row["label"] == "consistent" for row in cell)
            if (inconsistent < limits.minimum_kind_inconsistent or
                    consistent < limits.minimum_kind_consistent):
                raise PackageError("package is below a language by oracle kind cell minimum")
        repository_groups = {}
        for row in selected:
            project = row.get("project")
            if type(project) is not str or not project:
                raise PackageError("package project identity is invalid")
            lineage = row["lineage_id"]
            repository_groups[lineage] = repository_groups.get(lineage, 0) + 1
        if len(repository_groups) < limits.minimum_repositories:
            raise PackageError("package is below the repository minimum")
        if max(repository_groups.values()) / len(selected) > limits.maximum_repository_share:
            raise PackageError("package repository share exceeds the maximum")
    return True


def _validate_source_group_splits(
    entries, split_key, *, languages=LANGUAGES, limits=DEFAULT_SOURCE_GROUP_LIMITS,
):
    """Reject an unusable keyed split using source-manifest metadata alone."""
    for language in languages:
        lineages = {
            lineage for _identity, lineage, seed in entries if seed.get("language") == language
        }
        if len(lineages) < limits.minimum_per_language:
            raise PackageError(
                f"source manifest needs {limits.minimum_per_language} repository groups per language"
            )
        split_counts = {
            split: sum(assign_split(split_key, lineage) == split for lineage in lineages)
            for split in ("dev", "holdout")
        }
        if min(split_counts.values()) < limits.minimum_per_split:
            raise PackageError(
                f"source manifest needs {limits.minimum_per_split} repository groups in each split"
            )
    return True


def _public_id(key, seed_hash, variant):
    payload = b"evergreen-oracle-public-id-v1\0" + seed_hash.encode() + b"\0" + variant.encode()
    return "oracle-" + hmac.new(key, payload, hashlib.sha256).hexdigest()


def _package_bytes(rows):
    return b"".join(_canonical(row) + b"\n" for row in sorted(rows, key=lambda row: row["id"]))


def _exclusive_private_file(path, raw):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags, 0o600)
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
    except OSError:
        raise PackageError("private package exclusive creation failed") from None


def _atomic_public(path, raw):
    path = Path(path)
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
        os.chmod(temporary, 0o644)
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except OSError:
        try:
            os.unlink(temporary)
        except (FileNotFoundError, UnboundLocalError):
            pass
        raise PackageError("public split manifest atomic write failed") from None


def _build_packages(
    manifest_path, private_directory, public_manifest, split_key, approved_images, *,
    references=None, derive_seed=run_seed, limits=DEFAULT_LIMITS, languages=LANGUAGES,
    source_group_limits=DEFAULT_SOURCE_GROUP_LIMITS, oracle_kinds=ORACLE_KINDS,
):
    """Derive, isolate, validate, and exclusively write private split packages."""
    private_directory = Path(private_directory)
    if not private_directory.is_absolute():
        raise PackageError("private destination must be absolute")
    resolved_private = private_directory.resolve()
    if resolved_private == REPOSITORY_ROOT or REPOSITORY_ROOT in resolved_private.parents:
        raise PackageError("private destination must be outside the repository")
    resolved_public = Path(public_manifest).resolve()
    if resolved_public == resolved_private or resolved_private in resolved_public.parents:
        raise PackageError("public split manifest must be outside the private destination")
    if not isinstance(references, list) or not references:
        raise PackageError("a complete reference corpus is required")
    if ({item.get("category") for item in references if isinstance(item, dict)} !=
            set(REFERENCE_CATEGORIES)):
        raise PackageError("a complete reference corpus is required")
    try:
        os.lstat(private_directory)
    except FileNotFoundError:
        pass
    except OSError:
        raise PackageError("private destination could not be inspected") from None
    else:
        raise PackageError("private destination must be exclusively created")
    _policy, policy_hash = load_similarity_policy()
    entries = _manifest(manifest_path, policy_hash)
    if type(split_key) is not bytes or len(split_key) < 16:
        raise PackageError("split key must contain at least 16 bytes")
    _validate_source_group_splits(
        entries, split_key, languages=languages, limits=source_group_limits,
    )
    rows = []
    for seed_hash, lineage, seed in entries:
        split = assign_split(split_key, lineage)
        try:
            derived = derive_seed(seed, approved_images=approved_images)
        except (TypeError, ValueError) as error:
            raise PackageError("oracle seed derivation failed") from error
        if not isinstance(derived, (tuple, list)) or not derived:
            raise PackageError("oracle seed produced no derived rows")
        for row in derived:
            if not isinstance(row, dict) or row.get("seed_sha256") != seed_hash:
                raise PackageError("derived row is not bound to its seed")
            variant = row.get("variant")
            if type(variant) is not str or not variant:
                raise PackageError("derived row variant is invalid")
            rows.append({
                **row,
                "source_id": row.get("id"),
                "id": _public_id(split_key, seed_hash, variant),
                "lineage_id": lineage,
                "split": split,
                "similarity_policy_sha256": policy_hash,
            })
    try:
        validate_split_isolation(rows, list(references or []))
    except SimilarityError as error:
        raise PackageError(str(error)) from error
    packages = {}
    for split in ("dev", "holdout"):
        selected = [row for row in rows if row["split"] == split]
        validate_package_rows(
            selected, languages=languages, limits=limits, oracle_kinds=oracle_kinds,
        )
        raw = _package_bytes(selected)
        packages[split] = {
            "raw": raw,
            "package_sha256": hashlib.sha256(raw).hexdigest(),
            "policy_sha256": policy_hash,
            "rows": len(selected),
        }

    try:
        private_directory.mkdir(mode=0o700)
    except FileExistsError:
        raise PackageError("private destination must be exclusively created") from None
    except OSError:
        raise PackageError("private destination could not be created") from None
    try:
        for split in ("dev", "holdout"):
            _exclusive_private_file(private_directory / f"{split}.jsonl", packages[split]["raw"])
        directory = os.open(private_directory, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        shutil.rmtree(private_directory, ignore_errors=True)
        raise

    declarations = [{
        "sha256": packages[split]["package_sha256"], "split": split,
        "rows": packages[split]["rows"],
    } for split in ("dev", "holdout")]
    public_rows = [{
        "id": row["id"], "dataset_sha256": packages[row["split"]]["package_sha256"],
        "split": row["split"],
    } for row in sorted(rows, key=lambda item: item["id"])]
    document = {
        "schema_version": 2,
        "similarity_policy_sha256": policy_hash,
        "datasets": declarations,
        "rows": public_rows,
    }
    public_manifest = Path(public_manifest)
    try:
        _atomic_public(public_manifest, _canonical(document) + b"\n")
    except Exception:
        try:
            shutil.rmtree(private_directory)
        except OSError:
            raise PackageError("failed private package cleanup after public write failure") from None
        raise
    return packages["dev"], packages["holdout"], public_manifest


def build_packages(
    manifest_path, private_directory, public_manifest, split_key, approved_images, *, references,
):
    """Build production packages with the frozen oracle and five-language gates."""
    return _build_packages(
        manifest_path, private_directory, public_manifest, split_key, approved_images,
        references=references, derive_seed=run_seed, limits=DEFAULT_LIMITS, languages=LANGUAGES,
        source_group_limits=DEFAULT_SOURCE_GROUP_LIMITS, oracle_kinds=ORACLE_KINDS,
    )


def _read_private_rows(path, maximum=64 * 1024 * 1024):
    try:
        before = os.lstat(path)
        if (not stat.S_ISREG(before.st_mode) or stat.S_IMODE(before.st_mode) & 0o077 or
                hasattr(os, "getuid") and before.st_uid != os.getuid()):
            raise PackageError("private package is not an owner-only regular file")
        raw = read_bytes(path, maximum, label="private oracle package")
        after = os.lstat(path)
        if (after.st_dev, after.st_ino, after.st_mode) != (
                before.st_dev, before.st_ino, before.st_mode):
            raise PackageError("private package changed during validation")
        rows = [json.loads(line) for line in raw.splitlines() if line]
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        raise PackageError("private package is unavailable or invalid") from None
    return raw, rows


def load_development_rows(public_manifest, development_package):
    """Load only a caller-supplied development package; no holdout path is accepted."""
    from eval.bench.split_manifest import load_split_assignments

    before = read_bytes(public_manifest, 64 * 1024 * 1024, label="public split manifest")
    assignments = load_split_assignments(Path(public_manifest))
    after = read_bytes(public_manifest, 64 * 1024 * 1024, label="public split manifest")
    if before != after:
        raise PackageError("public split manifest changed during development selection")
    try:
        document = json.loads(before)
    except (UnicodeError, json.JSONDecodeError):
        raise PackageError("public split manifest is invalid") from None
    if (not isinstance(document, dict) or document.get("schema_version") != 2 or
            set(document) != {"schema_version", "similarity_policy_sha256", "datasets", "rows"}):
        raise PackageError("public split manifest is invalid")
    _policy, policy_hash = load_similarity_policy()
    if document["similarity_policy_sha256"] != policy_hash:
        raise PackageError("public split manifest policy drifted")
    declarations = [item for item in document["datasets"]
                    if isinstance(item, dict) and item.get("split") == "dev"]
    if len(declarations) != 1:
        raise PackageError("public split manifest development declaration is invalid")
    raw, rows = _read_private_rows(development_package)
    if hashlib.sha256(raw).hexdigest() != declarations[0].get("sha256"):
        raise PackageError("development package hash does not match the public manifest")
    expected_ids = {row_id for row_id, split in assignments.items() if split == "dev"}
    if (len(assignments) != len(document["rows"]) or
            {row.get("id") for row in rows} != expected_ids or
            any(row.get("split") != "dev" or
                row.get("similarity_policy_sha256") != policy_hash
                for row in rows)):
        raise PackageError("development package contains non-development rows")
    return rows


def main(argv=None):
    parser = argparse.ArgumentParser(description="Build or select locked oracle packages")
    commands = parser.add_subparsers(dest="command", required=True)
    development = commands.add_parser(
        "development", help="open one hash-bound development package"
    )
    development.add_argument("--manifest", required=True)
    development.add_argument("--package", required=True)
    arguments = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if arguments.command == "development":
        rows = load_development_rows(Path(arguments.manifest), Path(arguments.package))
        for row in sorted(rows, key=lambda item: item["id"]):
            sys.stdout.buffer.write(_canonical(row) + b"\n")
        return 0
    raise PackageError("unknown oracle package command")


if __name__ == "__main__":
    raise SystemExit(main())
