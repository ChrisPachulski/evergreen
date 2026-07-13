#!/usr/bin/env python3
"""Export and verify bounded public benchmark decision artifacts."""

import argparse
import hashlib
import json
import math
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path

try:
    from . import artifact, report, runner
except ImportError:  # Direct script execution.
    import artifact
    import report
    import runner


PUBLICATION_SCHEMA_VERSION = 1
MAX_PUBLIC_ARTIFACT_BYTES = 2 * 1024 * 1024
MAX_TOTAL_PUBLIC_BYTES = 8 * 1024 * 1024
MAX_PUBLIC_ROWS = 100_000
MAX_PUBLIC_ARTIFACTS = 5
VERDICTS = {"consistent", "inconsistent"}
RESULT_STATUSES = {"complete", "abstain"}
STAGE_STATUSES = {"ok", "abstain"}
STAGES = {"snap", "challenge", "prongs", "prongs_escalated", "blindspot", "synthesis"}
PRONG_ROLES = {"defend", "prove-wrong", "hardest-broken"}
SHA256 = re.compile(r"[0-9a-f]{64}")


def canonical_bytes(value):
    """Serialize deterministic JSON using the benchmark artifact format."""
    return (json.dumps(value, indent=2, sort_keys=True, allow_nan=False) + "\n").encode()


def _category(value, label="category"):
    if value not in artifact.VALID_CATEGORIES:
        raise ValueError(f"{label} is invalid")
    return value


def _verdict(value, *, nullable=False, label="verdict"):
    if nullable and value is None:
        return None
    if value not in VERDICTS:
        raise ValueError(f"{label} is invalid")
    return value


def _stage(record, name):
    if not isinstance(record, dict) or record.get("status") not in STAGE_STATUSES:
        raise ValueError(f"{name} stage status is invalid")
    if record["status"] == "abstain":
        return None, {"status": "abstain"}
    value = record.get("value")
    if not isinstance(value, dict):
        raise ValueError(f"{name} stage value is invalid")
    return value, {"status": "ok"}


def _project_verdict_stage(record, name):
    value, projected = _stage(record, name)
    if value is None:
        return projected
    projected["verdict"] = _verdict(value.get("verdict"), label=f"{name} verdict")
    projected["category"] = _category(value.get("category"), f"{name} category")
    return projected


def _project_prongs(records, name):
    if not isinstance(records, list) or len(records) != 3:
        raise ValueError(f"{name} must contain three results")
    projected = []
    roles = set()
    for record in records:
        value, item = _stage(record, name)
        if value is not None:
            role = value.get("role")
            if role not in PRONG_ROLES or role in roles:
                raise ValueError(f"{name} role is invalid")
            roles.add(role)
            item.update({
                "role": role,
                "verdict": _verdict(value.get("verdict"), label=f"{name} verdict"),
            })
        projected.append(item)
    return projected


def project_trial(stages):
    """Retain only structured trial outcomes and discard all free-form text."""
    if not isinstance(stages, dict):
        raise ValueError("trial stages must be an object")
    if set(stages) - STAGES:
        raise ValueError("unknown trial stage")
    projected = {}
    if "snap" in stages:
        projected["snap"] = _project_verdict_stage(stages["snap"], "snap")
    if "challenge" in stages:
        value, item = _stage(stages["challenge"], "challenge")
        if value is not None:
            cracks = value.get("cracks")
            if type(cracks) is not bool:
                raise ValueError("challenge cracks is invalid")
            item["cracks"] = cracks
        projected["challenge"] = item
    for name in ("prongs", "prongs_escalated"):
        if name in stages:
            projected[name] = _project_prongs(stages[name], name)
    if "blindspot" in stages:
        value, item = _stage(stages["blindspot"], "blindspot")
        if value is not None:
            missed = value.get("missed_angle")
            if missed is not None and (not isinstance(missed, str) or not missed.strip()):
                raise ValueError("blindspot missed angle is invalid")
            item["missed_angle_present"] = missed is not None
        projected["blindspot"] = item
    if "synthesis" in stages:
        projected["synthesis"] = _project_verdict_stage(stages["synthesis"], "synthesis")
    return projected


