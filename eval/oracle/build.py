"""Deterministic, private oracle package construction."""

from dataclasses import dataclass
import argparse
import fnmatch
import hashlib
import hmac
import json
import math
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import stat
import subprocess
import sys
from urllib.parse import urlsplit

from .oracle import LANGUAGE_ADAPTERS, MUTATION_OPERATORS, ORACLE_KINDS, run_seed
from .split import (
    LANGUAGES, REFERENCE_CATEGORIES, SimilarityError, assign_split, load_similarity_policy,
    validate_split_isolation,
)


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
                not math.isfinite(self.maximum_class_ratio) or self.maximum_class_ratio < 1 or
                type(self.maximum_repository_share) not in (int, float) or
                not math.isfinite(self.maximum_repository_share) or
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
SOURCE_PACK_ID = "evergreen-executable-oracle-v1"
SOURCE_ROOT = Path(__file__).with_name("sources")
PROVENANCE_SCHEMA_PATH = SOURCE_ROOT / "provenance-schema-v1.json"
RECIPE_SCHEMA_PATH = SOURCE_ROOT / "recipe-schema-v1.json"
PRIVATE_CUSTODY_SCHEMA_PATH = SOURCE_ROOT / "private-custody-schema-v1.json"
PROVENANCE_REQUIREMENTS = {
    "languages": list(LANGUAGES),
    "minimum_projects_per_language": MINIMUM_SOURCE_GROUPS,
    "minimum_seed_claims_per_language": 250,
    "minimum_projects_per_language_kind": MINIMUM_SOURCE_GROUPS,
    "minimum_seed_claims_per_language_kind": 40,
    "oracle_kinds": list(ORACLE_KINDS),
}
PUBLIC_PROVENANCE_FORBIDDEN_KEYS = {
    "code", "documentation", "label", "verdict", "mutation", "observable", "split",
    "split_key", "private_path", "holdout_path", "development_path",
}
PUBLIC_SOURCE_LICENSES = {
    "0BSD", "Apache-2.0", "BSD-2-Clause", "BSD-3-Clause", "ISC", "MIT", "Python-2.0",
}
MAX_REFERENCE_INVENTORY_BYTES = 16 * 1024 * 1024
MAX_REFERENCE_CONTENT_BYTES = 1024 * 1024
MAX_PACKAGE_ROWS = 100_000
MAX_SOURCE_SEEDS = 100_000
REFERENCE_POLICY_PATH = Path(__file__).with_name("reference-inventory-policy-v1.json")
REFERENCE_POLICY_SHA256 = "d6110358004d7a951e12b287e62731e06e4f82e7b00cea35d8771a164190b874"
_REFERENCE_POLICY = {
    "schema_version": 1,
    "categories": {
        "prompt": ["eval/prompt.md", "skills/evergreen/SKILL.md", "commands/*.md"],
        "example": ["examples/**"],
        "test": ["tests/*.py", "tests/*.sh"],
        "fixture": ["eval/fixture/**"],
        "prior-corpus": [
            "eval/bench/*.jsonl", "eval/bench/**/*.jsonl",
            "eval/bench/*.votes.json", "eval/bench/**/*.votes.json",
            "eval/bench/human-audit/**", "eval/bench/public/**",
        ],
    },
    "exclude_names": ["__pycache__", ".DS_Store"],
}


def _canonical(value):
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, sort_keys=True, separators=(",", ":"),
        ).encode()
    except (TypeError, ValueError, RecursionError):
        raise PackageError("oracle package data is not canonical JSON") from None


def _load_json(path, label):
    try:
        raw = _read_path_nofollow(path, 64 * 1024 * 1024, label)
        return _loads_strict(raw)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        raise PackageError(f"{label} is unavailable or invalid") from None


def _unique_object(pairs):
    value = {}
    for key, item in pairs:
        if key in value:
            raise ValueError("duplicate JSON key")
        value[key] = item
    return value


def _loads_strict(raw):
    return json.loads(
        raw, parse_constant=lambda item: (_ for _ in ()).throw(ValueError(item)),
        object_pairs_hook=_unique_object,
    )


def _sha256_file(path, label):
    return hashlib.sha256(_read_path_nofollow(path, 16 * 1024 * 1024, label)).hexdigest()


