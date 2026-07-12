#!/usr/bin/env python3
"""Generate deterministic, provenance-checked benchmark reports."""

import argparse
import hashlib
import html
import json
from pathlib import Path

try:
    from . import run_bench
    from .artifact import load_json
except ImportError:  # Direct script execution.
    import run_bench
    from artifact import load_json

MAX_ARTIFACTS = 64
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
MAX_ROWS = 100_000
MAX_LANGUAGE_CHARS = 128
REQUIRED_METADATA = ("dataset", "skill", "judge", "git", "cli_version", "settings")


def _safe_text(value):
    text = " ".join(str(value).split())
    text = html.escape(text, quote=True)
    for character in "\\`*_[]|":
        text = text.replace(character, "\\" + character)
    return text


def _is_hex(value, lengths):
    return (isinstance(value, str) and len(value) in lengths and
            all(character in "0123456789abcdef" for character in value.lower()))


def _validate_metadata(metadata):
    if not isinstance(metadata, dict) or any(key not in metadata for key in REQUIRED_METADATA):
        raise ValueError("unavailable provenance: required metadata is missing")
    for key in ("dataset", "skill", "judge"):
        value = metadata[key]
        if not isinstance(value, dict) or not isinstance(value.get("path"), str):
            raise ValueError(f"unavailable provenance: {key}")
        if not value["path"] or len(value["path"]) > 4096 or not _is_hex(value.get("sha256"), {64}):
            raise ValueError(f"invalid provenance: {key}")
    git = metadata["git"]
    if (not isinstance(git, dict) or git.get("dirty") is None or
            any(git.get(key) in (None, "", "unavailable") for key in
                ("commit", "tree", "status_sha256", "diff_sha256", "untracked_sha256"))):
        raise ValueError("unavailable provenance: git identity")
    if not _is_hex(git["commit"], {40, 64}) or not _is_hex(git["tree"], {40, 64}):
        raise ValueError("invalid provenance: git commit or tree")
    if any(not _is_hex(git[key], {64}) for key in
           ("status_sha256", "diff_sha256", "untracked_sha256")):
        raise ValueError("invalid provenance: git working tree hashes")
    if metadata["cli_version"] in (None, "", "unavailable"):
        raise ValueError("unavailable provenance: CLI version")
    if not isinstance(metadata["cli_version"], str) or len(metadata["cli_version"]) > 4096:
        raise ValueError("invalid provenance: CLI version")
    if not isinstance(metadata["settings"], dict):
        raise ValueError("unavailable provenance: settings")


def _compatibility_identity(metadata):
    return {key: value for key, value in metadata.items() if key != "dataset"}


def _load_artifacts(paths):
    if len(paths) > MAX_ARTIFACTS:
        raise ValueError(f"too many artifacts (maximum {MAX_ARTIFACTS})")
    artifacts = []
    row_count = 0
    seen_ids = set()
    compatibility = None
    for path in sorted((Path(path) for path in paths), key=lambda item: str(item.resolve())):
        document = load_json(path, MAX_ARTIFACT_BYTES)
        if isinstance(document, list):
            raise ValueError("legacy artifact provenance is unknown; publication refused")
        if not isinstance(document, dict) or document.get("schema_version") != 1:
            raise ValueError("unsupported artifact schema; publication refused")
        metadata = document.get("metadata")
        _validate_metadata(metadata)
        identity = _compatibility_identity(metadata)
        if compatibility is None:
            compatibility = identity
        elif identity != compatibility:
            raise ValueError("incompatible provenance across artifacts")
        rows = document.get("rows")
        if not isinstance(rows, list):
            raise ValueError("artifact rows must be a list")
        timing = document.get("timing")
        if (not isinstance(timing, dict) or not isinstance(timing.get("started_at"), str) or
                not isinstance(timing.get("elapsed_seconds"), (int, float)) or
                isinstance(timing.get("elapsed_seconds"), bool) or
                timing["elapsed_seconds"] < 0):
            raise ValueError("artifact timing is unavailable or invalid")
        if "provider_usage" in document and not isinstance(document["provider_usage"], dict):
            raise ValueError("artifact provider usage is invalid")
        row_count += len(rows)
        if row_count > MAX_ROWS:
            raise ValueError(f"too many rows (maximum {MAX_ROWS})")
        for row in rows:
            if not isinstance(row, dict) or not isinstance(row.get("id"), str) or not row["id"]:
                raise ValueError("every artifact row must have a non-empty string id")
            language = row.get("language", "unknown")
            if not isinstance(language, str) or not language or len(language) > MAX_LANGUAGE_CHARS:
                raise ValueError("every artifact row must have a bounded string language")
            if row.get("label") not in ("consistent", "inconsistent") or "category" not in row:
                raise ValueError("every artifact row must have a valid label and category")
            if not isinstance(row.get("got"), dict):
                raise ValueError("every artifact row must have a result object")
            if row["id"] in seen_ids:
                raise ValueError(f"duplicate pair id: {_safe_text(row['id'])}")
            seen_ids.add(row["id"])
        artifacts.append({"path": path, "metadata": metadata, "rows": rows})
    if not artifacts:
        raise ValueError("no artifacts supplied")
    return artifacts