def project_row(row):
    """Project one private result row through an explicit public allowlist."""
    artifact.validate_benchmark_row(row, require_result=True)
    got = row["got"]
    status = got.get("final_status")
    if status not in RESULT_STATUSES:
        raise ValueError("final status is invalid")
    final_verdict = _verdict(
        got.get("final_verdict"), nullable=status == "abstain", label="final verdict"
    )
    verdict = _verdict(got.get("verdict"), nullable=status == "abstain")
    if status == "abstain" and (final_verdict is not None or verdict is not None):
        raise ValueError("abstained result must not have a verdict")
    contested = got.get("contested")
    if type(contested) is not bool:
        raise ValueError("contested must be a boolean")
    return {
        "id": row["id"],
        "language": row.get("language", "unknown"),
        "label": row["label"],
        "category": row["category"],
        "got": {
            "final_status": status,
            "final_verdict": final_verdict,
            "verdict": verdict,
            "category": _category(got.get("category"), "predicted category"),
            "contested": contested,
        },
        "trial": project_trial(got.get("stages")),
    }


def project_artifact(document):
    """Project a private version-1 benchmark artifact into its public form."""
    if (not isinstance(document, dict) or type(document.get("schema_version")) is not int or
            document["schema_version"] != PUBLICATION_SCHEMA_VERSION):
        raise ValueError("unsupported benchmark artifact schema")
    report._validate_metadata(document.get("metadata"))
    timing = document.get("timing")
    if not isinstance(timing, dict):
        raise ValueError("artifact timing is invalid")
    elapsed = timing.get("elapsed_seconds")
    if (not artifact.valid_iso_time(timing.get("started_at")) or
            not isinstance(elapsed, (int, float)) or isinstance(elapsed, bool) or
            not math.isfinite(elapsed) or elapsed < 0):
        raise ValueError("artifact timing is invalid")
    rows = document.get("rows")
    if not isinstance(rows, list) or len(rows) > MAX_PUBLIC_ROWS:
        raise ValueError("artifact rows are invalid")
    projected_rows = [project_row(row) for row in rows]
    identifiers = [row["id"] for row in projected_rows]
    if len(identifiers) != len(set(identifiers)):
        raise ValueError("artifact contains duplicate pair ids")
    projected = {
        "schema_version": PUBLICATION_SCHEMA_VERSION,
        "metadata": document["metadata"],
        "timing": timing,
        "rows": projected_rows,
    }
    if "provider_usage" in document:
        artifact.validate_usage(document["provider_usage"])
        projected["provider_usage"] = document["provider_usage"]
    return projected


def parse_source(value):
    """Parse one expected-hash/source-path CLI value."""
    if not isinstance(value, str) or "=" not in value:
        raise ValueError("source must be SHA256=PATH")
    digest, raw_path = value.split("=", 1)
    if not SHA256.fullmatch(digest) or not raw_path:
        raise ValueError("source must be SHA256=PATH with lowercase SHA-256")
    return digest, Path(raw_path)


def repository_path(path, repo):
    """Return a safe repository-relative POSIX path."""
    repo = Path(repo).resolve()
    path = Path(path)
    resolved = (repo / path).resolve() if not path.is_absolute() else path.resolve()
    try:
        return resolved.relative_to(repo).as_posix()
    except ValueError:
        raise ValueError("publication path escapes repository") from None