def _forbid_public_answers(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in PUBLIC_PROVENANCE_FORBIDDEN_KEYS:
                raise PackageError("public provenance contains private oracle material")
            _forbid_public_answers(child)
    elif isinstance(value, list):
        for child in value:
            _forbid_public_answers(child)


def _public_source_path(value):
    return (type(value) is str and re.fullmatch(
        r"[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*"
        r"(?:/[A-Za-z0-9_-]+(?:\.[A-Za-z0-9_-]+)*)*", value,
    ) is not None)


def _public_recipe(manifest_path, language, extraction):
    if not isinstance(extraction, dict) or set(extraction) != {
            "recipe_path", "recipe_sha256", "argv"}:
        raise PackageError("oracle source provenance extraction recipe is invalid")
    relative = extraction["recipe_path"]
    if type(relative) is not str:
        raise PackageError("oracle source provenance extraction recipe is invalid")
    pure = PurePosixPath(relative)
    if (pure.is_absolute() or pure.as_posix() != relative or len(pure.parts) != 2 or
            pure.parts[0] != language or pure.suffix != ".json" or
            any(part in ("", ".", "..") or part.startswith(".") for part in pure.parts)):
        raise PackageError("oracle source provenance extraction recipe path is invalid")
    recipe_path = Path(manifest_path).absolute().parent / relative
    raw = _read_path_nofollow(recipe_path, 1024 * 1024, "public extraction recipe")
    if (type(extraction["recipe_sha256"]) is not str or
            hashlib.sha256(raw).hexdigest() != extraction["recipe_sha256"]):
        raise PackageError("oracle source provenance extraction recipe hash is invalid")
    try:
        recipe = _loads_strict(raw)
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise PackageError("public extraction recipe is invalid") from None
    expected_recipe = {
        "schema_version": 1,
        "kind": "evergreen-public-extraction-recipe",
        "language": language,
        "steps": [{"argv": ["git", "show", "--no-ext-diff", "COMMIT:PATH"]}],
    }
    if recipe != expected_recipe:
        raise PackageError("public extraction recipe is invalid")


def _validate_public_sources(manifest_path, sources, toolchains):
    source_keys = {
        "source_id", "language", "project", "lineage_id", "origin", "commit", "tree",
        "license", "extraction", "harness", "toolchain_id", "sandbox_image",
        "extracted_tree_sha256", "seed_claims", "oracle_kind_counts",
        "source_identity_sha256",
    }
    toolchain_by_language = {item["language"]: item for item in toolchains}
    seen_ids = set()
    seen_projects = set()
    for source in sources:
        if not isinstance(source, dict) or set(source) != source_keys:
            raise PackageError("oracle source provenance source fields are invalid")
        language = source["language"]
        parsed = urlsplit(source["origin"]) if type(source["origin"]) is str else None
        if (language not in LANGUAGES or type(source["source_id"]) is not str or
                not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", source["source_id"]) or
                source["source_id"] in seen_ids or type(source["project"]) is not str or
                not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", source["project"]) or
                (language, source["project"]) in seen_projects or
                type(source["lineage_id"]) is not str or
                not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", source["lineage_id"]) or
                parsed is None or parsed.scheme != "https" or not parsed.hostname or
                parsed.username is not None or parsed.password is not None or
                parsed.query or parsed.fragment or
                type(source["commit"]) is not str or
                not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", source["commit"]) or
                type(source["tree"]) is not str or
                not re.fullmatch(r"(?:[0-9a-f]{40}|[0-9a-f]{64})", source["tree"])):
            raise PackageError("oracle source provenance source identity is invalid")
        seen_ids.add(source["source_id"])
        seen_projects.add((language, source["project"]))

        license_record = source["license"]
        if (not isinstance(license_record, dict) or set(license_record) != {
                "spdx", "path", "sha256"} or
                license_record["spdx"] not in PUBLIC_SOURCE_LICENSES or
                not _public_source_path(license_record["path"]) or
                type(license_record["sha256"]) is not str or
                not re.fullmatch(r"[0-9a-f]{64}", license_record["sha256"])):
            raise PackageError("oracle source provenance license identity is invalid")
        _public_recipe(manifest_path, language, source["extraction"])
        extraction_argv = source["extraction"]["argv"]
        if (not isinstance(extraction_argv, list) or len(extraction_argv) != 4 or
                extraction_argv[:3] != ["git", "show", "--no-ext-diff"] or
                type(extraction_argv[3]) is not str or
                not extraction_argv[3].startswith(source["commit"] + ":") or
                not _public_source_path(extraction_argv[3].partition(":")[2]) or
                any(any(token in argument for token in ("\0", "\n", "\r"))
                    for argument in extraction_argv)):
            raise PackageError("oracle source provenance extraction argv is invalid")

        harness = source["harness"]
        if not isinstance(harness, dict) or set(harness) != {"adapter_id", "argv", "sha256"}:
            raise PackageError("oracle source provenance harness is invalid")
        unsigned_harness = {key: value for key, value in harness.items() if key != "sha256"}
        argv = harness["argv"]
        if (harness["adapter_id"] != f"{language}-oracle-v1" or
                not isinstance(argv, list) or len(argv) != 3 or
                argv[0] != LANGUAGE_ADAPTERS[language] or argv[2] != "/control/oracle-v1.json" or
                type(argv[1]) is not str or not argv[1].startswith("/input/") or
                harness["sha256"] != hashlib.sha256(_canonical(unsigned_harness)).hexdigest()):
            raise PackageError("oracle source provenance harness identity is invalid")
        relative_input = argv[1].removeprefix("/input/")
        input_path = PurePosixPath(relative_input)
        if (input_path.is_absolute() or input_path.as_posix() != relative_input or
                any(part in ("", ".", "..") or part.startswith(".") for part in input_path.parts)):
            raise PackageError("oracle source provenance harness path is invalid")

        toolchain = toolchain_by_language[language]
        counts = source["oracle_kind_counts"]
        if (source["toolchain_id"] != toolchain["toolchain_id"] or
                not isinstance(counts, dict) or set(counts) != set(ORACLE_KINDS) or
                any(type(value) is not int or value < 0 for value in counts.values()) or
                type(source["seed_claims"]) is not int or source["seed_claims"] < 1 or
                sum(counts.values()) != source["seed_claims"] or
                type(source["sandbox_image"]) is not str or
                not re.fullmatch(r"[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}",
                                 source["sandbox_image"]) or
                type(source["extracted_tree_sha256"]) is not str or
                not re.fullmatch(r"[0-9a-f]{64}", source["extracted_tree_sha256"])):
            raise PackageError("oracle source provenance toolchain or seed counts are invalid")
        unsigned = {key: value for key, value in source.items()
                    if key != "source_identity_sha256"}
        if source["source_identity_sha256"] != hashlib.sha256(_canonical(unsigned)).hexdigest():
            raise PackageError("oracle source provenance source identity hash is invalid")


def _source_aggregates(sources):
    aggregates = []
    for language in LANGUAGES:
        selected = [source for source in sources if source["language"] == language]
        aggregates.append({
            "language": language,
            "projects": len({source["lineage_id"] for source in selected}),
            "seed_claims": sum(source["seed_claims"] for source in selected),
            "oracle_kind_counts": {
                kind: sum(source["oracle_kind_counts"][kind] for source in selected)
                for kind in ORACLE_KINDS
            },
            "oracle_kind_projects": {
                kind: len({source["lineage_id"] for source in selected
                           if source["oracle_kind_counts"][kind]})
                for kind in ORACLE_KINDS
            },
        })
    return aggregates


def validate_provenance(path, *, require_ready=True):
    """Validate the public source contract without opening any private corpus artifact."""
    document = _load_json(path, "oracle source provenance")
    if not isinstance(document, dict) or set(document) != {
            "schema_version", "kind", "source_pack_id", "policy", "requirements",
            "toolchains", "sources", "aggregates", "custody_commitments"}:
        raise PackageError("oracle source provenance fields are invalid")
    _forbid_public_answers(document)
    if (type(document["schema_version"]) is not int or document["schema_version"] != 1 or
            document["kind"] != "evergreen-oracle-source-pack-provenance" or
            document["source_pack_id"] != SOURCE_PACK_ID or
            document["requirements"] != PROVENANCE_REQUIREMENTS):
        raise PackageError("oracle source provenance contract is invalid")
    expected_policy = {
        "provenance_schema_sha256": _sha256_file(
            PROVENANCE_SCHEMA_PATH, "provenance schema",
        ),
        "recipe_schema_sha256": _sha256_file(RECIPE_SCHEMA_PATH, "recipe schema"),
        "private_custody_schema_sha256": _sha256_file(
            PRIVATE_CUSTODY_SCHEMA_PATH, "private custody schema",
        ),
        "oracle_schema_sha256": _sha256_file(
            Path(__file__).with_name("schema-v1.json"), "oracle schema",
        ),
        "similarity_policy_sha256": _sha256_file(
            Path(__file__).with_name("similarity-policy-v1.json"), "similarity policy",
        ),
    }
    if document["policy"] != expected_policy:
        raise PackageError("oracle source provenance policy hash is invalid")

    toolchains = document["toolchains"]
    toolchain_keys = {
        "language", "toolchain_id", "runtime", "version", "compiler", "setup_action",
        "setup_commit", "adapter_id", "identity_sha256",
    }
    if not isinstance(toolchains, list) or len(toolchains) != len(LANGUAGES):
        raise PackageError("oracle source provenance toolchains are incomplete")
    seen_toolchains = set()
    seen_toolchain_ids = set()
    for toolchain in toolchains:
        if not isinstance(toolchain, dict) or set(toolchain) != toolchain_keys:
            raise PackageError("oracle source provenance toolchain is invalid")
        language = toolchain["language"]
        unsigned = {key: value for key, value in toolchain.items() if key != "identity_sha256"}
        if (language not in LANGUAGES or language in seen_toolchains or
                any(type(toolchain[key]) is not str or not toolchain[key]
                    for key in ("toolchain_id", "runtime", "version", "compiler")) or
                toolchain["toolchain_id"] in seen_toolchain_ids or
                type(toolchain["setup_action"]) is not str or
                not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+",
                                 toolchain["setup_action"]) or
                toolchain["adapter_id"] != f"{language}-oracle-v1" or
                not re.fullmatch(r"[0-9a-f]{40}", toolchain["setup_commit"]) or
                toolchain["identity_sha256"] != hashlib.sha256(_canonical(unsigned)).hexdigest()):
            raise PackageError("oracle source provenance toolchain identity is invalid")
        seen_toolchains.add(language)
        seen_toolchain_ids.add(toolchain["toolchain_id"])
    if seen_toolchains != set(LANGUAGES):
        raise PackageError("oracle source provenance toolchains are incomplete")

    sources = document["sources"]
    if not isinstance(sources, list):
        raise PackageError("oracle source provenance sources are invalid")
    _validate_public_sources(path, sources, toolchains)
    aggregates = document["aggregates"]
    if not isinstance(aggregates, list) or len(aggregates) != len(LANGUAGES):
        raise PackageError("oracle source provenance aggregates are incomplete")
    aggregate_by_language = {}
    count_keys = set(ORACLE_KINDS)
    for aggregate in aggregates:
        if not isinstance(aggregate, dict) or set(aggregate) != {
                "language", "projects", "seed_claims", "oracle_kind_counts",
                "oracle_kind_projects"}:
            raise PackageError("oracle source provenance aggregate is invalid")
        language = aggregate["language"]
        values = [aggregate["projects"], aggregate["seed_claims"],
                  *aggregate["oracle_kind_counts"].values(),
                  *aggregate["oracle_kind_projects"].values()]
        if (language not in LANGUAGES or language in aggregate_by_language or
                set(aggregate["oracle_kind_counts"]) != count_keys or
                set(aggregate["oracle_kind_projects"]) != count_keys or
                any(type(value) is not int or value < 0 for value in values)):
            raise PackageError("oracle source provenance aggregate is invalid")
        aggregate_by_language[language] = aggregate
    if set(aggregate_by_language) != set(LANGUAGES):
        raise PackageError("oracle source provenance aggregates are incomplete")
    if aggregates != _source_aggregates(sources):
        raise PackageError("oracle source provenance aggregate does not match sources")

    reasons = []
    for language in LANGUAGES:
        if aggregate_by_language[language]["projects"] < MINIMUM_SOURCE_GROUPS:
            reasons.append(f"{language}:projects-below-20")
    for language in LANGUAGES:
        if aggregate_by_language[language]["seed_claims"] < 250:
            reasons.append(f"{language}:seed-claims-below-250")
    for language in LANGUAGES:
        aggregate = aggregate_by_language[language]
        for kind in ORACLE_KINDS:
            if (aggregate["oracle_kind_counts"][kind] < 40 or
                    aggregate["oracle_kind_projects"][kind] < MINIMUM_SOURCE_GROUPS):
                reasons.append(f"{language}:{kind}:post-split-capacity-below-minimum")
    custody = document["custody_commitments"]
    if (not isinstance(custody, dict) or set(custody) != {
            "manifest_sha256", "seed_manifest_sha256", "split_key_sha256",
            "development_package_sha256", "holdout_package_sha256"} or
            any(value is not None and (
                type(value) is not str or not re.fullmatch(r"[0-9a-f]{64}", value)
            ) for value in custody.values())):
        raise PackageError("oracle source provenance custody commitments are invalid")
    if any(value is None for value in custody.values()):
        reasons.append("external-custody-commitments-missing")
    report = {
        "kind": "evergreen-oracle-source-pack-validation",
        "languages": list(LANGUAGES),
        "ready": not reasons,
        "reasons": reasons,
        "schema_version": 1,
        "source_pack_id": SOURCE_PACK_ID,
    }
    if require_ready and reasons:
        raise PackageError("source pack is incomplete: " + ", ".join(reasons))
    return report