def _percent(value):
    return f"{value:.1%}"


def _metric(value):
    return "unavailable" if value is None else f"{value:.3f}"


def _build_report(paths, coverage_threshold):
    if not 0 <= coverage_threshold <= 1:
        raise ValueError("coverage threshold must be between 0 and 1")
    artifacts = _load_artifacts(paths)
    rows = [row for artifact in artifacts for row in artifact["rows"]]
    languages = sorted({row.get("language", "unknown") for row in rows})
    provenance = artifacts[0]["metadata"]
    git = provenance["git"]
    lines = [
        "# Evergreen benchmark report",
        "",
        "Publication status: **PASS**.",
        "",
        f"Required completion coverage: **{_percent(coverage_threshold)}**.",
        "",
        "### Provenance",
        "",
        "| Input | Identity |",
        "|---|---|",
        f"| Skill SHA-256 | `{_safe_text(provenance['skill']['sha256'])}` |",
        f"| Judge SHA-256 | `{_safe_text(provenance['judge']['sha256'])}` |",
        f"| Git commit | `{_safe_text(git['commit'])}` |",
        f"| Git tree | `{_safe_text(git['tree'])}` |",
        f"| Git dirty | `{str(git['dirty']).lower()}` |",
        f"| Git status SHA-256 | `{_safe_text(git['status_sha256'])}` |",
        f"| Git diff SHA-256 | `{_safe_text(git['diff_sha256'])}` |",
        f"| Git untracked SHA-256 | `{_safe_text(git['untracked_sha256'])}` |",
        f"| CLI version | {_safe_text(provenance['cli_version'])} |",
        "| Settings SHA-256 | `"
        + hashlib.sha256(json.dumps(
            provenance["settings"], sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest()
        + "` |",
    ]
    for artifact in artifacts:
        dataset = artifact["metadata"]["dataset"]
        lines.append(
            f"| Dataset {_safe_text(dataset['path'])} | `{_safe_text(dataset['sha256'])}` |"
        )
    passed_all = bool(languages)
    for language in languages:
        language_rows = [row for row in rows if row.get("language", "unknown") == language]
        metrics = run_bench.score(run_bench.rows_from_transcript(language_rows))
        passed = metrics["completion_rate"] >= coverage_threshold
        passed_all = passed_all and passed
        lines.extend([
            "",
            f"## {_safe_text(language)}",
            "",
            f"Coverage: **{_percent(metrics['completion_rate'])}** — "
            f"**{'PASS' if passed else 'FAIL'}**.",
            "",
            "| Coverage | Count |",
            "|---|---:|",
            f"| Attempted | {metrics['attempted']} |",
            f"| Completed | {metrics['completed']} |",
            f"| Abstained | {metrics['abstained']} |",
            "",
            "| Core result | Value |",
            "|---|---:|",
            f"| TP | {metrics['tp']} |",
            f"| FP | {metrics['fp']} |",
            f"| FN | {metrics['fn']} |",
            f"| TN | {metrics['tn']} |",
            f"| Precision | {_metric(metrics['precision'])} |",
            f"| Recall | {_metric(metrics['recall'])} |",
            f"| F1 | {_metric(metrics['f1'])} |",
            f"| Specificity | {_metric(metrics['specificity'])} |",
            f"| Accuracy | {_metric(metrics['accuracy'])} |",
            "",
            "| Under-promise (informational) | Count |",
            "|---|---:|",
            f"| Attempted | {metrics['under_attempted']} |",
            f"| Completed | {metrics['under_completed']} |",
            f"| Abstained | {metrics['under_abstained']} |",
            f"| Flagged | {metrics['under_flagged']} |",
        ])
    if not passed_all:
        lines[2] = "Publication status: **FAIL**."
    return "\n".join(lines) + "\n", passed_all


def render_markdown(paths, coverage_threshold=1.0):
    return _build_report(paths, coverage_threshold)[0]


def coverage_passes(paths, coverage_threshold):
    return _build_report(paths, coverage_threshold)[1]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("artifacts", nargs="+")
    parser.add_argument("--markdown", type=Path, required=True)
    parser.add_argument("--coverage-threshold", type=float, default=1.0)
    args = parser.parse_args(argv)
    try:
        markdown, passed = _build_report(args.artifacts, args.coverage_threshold)
    except (OSError, ValueError, json.JSONDecodeError) as error:
        markdown = (
            "# Evergreen benchmark report\n\nPublication status: **FAIL**.\n\n"
            f"Reason: {_safe_text(error)}.\n"
        )
        passed = False
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(markdown)
    return 0 if passed else 2


if __name__ == "__main__":
    raise SystemExit(main())