def _settings_sha256(settings):
    encoded = json.dumps(settings, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()


def _fsync_directory(path):
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def export_publication(
    source_specs, output_dir, evaluated_release, required_languages, coverage_threshold,
    report_path, repo,
):
    """Export a new immutable public publication directory."""
    repo = Path(repo).resolve()
    output_dir = Path(output_dir)
    if not output_dir.is_absolute():
        output_dir = repo / output_dir
    repository_path(output_dir, repo)
    if output_dir.exists() or output_dir.is_symlink():
        raise ValueError("public output directory already exists")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", evaluated_release):
        raise ValueError("evaluated release must be a semantic version")
    required = report._required_languages(required_languages)
    if not source_specs or len(source_specs) > MAX_PUBLIC_ARTIFACTS:
        raise ValueError("publication source count is invalid")
    if len(source_specs) != len(required):
        raise ValueError("publication source count must match required languages")
    if not 0 <= coverage_threshold <= 1:
        raise ValueError("coverage threshold must be between 0 and 1")

    sources = []
    for expected, source in source_specs:
        source = Path(source)
        actual = artifact.sha256_file(source, artifact.MAX_ARTIFACT_BYTES)
        if actual != expected:
            raise ValueError("source artifact SHA-256 does not match expected identity")
        sources.append((expected, source, source.stat().st_size))

    loaded = report._load_artifacts([source for _digest, source, _size in sources])
    projected = []
    observed_languages = set()
    total_bytes = 0
    for source_info, loaded_artifact in zip(
        sorted(sources, key=lambda item: str(item[1].resolve())), loaded
    ):
        expected, source, source_bytes = source_info
        document = artifact.load_json(source, artifact.MAX_ARTIFACT_BYTES)
        dataset_value = loaded_artifact["metadata"]["dataset"]
        dataset_path = repo / dataset_value["path"]
        repository_path(dataset_path, repo)
        _payload, dataset_rows = runner.load_dataset(dataset_path)
        artifact.resume_state(document, document["metadata"], dataset_rows=dataset_rows)
        public_document = project_artifact(document)
        languages = {row["language"] for row in public_document["rows"]}
        if len(languages) != 1:
            raise ValueError("each public artifact must contain exactly one language")
        language = next(iter(languages))
        if language in observed_languages:
            raise ValueError("duplicate public artifact language")
        observed_languages.add(language)
        public_bytes = canonical_bytes(public_document)
        if len(public_bytes) > MAX_PUBLIC_ARTIFACT_BYTES:
            raise ValueError("public artifact exceeds per-file byte limit")
        total_bytes += len(public_bytes)
        projected.append({
            "document": public_document,
            "language": language,
            "name": source.name,
            "source_bytes": source_bytes,
            "source_sha256": expected,
            "dataset": dataset_value,
            "bytes": public_bytes,
        })
    if observed_languages != required:
        raise ValueError("public artifact languages do not match required languages")
    if total_bytes > MAX_TOTAL_PUBLIC_BYTES:
        raise ValueError("public artifacts exceed aggregate byte limit")

    report_path = Path(report_path)
    if not report_path.is_absolute():
        report_path = repo / report_path
    repository_path(report_path, repo)
    expected_report = artifact.read_bytes(report_path, report.MAX_TOTAL_ARTIFACT_BYTES, label="report")

    parent = output_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}.", dir=parent))
    try:
        staged_paths = []
        entries = []
        for item in sorted(projected, key=lambda value: (value["language"], value["name"])):
            destination = staging / item["name"]
            artifact.atomic_write_json(
                destination, item["document"], max_bytes=MAX_PUBLIC_ARTIFACT_BYTES
            )
            if artifact.read_bytes(
                destination, MAX_PUBLIC_ARTIFACT_BYTES, label="public artifact"
            ) != item["bytes"]:
                raise ValueError("public artifact serialization is not canonical")
            staged_paths.append(destination)
            final_path = output_dir / item["name"]
            entries.append({
                "bytes": len(item["bytes"]),
                "dataset": item["dataset"],
                "language": item["language"],
                "path": repository_path(final_path, repo),
                "rows": len(item["document"]["rows"]),
                "sha256": hashlib.sha256(item["bytes"]).hexdigest(),
                "source": {
                    "bytes": item["source_bytes"],
                    "sha256": item["source_sha256"],
                },
            })
        rendered = report.render_markdown(staged_paths, sorted(required), coverage_threshold).encode()
        if rendered != expected_report:
            raise ValueError("public artifacts do not regenerate the declared report")
        shared = projected[0]["document"]["metadata"]
        manifest = {
            "schema_version": PUBLICATION_SCHEMA_VERSION,
            "kind": "evergreen-benchmark-decision-publication",
            "evaluated_release": evaluated_release,
            "projection": {
                "name": "structured-decisions",
                "version": PUBLICATION_SCHEMA_VERSION,
                "omitted_fields": ["code", "doc", "func", "missed_angle", "reason", "why"],
            },
            "publication": {
                "coverage_threshold": coverage_threshold,
                "required_languages": sorted(required),
            },
            "provenance": {
                "cli_version": shared["cli_version"],
                "commit": shared["git"]["commit"],
                "judge_sha256": shared["judge"]["sha256"],
                "provider": shared["provider"],
                "settings_sha256": _settings_sha256(shared["settings"]),
                "skill_sha256": shared["skill"]["sha256"],
                "tree": shared["git"]["tree"],
            },
            "artifacts": entries,
            "report": {
                "path": repository_path(report_path, repo),
                "sha256": hashlib.sha256(expected_report).hexdigest(),
            },
        }
        manifest_path = staging / "manifest.json"
        artifact.atomic_write_json(manifest_path, manifest, max_bytes=MAX_PUBLIC_ARTIFACT_BYTES)
        if artifact.read_bytes(
            manifest_path, MAX_PUBLIC_ARTIFACT_BYTES, label="manifest"
        ) != canonical_bytes(manifest):
            raise ValueError("manifest serialization is not canonical")
        _fsync_directory(staging)
        os.replace(staging, output_dir)
        _fsync_directory(parent)
    except Exception:
        if staging.exists():
            shutil.rmtree(staging)
        raise
    return output_dir / "manifest.json"