def validate_private_custody(path, provenance_path):
    """Validate only a private custody receipt and public commitments, never corpus bytes."""
    resolved = Path(path).resolve()
    if resolved == REPOSITORY_ROOT or REPOSITORY_ROOT in resolved.parents:
        raise PackageError("private custody receipt must remain outside the detector repository")
    raw = _read_path_nofollow(path, 16 * 1024 * 1024, "private custody receipt", owner_only=True)
    try:
        custody = _loads_strict(raw)
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise PackageError("private custody receipt is invalid") from None
    private_keys = {
        "code", "documentation", "label", "verdict", "mutation", "observable", "split_key",
        "seed_manifest", "development_package", "holdout_package",
    }

    def reject_private_material(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if key in private_keys:
                    raise PackageError("private custody receipt embeds private material")
                reject_private_material(child)
        elif isinstance(value, list):
            for child in value:
                reject_private_material(child)

    reject_private_material(custody)
    provenance = _load_json(provenance_path, "oracle source provenance")
    validate_provenance(provenance_path)
    if (not isinstance(custody, dict) or set(custody) != {
            "schema_version", "kind", "source_pack_id", "public_inventory_sha256",
            "artifacts", "toolchain_receipts", "split_aggregates"} or
            type(custody["schema_version"]) is not int or custody["schema_version"] != 1 or
            custody["kind"] != "evergreen-oracle-private-custody-receipt" or
            custody["source_pack_id"] != provenance["source_pack_id"]):
        raise PackageError("private custody receipt fields are invalid")
    if provenance["custody_commitments"]["manifest_sha256"] != hashlib.sha256(raw).hexdigest():
        raise PackageError("private custody manifest commitment does not match")
    inventory = {key: value for key, value in provenance.items()
                 if key != "custody_commitments"}
    if custody["public_inventory_sha256"] != hashlib.sha256(_canonical(inventory)).hexdigest():
        raise PackageError("private custody public inventory commitment does not match")

    expected_artifacts = {
        "seed-manifest": provenance["custody_commitments"]["seed_manifest_sha256"],
        "split-key": provenance["custody_commitments"]["split_key_sha256"],
        "development-package": provenance["custody_commitments"]["development_package_sha256"],
        "holdout-package": provenance["custody_commitments"]["holdout_package_sha256"],
    }
    artifacts = custody["artifacts"]
    if not isinstance(artifacts, list) or len(artifacts) != len(expected_artifacts):
        raise PackageError("private custody artifact commitment is invalid")
    artifact_map = {}
    for artifact in artifacts:
        if (not isinstance(artifact, dict) or set(artifact) != {
                "role", "relative_path", "sha256", "bytes"} or
                artifact["role"] not in expected_artifacts or artifact["role"] in artifact_map or
                artifact["sha256"] != expected_artifacts[artifact["role"]] or
                type(artifact["bytes"]) is not int or artifact["bytes"] < 1):
            raise PackageError("private custody artifact commitment is invalid")
        relative = artifact["relative_path"]
        if type(relative) is not str:
            raise PackageError("private custody artifact path is invalid")
        pure = PurePosixPath(relative)
        if (pure.is_absolute() or pure.as_posix() != relative or
                any(part in ("", ".", "..") or part.startswith(".") for part in pure.parts)):
            raise PackageError("private custody artifact path is invalid")
        asset = Path(path).absolute().parent / relative
        asset_raw = _read_path_nofollow(
            asset, 64 * 1024 * 1024, "private custody artifact", owner_only=True,
        )
        if (len(asset_raw) != artifact["bytes"] or
                hashlib.sha256(asset_raw).hexdigest() != artifact["sha256"]):
            raise PackageError("private custody artifact bytes do not match receipt")
        artifact_map[artifact["role"]] = artifact
    if set(artifact_map) != set(expected_artifacts):
        raise PackageError("private custody artifact commitment is invalid")

    toolchains = {item["language"]: item for item in provenance["toolchains"]}
    source_images = {
        language: {item["sandbox_image"] for item in provenance["sources"]
                   if item["language"] == language}
        for language in LANGUAGES
    }
    receipts = custody["toolchain_receipts"]
    if not isinstance(receipts, list) or len(receipts) != len(LANGUAGES):
        raise PackageError("private custody toolchain receipts are invalid")
    seen_languages = set()
    receipt_keys = {
        "language", "toolchain_id", "identity_sha256", "executable_sha256",
        "adapter_sha256", "sandbox_image",
    }
    for receipt in receipts:
        if not isinstance(receipt, dict) or set(receipt) != receipt_keys:
            raise PackageError("private custody toolchain receipt is invalid")
        language = receipt["language"]
        expected = toolchains.get(language)
        if (expected is None or language in seen_languages or
                receipt["toolchain_id"] != expected["toolchain_id"] or
                receipt["identity_sha256"] != expected["identity_sha256"] or
                any(type(receipt[key]) is not str or
                    not re.fullmatch(r"[0-9a-f]{64}", receipt[key])
                    for key in ("executable_sha256", "adapter_sha256")) or
                source_images[language] != {receipt["sandbox_image"]}):
            raise PackageError("private custody toolchain receipt is invalid")
        seen_languages.add(language)
    if seen_languages != set(LANGUAGES):
        raise PackageError("private custody toolchain receipts are invalid")

    split_aggregates = custody["split_aggregates"]
    if not isinstance(split_aggregates, list) or len(split_aggregates) != 2:
        raise PackageError("private custody split capacity is invalid")
    split_map = {}
    for split in split_aggregates:
        if (not isinstance(split, dict) or set(split) != {"split_name", "languages"} or
                split["split_name"] not in ("dev", "holdout") or
                split["split_name"] in split_map or not isinstance(split["languages"], list)):
            raise PackageError("private custody split capacity is invalid")
        language_map = {}
        for cell in split["languages"]:
            if (not isinstance(cell, dict) or set(cell) != {
                    "language", "projects", "seed_claims", "consistent_rows",
                    "inconsistent_rows", "oracle_kinds"} or
                    cell["language"] not in LANGUAGES or cell["language"] in language_map or
                    not isinstance(cell["oracle_kinds"], dict) or
                    set(cell["oracle_kinds"]) != set(ORACLE_KINDS)):
                raise PackageError("private custody split capacity is invalid")
            for kind_cell in cell["oracle_kinds"].values():
                if (not isinstance(kind_cell, dict) or set(kind_cell) != {
                        "projects", "seed_claims", "consistent_rows", "inconsistent_rows"} or
                        type(kind_cell["projects"]) is not int or kind_cell["projects"] < 10 or
                        type(kind_cell["seed_claims"]) is not int or
                        kind_cell["seed_claims"] < 20 or
                        type(kind_cell["consistent_rows"]) is not int or
                        kind_cell["consistent_rows"] < 40 or
                        type(kind_cell["inconsistent_rows"]) is not int or
                        kind_cell["inconsistent_rows"] < 20):
                    raise PackageError("private custody split capacity is invalid")
            kind_values = list(cell["oracle_kinds"].values())
            if (type(cell["projects"]) is not int or cell["projects"] < 10 or
                    cell["seed_claims"] != sum(item["seed_claims"] for item in kind_values) or
                    cell["consistent_rows"] != sum(
                        item["consistent_rows"] for item in kind_values) or
                    cell["inconsistent_rows"] != sum(
                        item["inconsistent_rows"] for item in kind_values) or
                    cell["consistent_rows"] != 2 * cell["seed_claims"] or
                    cell["inconsistent_rows"] != cell["seed_claims"]):
                raise PackageError("private custody split capacity is invalid")
            language_map[cell["language"]] = cell
        if set(language_map) != set(LANGUAGES):
            raise PackageError("private custody split capacity is invalid")
        split_map[split["split_name"]] = language_map
    if set(split_map) != {"dev", "holdout"}:
        raise PackageError("private custody split capacity is invalid")
    public_aggregates = {item["language"]: item for item in provenance["aggregates"]}
    for language in LANGUAGES:
        if sum(split_map[name][language]["seed_claims"] for name in split_map) != \
                public_aggregates[language]["seed_claims"]:
            raise PackageError("private custody split capacity does not match public aggregate")
        for kind in ORACLE_KINDS:
            if sum(split_map[name][language]["oracle_kinds"][kind]["seed_claims"]
                   for name in split_map) != public_aggregates[language]["oracle_kind_counts"][kind]:
                raise PackageError("private custody split capacity does not match public aggregate")
    return {
        "kind": "evergreen-oracle-private-custody-validation",
        "languages": list(LANGUAGES),
        "schema_version": 1,
        "source_pack_id": SOURCE_PACK_ID,
        "valid": True,
    }


def _manifest(path, policy_hash):
    resolved = Path(path).resolve()
    if resolved == REPOSITORY_ROOT or REPOSITORY_ROOT in resolved.parents:
        raise PackageError("oracle source manifest must be outside the detector repository")
    document = _load_json(path, "oracle source manifest")
    if (not isinstance(document, dict) or set(document) != {
            "schema_version", "similarity_policy_sha256", "seeds"} or
            type(document["schema_version"]) is not int or document["schema_version"] != 1 or
            document["similarity_policy_sha256"] != policy_hash or
            not isinstance(document["seeds"], list) or not document["seeds"] or
            len(document["seeds"]) > MAX_SOURCE_SEEDS):
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


def _git_bytes(*arguments, maximum=64 * 1024 * 1024):
    try:
        completed = subprocess.run(
            ["git", "-C", str(REPOSITORY_ROOT), *arguments], capture_output=True,
            timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise PackageError("trusted Git inventory command failed") from None
    if completed.returncode or len(completed.stdout) > maximum:
        raise PackageError("trusted Git inventory command failed")
    return completed.stdout


def _load_reference_inventory(
    subject_commit, subject_tree, *, require_clean=True, caller_inventory=None,
):
    """Derive the exclusion corpus from regular blobs at one exact subject tree."""
    if caller_inventory is not None:
        raise PackageError("caller-supplied reference inventories are forbidden")
    hex_identity = re.compile(r"[0-9a-f]{40,64}")
    if (type(subject_commit) is not str or not hex_identity.fullmatch(subject_commit) or
            type(subject_tree) is not str or not hex_identity.fullmatch(subject_tree)):
        raise PackageError("reference subject identity is invalid")
    resolved_commit = _git_bytes("rev-parse", "--verify", f"{subject_commit}^{{commit}}", maximum=256)
    resolved_tree = _git_bytes("rev-parse", "--verify", f"{subject_commit}^{{tree}}", maximum=256)
    if (resolved_commit.decode().strip() != subject_commit or
            resolved_tree.decode().strip() != subject_tree):
        raise PackageError("reference subject commit and tree do not match")
    if require_clean and _git_bytes("status", "--porcelain=v1", "-z", "--untracked-files=all"):
        raise PackageError("reference subject checkout is dirty")
    try:
        policy_raw = _read_path_nofollow(
            REFERENCE_POLICY_PATH, MAX_REFERENCE_INVENTORY_BYTES, "reference inventory policy",
        )
        policy = _loads_strict(policy_raw)
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        raise PackageError("reference inventory policy is unavailable") from None
    if (hashlib.sha256(policy_raw).hexdigest() != REFERENCE_POLICY_SHA256 or
            policy != _REFERENCE_POLICY):
        raise PackageError("reference inventory policy drifted")
    tree_entries = {}
    raw_tree = _git_bytes("ls-tree", "-rz", "--full-tree", subject_commit)
    try:
        for record in raw_tree.split(b"\0"):
            if not record:
                continue
            metadata, raw_path = record.split(b"\t", 1)
            mode, kind, object_id = metadata.decode("ascii").split(" ")
            relative = raw_path.decode("utf-8")
            if (mode not in ("100644", "100755") or kind != "blob" or relative in tree_entries):
                continue
            tree_entries[relative] = object_id
    except (UnicodeError, ValueError):
        raise PackageError("reference subject tree is invalid") from None
    references = []
    commitment_entries = []
    seen_paths = set()
    excluded = set(policy["exclude_names"])
    for category, patterns in policy["categories"].items():
        category_paths = set()
        for pattern in patterns:
            matched = {path for path in tree_entries if fnmatch.fnmatchcase(path, pattern)}
            if not matched:
                raise PackageError(
                    f"reference inventory required pattern {pattern} is empty"
                )
            category_paths.update(matched)
        category_paths = {
            path for path in category_paths if not any(part in excluded for part in path.split("/"))
        }
        if not category_paths:
            raise PackageError(f"reference inventory category {category} is empty")
        for relative in sorted(category_paths):
            if relative in seen_paths:
                raise PackageError("reference inventory paths overlap categories")
            seen_paths.add(relative)
            content = _git_bytes(
                "cat-file", "blob", tree_entries[relative], maximum=MAX_REFERENCE_CONTENT_BYTES,
            )
            try:
                text = content.decode("utf-8")
            except UnicodeError:
                raise PackageError("reference content is not UTF-8") from None
            digest = hashlib.sha256(content).hexdigest()
            field = (
                "code" if category in ("test", "fixture") and relative.endswith(".py")
                else "documentation"
            )
            references.append({
                "category": category, "source": relative, "field": field,
                "language": "python", "text": text,
            })
            commitment_entries.append({
                "category": category, "path": relative, "sha256": digest,
            })
    if {item["category"] for item in references} != set(REFERENCE_CATEGORIES):
        raise PackageError("reference inventory does not cover every category")
    commitment = hashlib.sha256(_canonical({
        "policy_sha256": REFERENCE_POLICY_SHA256,
        "subject_commit": subject_commit, "subject_tree": subject_tree,
        "files": sorted(commitment_entries, key=lambda item: (item["category"], item["path"])),
    })).hexdigest()
    return references, commitment


def _path_sha256(path):
    return hashlib.sha256(os.fsencode(str(Path(path).absolute()))).hexdigest()


def validate_package_rows(
    rows, *, languages=LANGUAGES, limits=DEFAULT_LIMITS, oracle_kinds=ORACLE_KINDS,
):
    """Enforce post-split language, class, repository, balance, and share gates."""
    if not isinstance(rows, list) or not rows:
        raise PackageError("package is below the language minimum")
    if len(rows) > MAX_PACKAGE_ROWS:
        raise PackageError("package row limit exceeded")
    seen = set()
    operator_by_kind = {
        contract["kind"]: identity for identity, contract in MUTATION_OPERATORS.items()
    }
    for row in rows:
        if not isinstance(row, dict) or type(row.get("id")) is not str or not row["id"]:
            raise PackageError("package row is invalid")
        if row.get("label") not in ("consistent", "inconsistent"):
            raise PackageError("package row label is invalid")
        if row.get("language") not in languages:
            raise PackageError("package contains an unknown language")
        if row.get("oracle_kind") not in oracle_kinds:
            raise PackageError("package row oracle kind is invalid")
        variant = row.get("variant")
        mutation_id = row.get("mutation_id")
        if ((variant == "mutation" and (
                row["label"] != "inconsistent" or
                mutation_id != operator_by_kind.get(row["oracle_kind"]))) or
                (variant == "source" and (
                    row["label"] != "consistent" or mutation_id is not None)) or
                (variant == "semantic-noop" and (
                    row["label"] != "consistent" or mutation_id != "comment-v1")) or
                variant not in ("source", "mutation", "semantic-noop")):
            raise PackageError("package row operator contract is invalid")
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
            cell_groups = {}
            for row in cell:
                lineage = row["lineage_id"]
                cell_groups[lineage] = cell_groups.get(lineage, 0) + 1
            if len(cell_groups) < limits.minimum_repositories:
                raise PackageError("package kind cell repository minimum is not met")
            if max(cell_groups.values()) / len(cell) > limits.maximum_repository_share:
                raise PackageError("package kind cell repository share exceeds the maximum")
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


def _open_parent_no_symlinks(path, label):
    absolute = Path(os.path.abspath(path))
    if not absolute.is_absolute() or absolute.name in ("", ".", ".."):
        raise PackageError(f"{label} path is invalid")
    flags = os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open("/", flags)
    try:
        for component in absolute.parent.parts[1:]:
            child = os.open(component, flags, dir_fd=descriptor)
            os.close(descriptor)
            descriptor = child
    except OSError:
        os.close(descriptor)
        raise PackageError(f"{label} parent contains a symlink or is unavailable") from None
    return descriptor, absolute.name, absolute


def _read_path_nofollow(path, maximum, label, *, owner_only=False):
    parent, name, _absolute = _open_parent_no_symlinks(path, label)
    descriptor = None
    try:
        descriptor = os.open(name, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0), dir_fd=parent)
        before = os.fstat(descriptor)
        if not stat.S_ISREG(before.st_mode):
            raise PackageError(f"{label} is not a regular file")
        if owner_only and (stat.S_IMODE(before.st_mode) & 0o077 or
                           hasattr(os, "getuid") and before.st_uid != os.getuid()):
            raise PackageError(f"{label} is not owner-only")
        chunks = []
        total = 0
        while True:
            chunk = os.read(descriptor, min(64 * 1024, maximum + 1 - total))
            if not chunk:
                break
            chunks.append(chunk)
            total += len(chunk)
            if total > maximum:
                raise PackageError(f"{label} exceeds the byte limit")
        after = os.fstat(descriptor)
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise PackageError(f"{label} changed during validation")
        return b"".join(chunks)
    except OSError:
        raise PackageError(f"{label} is unavailable") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        os.close(parent)


def _create_private_root(parent_descriptor, name):
    created = False
    try:
        os.mkdir(name, mode=0o700, dir_fd=parent_descriptor)
        created = True
        descriptor = os.open(
            name, os.O_RDONLY | getattr(os, "O_DIRECTORY", 0) |
            getattr(os, "O_NOFOLLOW", 0), dir_fd=parent_descriptor,
        )
    except OSError:
        if created:
            try:
                os.rmdir(name, dir_fd=parent_descriptor)
            except OSError:
                pass
        raise PackageError("private destination must be exclusively created") from None
    return descriptor


def _write_private_file(directory_descriptor, raw):
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open("oracle.jsonl", flags, 0o600, dir_fd=directory_descriptor)
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.fsync(directory_descriptor)
    except OSError:
        raise PackageError("private package exclusive creation failed") from None


def _cleanup_private_root(parent_descriptor, name, directory_descriptor):
    try:
        try:
            os.unlink("oracle.jsonl", dir_fd=directory_descriptor)
        except FileNotFoundError:
            pass
        live = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        created = os.fstat(directory_descriptor)
        if (live.st_dev, live.st_ino) == (created.st_dev, created.st_ino):
            os.rmdir(name, dir_fd=parent_descriptor)
    except OSError:
        pass


def _verify_private_roots(created):
    for parent, name, directory in created:
        live = os.stat(name, dir_fd=parent, follow_symlinks=False)
        opened = os.fstat(directory)
        if ((live.st_dev, live.st_ino) != (opened.st_dev, opened.st_ino) or
                not stat.S_ISDIR(live.st_mode)):
            raise PackageError("private destination changed during publication")
        package = os.stat("oracle.jsonl", dir_fd=directory, follow_symlinks=False)
        if not stat.S_ISREG(package.st_mode) or stat.S_IMODE(package.st_mode) != 0o600:
            raise PackageError("private package changed during publication")


def _atomic_public(parent_descriptor, name, raw):
    temporary = f".{name}.{secrets.token_hex(12)}"
    descriptor = None
    expected = None
    try:
        try:
            current = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
            if not stat.S_ISREG(current.st_mode):
                raise PackageError("public split manifest destination is not a regular file")
            expected = (current.st_dev, current.st_ino)
        except FileNotFoundError:
            pass
        descriptor = os.open(
            temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0),
            0o644, dir_fd=parent_descriptor,
        )
        with os.fdopen(descriptor, "wb") as output:
            descriptor = None
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        try:
            current = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
            live = (current.st_dev, current.st_ino)
        except FileNotFoundError:
            live = None
        if live != expected:
            raise PackageError("public split manifest destination changed before publication")
        os.replace(temporary, name, src_dir_fd=parent_descriptor, dst_dir_fd=parent_descriptor)
        os.fsync(parent_descriptor)
        published = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        return published.st_dev, published.st_ino
    except PackageError:
        raise
    except OSError:
        raise PackageError("public split manifest atomic write failed") from None
    finally:
        if descriptor is not None:
            os.close(descriptor)
        try:
            os.unlink(temporary, dir_fd=parent_descriptor)
        except FileNotFoundError:
            pass