def _manifest_file(raw_path, repo):
    if not isinstance(raw_path, str) or not raw_path or Path(raw_path).is_absolute():
        raise ValueError("manifest path must be repository-relative")
    path = repo / raw_path
    if repository_path(path, repo) != raw_path:
        raise ValueError("manifest path is not normalized")
    return path


def _hash(value, label):
    if not isinstance(value, str) or not SHA256.fullmatch(value):
        raise ValueError(f"{label} must be lowercase SHA-256")
    return value


def _public_stage(stage, name, required):
    if not isinstance(stage, dict) or stage.get("status") not in STAGE_STATUSES:
        raise ValueError(f"public {name} stage is invalid")
    expected = {"status"} if stage["status"] == "abstain" else {"status", *required}
    if set(stage) != expected:
        raise ValueError(f"public {name} stage fields are invalid")
    if stage["status"] == "abstain":
        return
    if "verdict" in required:
        _verdict(stage["verdict"], label=f"public {name} verdict")
    if "category" in required:
        _category(stage["category"], f"public {name} category")
    if "cracks" in required and type(stage["cracks"]) is not bool:
        raise ValueError("public challenge cracks is invalid")
    if "missed_angle_present" in required and type(stage["missed_angle_present"]) is not bool:
        raise ValueError("public blindspot presence is invalid")


def _validate_public_row(row):
    if not isinstance(row, dict) or set(row) != {
        "id", "language", "label", "category", "got", "trial",
    }:
        raise ValueError("public artifact projection row fields are invalid")
    artifact.validate_benchmark_row(row, require_result=True)
    got = row["got"]
    if set(got) != {"final_status", "final_verdict", "verdict", "category", "contested"}:
        raise ValueError("public artifact projection result fields are invalid")
    status = got["final_status"]
    if status not in RESULT_STATUSES:
        raise ValueError("public final status is invalid")
    _verdict(got["final_verdict"], nullable=status == "abstain", label="public final verdict")
    _verdict(got["verdict"], nullable=status == "abstain", label="public verdict")
    if status == "abstain" and (got["final_verdict"] is not None or got["verdict"] is not None):
        raise ValueError("public abstention contains a verdict")
    _category(got["category"], "public predicted category")
    if type(got["contested"]) is not bool:
        raise ValueError("public contested value is invalid")
    trial = row["trial"]
    if not isinstance(trial, dict) or set(trial) - STAGES:
        raise ValueError("public artifact projection trial fields are invalid")
    if "snap" in trial:
        _public_stage(trial["snap"], "snap", {"verdict", "category"})
    if "challenge" in trial:
        _public_stage(trial["challenge"], "challenge", {"cracks"})
    for name in ("prongs", "prongs_escalated"):
        if name not in trial:
            continue
        prongs = trial[name]
        if not isinstance(prongs, list) or len(prongs) != 3:
            raise ValueError(f"public {name} is invalid")
        roles = set()
        for prong in prongs:
            required = {"role", "verdict"} if prong.get("status") == "ok" else set()
            _public_stage(prong, name, required)
            if prong["status"] == "ok":
                if prong["role"] not in PRONG_ROLES or prong["role"] in roles:
                    raise ValueError(f"public {name} role is invalid")
                roles.add(prong["role"])
    if "blindspot" in trial:
        _public_stage(trial["blindspot"], "blindspot", {"missed_angle_present"})
    if "synthesis" in trial:
        _public_stage(trial["synthesis"], "synthesis", {"verdict", "category"})


def _historical_blob(repo, commit, path):
    try:
        return artifact._git_bytes(repo, "show", f"{commit}:{path}")
    except (OSError, ValueError):
        raise ValueError(f"historical Git blob is unavailable: {path}") from None


def _verify_historical_provenance(repo, metadata):
    commit = metadata["git"]["commit"]
    try:
        artifact._git_bytes(repo, "cat-file", "-e", f"{commit}^{{commit}}")
        tree = artifact._git_bytes(repo, "rev-parse", f"{commit}^{{tree}}").decode().strip()
    except (OSError, UnicodeError, ValueError):
        raise ValueError("historical Git commit is unavailable") from None
    if tree != metadata["git"]["tree"]:
        raise ValueError("historical Git tree does not match provenance")
    skill = metadata["skill"]
    if hashlib.sha256(_historical_blob(repo, commit, skill["path"])).hexdigest() != skill["sha256"]:
        raise ValueError("historical skill SHA-256 does not match provenance")
    judge_files = metadata["judge"].get("files")
    if not judge_files:
        raise ValueError("historical judge files are unavailable")
    for item in judge_files:
        digest = hashlib.sha256(_historical_blob(repo, commit, item["path"])).hexdigest()
        if digest != item["sha256"]:
            raise ValueError("historical judge file SHA-256 does not match provenance")
    encoded = json.dumps(judge_files, sort_keys=True, separators=(",", ":")).encode()
    if hashlib.sha256(encoded).hexdigest() != metadata["judge"]["sha256"]:
        raise ValueError("historical judge SHA-256 does not match provenance")