def _remove_public_if_identity(parent_descriptor, name, identity):
    if identity is None:
        return
    try:
        live = os.stat(name, dir_fd=parent_descriptor, follow_symlinks=False)
        if (live.st_dev, live.st_ino) == identity:
            os.unlink(name, dir_fd=parent_descriptor)
            os.fsync(parent_descriptor)
    except OSError:
        pass


def _build_packages(
    manifest_path, development_root, holdout_root, public_manifest, split_key, approved_images, *,
    references=None, derive_seed=run_seed, limits=DEFAULT_LIMITS, languages=LANGUAGES,
    source_group_limits=DEFAULT_SOURCE_GROUP_LIMITS, oracle_kinds=ORACLE_KINDS,
    reference_corpus_sha256="f" * 64,
    subject_commit="0" * 40, subject_tree="1" * 40,
):
    """Derive, isolate, validate, and exclusively write private split packages."""
    supplied_roots = [Path(item) for item in (development_root, holdout_root)]
    if not all(root.is_absolute() for root in supplied_roots):
        raise PackageError("private destination must be absolute and outside the repository")
    roots = [Path(os.path.abspath(item)) for item in supplied_roots]
    if roots[0] == roots[1] or roots[0] in roots[1].parents or roots[1] in roots[0].parents:
        raise PackageError("development and holdout require separate private roots")
    for root in roots:
        if root == REPOSITORY_ROOT or REPOSITORY_ROOT in root.parents:
            raise PackageError("private destination must be absolute and outside the repository")
    opened_parents = []
    try:
        for root in roots:
            parent, name, absolute = _open_parent_no_symlinks(root, "private destination")
            try:
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                os.close(parent)
                raise PackageError("private destination must be exclusively created")
            opened_parents.append((parent, name, absolute))
        public_parent, public_name, resolved_public = _open_parent_no_symlinks(
            public_manifest, "public split manifest",
        )
    except Exception:
        for parent, _name, _absolute in opened_parents:
            os.close(parent)
        raise
    if any(resolved_public == root or root in resolved_public.parents for root in roots):
        for parent, _name, _absolute in opened_parents:
            os.close(parent)
        os.close(public_parent)
        raise PackageError("public split manifest must be outside private destinations")
    for parent, _name, _absolute in opened_parents:
        os.close(parent)
    os.close(public_parent)
    opened_parents = []
    if not isinstance(references, list) or not references:
        raise PackageError("a complete reference corpus is required")
    if ({item.get("category") for item in references if isinstance(item, dict)} !=
            set(REFERENCE_CATEGORIES)):
        raise PackageError("a complete reference corpus is required")
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
                "reference_corpus_sha256": reference_corpus_sha256,
                "subject_commit": subject_commit,
                "subject_tree": subject_tree,
            })
            if len(rows) > MAX_PACKAGE_ROWS:
                raise PackageError("derived oracle row limit exceeded")
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
        for root in roots:
            parent, name, absolute = _open_parent_no_symlinks(root, "private destination")
            try:
                os.stat(name, dir_fd=parent, follow_symlinks=False)
            except FileNotFoundError:
                pass
            else:
                os.close(parent)
                raise PackageError("private destination must be exclusively created")
            opened_parents.append((parent, name, absolute))
        public_parent, public_name, _public_absolute = _open_parent_no_symlinks(
            public_manifest, "public split manifest",
        )
    except Exception:
        for parent, _name, _absolute in opened_parents:
            os.close(parent)
        raise
    created = []
    try:
        for split, (parent, name, _absolute) in zip(("dev", "holdout"), opened_parents):
            directory = _create_private_root(parent, name)
            created.append((parent, name, directory))
            _write_private_file(directory, packages[split]["raw"])
    except Exception:
        for parent, name, directory in created:
            _cleanup_private_root(parent, name, directory)
            os.close(directory)
        for parent, _name, _absolute in opened_parents:
            os.close(parent)
        os.close(public_parent)
        raise

    declarations = [{
        "sha256": packages[split]["package_sha256"],
        "path_sha256": _path_sha256(opened_parents[index][2] / "oracle.jsonl"), "split": split,
        "rows": packages[split]["rows"],
    } for index, split in enumerate(("dev", "holdout"))]
    public_rows = [{
        "id": row["id"], "dataset_sha256": packages[row["split"]]["package_sha256"],
        "split": row["split"],
    } for row in sorted(rows, key=lambda item: item["id"])]
    document = {
        "schema_version": 2,
        "similarity_policy_sha256": policy_hash,
        "reference_corpus_sha256": reference_corpus_sha256,
        "subject_commit": subject_commit,
        "subject_tree": subject_tree,
        "datasets": declarations,
        "rows": public_rows,
    }
    published_identity = None
    try:
        _verify_private_roots(created)
        published_identity = _atomic_public(
            public_parent, public_name, _canonical(document) + b"\n",
        )
        _verify_private_roots(created)
    except Exception:
        for parent, name, directory in created:
            _cleanup_private_root(parent, name, directory)
        _remove_public_if_identity(public_parent, public_name, published_identity)
        raise
    finally:
        for _parent, _name, directory in created:
            os.close(directory)
        for parent, _name, _absolute in opened_parents:
            os.close(parent)
        os.close(public_parent)
    return packages["dev"], packages["holdout"], public_manifest