def verify_publication(manifest_path, repo, report_path):
    """Verify a committed public benchmark package without private artifacts or model calls."""
    repo = Path(repo).resolve()
    manifest_path = Path(manifest_path)
    if not manifest_path.is_absolute():
        manifest_path = repo / manifest_path
    repository_path(manifest_path, repo)
    manifest = artifact.load_json(manifest_path, MAX_PUBLIC_ARTIFACT_BYTES)
    if not isinstance(manifest, dict) or set(manifest) != {
        "schema_version", "kind", "evaluated_release", "projection", "publication",
        "provenance", "artifacts", "report",
    }:
        raise ValueError("public manifest fields are invalid")
    if (manifest["schema_version"] != PUBLICATION_SCHEMA_VERSION or
            manifest["kind"] != "evergreen-benchmark-decision-publication"):
        raise ValueError("public manifest schema is invalid")
    if not re.fullmatch(r"[0-9]+\.[0-9]+\.[0-9]+", manifest["evaluated_release"]):
        raise ValueError("public evaluated release is invalid")
    if manifest["projection"] != {
        "name": "structured-decisions", "version": PUBLICATION_SCHEMA_VERSION,
        "omitted_fields": ["code", "doc", "func", "missed_angle", "reason", "why"],
    }:
        raise ValueError("public projection declaration is invalid")
    publication = manifest["publication"]
    if not isinstance(publication, dict) or set(publication) != {
        "coverage_threshold", "required_languages",
    }:
        raise ValueError("public publication policy is invalid")
    required = report._required_languages(publication["required_languages"])
    threshold = publication["coverage_threshold"]
    if not isinstance(threshold, (int, float)) or isinstance(threshold, bool) or not 0 <= threshold <= 1:
        raise ValueError("public coverage threshold is invalid")

    entries = manifest["artifacts"]
    if (not isinstance(entries, list) or not entries or
            len(entries) > MAX_PUBLIC_ARTIFACTS or len(entries) != len(required)):
        raise ValueError("public manifest artifact count is invalid")
    public_paths = []
    observed_languages = set()
    total_bytes = 0
    entry_by_path = {}
    for entry in entries:
        if not isinstance(entry, dict) or set(entry) != {
            "bytes", "dataset", "language", "path", "rows", "sha256", "source",
        }:
            raise ValueError("public manifest artifact entry is invalid")
        if entry["language"] in observed_languages:
            raise ValueError("duplicate public manifest language")
        observed_languages.add(entry["language"])
        path = _manifest_file(entry["path"], repo)
        if entry["path"] in entry_by_path:
            raise ValueError("duplicate public manifest path")
        entry_by_path[entry["path"]] = entry
        raw = artifact.read_bytes(path, MAX_PUBLIC_ARTIFACT_BYTES, label="public artifact")
        if type(entry["bytes"]) is not int or entry["bytes"] != len(raw):
            raise ValueError("public artifact bytes do not match manifest")
        total_bytes += len(raw)
        if hashlib.sha256(raw).hexdigest() != _hash(entry["sha256"], "public artifact hash"):
            raise ValueError("public artifact SHA-256 does not match manifest")
        if type(entry["rows"]) is not int or entry["rows"] < 0:
            raise ValueError("public artifact row count is invalid")
        source = entry["source"]
        if (not isinstance(source, dict) or set(source) != {"bytes", "sha256"} or
                type(source["bytes"]) is not int or source["bytes"] < 1):
            raise ValueError("private source identity is invalid")
        _hash(source["sha256"], "private source hash")
        dataset = entry["dataset"]
        if not isinstance(dataset, dict) or set(dataset) != {"path", "sha256"}:
            raise ValueError("public dataset identity is invalid")
        _hash(dataset["sha256"], "dataset hash")
        public_paths.append(path)
    if total_bytes > MAX_TOTAL_PUBLIC_BYTES:
        raise ValueError("public artifacts exceed aggregate byte limit")
    if observed_languages != required:
        raise ValueError("public manifest languages do not match policy")

    loaded = report._load_artifacts(public_paths)
    all_ids = set()
    for loaded_artifact in loaded:
        path = loaded_artifact["path"]
        entry = entry_by_path[repository_path(path, repo)]
        document = artifact.load_json(path, MAX_PUBLIC_ARTIFACT_BYTES)
        if len(document["rows"]) != entry["rows"]:
            raise ValueError("public artifact row count does not match manifest")
        for row in document["rows"]:
            _validate_public_row(row)
            if row["id"] in all_ids:
                raise ValueError("duplicate public pair id")
            all_ids.add(row["id"])
        metadata = loaded_artifact["metadata"]
        if metadata["dataset"] != entry["dataset"]:
            raise ValueError("public artifact dataset metadata does not match manifest")
        dataset_path = _manifest_file(entry["dataset"]["path"], repo)
        if artifact.sha256_file(dataset_path, artifact.MAX_DATASET_METADATA_BYTES) != entry["dataset"]["sha256"]:
            raise ValueError("dataset SHA-256 does not match manifest")
        _payload, dataset_rows = runner.load_dataset(dataset_path)
        expected = {row["id"]: row for row in dataset_rows}
        if len(expected) != len(document["rows"]):
            raise ValueError("public artifact does not cover the declared dataset")
        for row in document["rows"]:
            source = expected.get(row["id"])
            if source is None or any(row[key] != source[key] for key in
                                     ("language", "label", "category")):
                raise ValueError("public artifact row does not match declared dataset")

    metadata = loaded[0]["metadata"]
    provenance = manifest["provenance"]
    if not isinstance(provenance, dict) or set(provenance) != {
        "cli_version", "commit", "judge_sha256", "provider", "settings_sha256",
        "skill_sha256", "tree",
    }:
        raise ValueError("public provenance summary is invalid")
    expected_provenance = {
        "cli_version": metadata["cli_version"],
        "commit": metadata["git"]["commit"],
        "judge_sha256": metadata["judge"]["sha256"],
        "provider": metadata["provider"],
        "settings_sha256": _settings_sha256(metadata["settings"]),
        "skill_sha256": metadata["skill"]["sha256"],
        "tree": metadata["git"]["tree"],
    }
    if provenance != expected_provenance:
        raise ValueError("public provenance summary does not match artifacts")
    _verify_historical_provenance(repo, metadata)
    commit = metadata["git"]["commit"]
    for entry in entries:
        dataset = entry["dataset"]
        if hashlib.sha256(_historical_blob(repo, commit, dataset["path"])).hexdigest() != dataset["sha256"]:
            raise ValueError("historical dataset SHA-256 does not match provenance")

    declared_report = manifest["report"]
    if not isinstance(declared_report, dict) or set(declared_report) != {"path", "sha256"}:
        raise ValueError("public report identity is invalid")
    expected_report_path = _manifest_file(declared_report["path"], repo)
    report_path = Path(report_path)
    if not report_path.is_absolute():
        report_path = repo / report_path
    if report_path.resolve() != expected_report_path.resolve():
        raise ValueError("report path does not match manifest")
    report_bytes = artifact.read_bytes(report_path, report.MAX_TOTAL_ARTIFACT_BYTES, label="report")
    if hashlib.sha256(report_bytes).hexdigest() != _hash(declared_report["sha256"], "report hash"):
        raise ValueError("report SHA-256 does not match manifest")
    rendered = report.render_markdown(public_paths, sorted(required), threshold).encode()
    if rendered != report_bytes:
        raise ValueError("public artifacts do not regenerate the report")
    return public_paths


def main(argv=None):
    parser = argparse.ArgumentParser()
    commands = parser.add_subparsers(dest="command", required=True)
    export = commands.add_parser("export")
    export.add_argument("--source", action="append", required=True)
    export.add_argument("--output-dir", type=Path, required=True)
    export.add_argument("--evaluated-release", required=True)
    export.add_argument("--require-language", action="append", required=True)
    export.add_argument("--coverage-threshold", type=float, default=1.0)
    export.add_argument("--report", type=Path, required=True)
    verify = commands.add_parser("verify")
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--repo", type=Path, default=Path("."))
    verify.add_argument("--report", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == "export":
            repo = Path.cwd()
            manifest = export_publication(
                [parse_source(value) for value in args.source], args.output_dir,
                args.evaluated_release, args.require_language, args.coverage_threshold,
                args.report, repo,
            )
            print(f"exported public benchmark manifest: {repository_path(manifest, repo)}")
        else:
            paths = verify_publication(args.manifest, args.repo, args.report)
            print(f"verified public benchmark publication: {len(paths)} artifacts")
    except (OSError, ValueError, json.JSONDecodeError, RecursionError) as error:
        message = " ".join(str(error).split())[:500] or error.__class__.__name__
        print(f"publication error: {message}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