def build_packages(
    manifest_path, development_root, holdout_root, public_manifest, split_key, approved_images,
    subject_commit, subject_tree,
):
    """Build production packages with the frozen oracle and five-language gates."""
    references, reference_corpus_sha256 = _load_reference_inventory(subject_commit, subject_tree)
    return _build_packages(
        manifest_path, development_root, holdout_root, public_manifest, split_key, approved_images,
        references=references, derive_seed=run_seed, limits=DEFAULT_LIMITS, languages=LANGUAGES,
        source_group_limits=DEFAULT_SOURCE_GROUP_LIMITS, oracle_kinds=ORACLE_KINDS,
        reference_corpus_sha256=reference_corpus_sha256,
        subject_commit=subject_commit, subject_tree=subject_tree,
    )


def _read_private_rows(path, maximum=64 * 1024 * 1024):
    try:
        raw = _read_path_nofollow(
            path, maximum, "private oracle package", owner_only=True,
        )
        rows = [_loads_strict(line) for line in raw.splitlines() if line]
    except (OSError, UnicodeError, json.JSONDecodeError, ValueError):
        raise PackageError("private package is unavailable or invalid") from None
    return raw, rows


def load_development_rows(public_manifest, development_package):
    """Load only a caller-supplied development package; no holdout path is accepted."""
    from eval.bench.split_manifest import _manifest_v2

    before = _read_path_nofollow(
        public_manifest, 64 * 1024 * 1024, "public split manifest",
    )
    try:
        document = _loads_strict(before)
    except (UnicodeError, json.JSONDecodeError, ValueError):
        raise PackageError("public split manifest is invalid") from None
    if (not isinstance(document, dict) or document.get("schema_version") != 2 or
            set(document) != {"schema_version", "similarity_policy_sha256",
                              "reference_corpus_sha256", "subject_commit", "subject_tree",
                              "datasets", "rows"}):
        raise PackageError("public split manifest is invalid")
    try:
        assignments = _manifest_v2(document)[0]
    except ValueError as error:
        raise PackageError("public split manifest is invalid") from error
    _policy, policy_hash = load_similarity_policy()
    if document["similarity_policy_sha256"] != policy_hash:
        raise PackageError("public split manifest policy drifted")
    reference_corpus_sha256 = document["reference_corpus_sha256"]
    if (type(reference_corpus_sha256) is not str or len(reference_corpus_sha256) != 64):
        raise PackageError("public split manifest reference corpus is invalid")
    subject_commit = document["subject_commit"]
    subject_tree = document["subject_tree"]
    if (type(subject_commit) is not str or type(subject_tree) is not str or
            not re.fullmatch(r"[0-9a-f]{40,64}", subject_commit) or
            not re.fullmatch(r"[0-9a-f]{40,64}", subject_tree)):
        raise PackageError("public split manifest subject identity is invalid")
    declarations = [item for item in document["datasets"]
                    if isinstance(item, dict) and item.get("split") == "dev"]
    if len(declarations) != 1:
        raise PackageError("public split manifest development declaration is invalid")
    if declarations[0].get("path_sha256") != _path_sha256(development_package):
        raise PackageError("development package path identity does not match the public manifest")
    raw, rows = _read_private_rows(development_package)
    if hashlib.sha256(raw).hexdigest() != declarations[0].get("sha256"):
        raise PackageError("development package hash does not match the public manifest")
    expected_ids = {row_id for row_id, split in assignments.items() if split == "dev"}
    if (len(assignments) != len(document["rows"]) or
            {row.get("id") for row in rows} != expected_ids or
            any(row.get("split") != "dev" or
                row.get("similarity_policy_sha256") != policy_hash or
                row.get("reference_corpus_sha256") != reference_corpus_sha256 or
                row.get("subject_commit") != subject_commit or row.get("subject_tree") != subject_tree
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
    provenance = commands.add_parser(
        "validate-provenance", help="validate the public source-pack provenance contract"
    )
    provenance.add_argument("--manifest", required=True)
    provenance.add_argument(
        "--contract-only", action="store_true",
        help="validate structure and report missing external custody without claiming readiness",
    )
    custody = commands.add_parser(
        "validate-custody", help="validate an external owner-only custody receipt"
    )
    custody.add_argument("--manifest", required=True)
    custody.add_argument("--provenance", required=True)
    arguments = parser.parse_args(sys.argv[1:] if argv is None else argv)
    if arguments.command == "development":
        rows = load_development_rows(Path(arguments.manifest), Path(arguments.package))
        for row in sorted(rows, key=lambda item: item["id"]):
            sys.stdout.buffer.write(_canonical(row) + b"\n")
        return 0
    if arguments.command == "validate-provenance":
        try:
            report = validate_provenance(
                Path(arguments.manifest), require_ready=not arguments.contract_only,
            )
        except PackageError as error:
            parser.exit(2, f"oracle provenance invalid: {error}\n")
        sys.stdout.buffer.write(_canonical(report) + b"\n")
        return 0
    if arguments.command == "validate-custody":
        try:
            report = validate_private_custody(
                Path(arguments.manifest), Path(arguments.provenance),
            )
        except PackageError as error:
            parser.exit(2, f"oracle custody invalid: {error}\n")
        sys.stdout.buffer.write(_canonical(report) + b"\n")
        return 0
    raise PackageError("unknown oracle package command")


if __name__ == "__main__":
    raise SystemExit(main())
