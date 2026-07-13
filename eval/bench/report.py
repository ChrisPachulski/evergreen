#!/usr/bin/env python3
"""Generate deterministic, provenance-checked benchmark reports."""

import argparse
import hashlib
import html
import json
import math
from pathlib import Path

try:
    from . import metrics
    from .artifact import load_json, valid_iso_time, validate_benchmark_row, validate_usage
except ImportError:  # Direct script execution.
    import metrics
    from artifact import load_json, valid_iso_time, validate_benchmark_row, validate_usage

MAX_ARTIFACTS = 64
MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
# Keep aggregate ingestion well below the 64-artifact theoretical maximum (4 GiB).
MAX_TOTAL_ARTIFACT_BYTES = 128 * 1024 * 1024
MAX_ROWS = 100_000
MAX_LANGUAGES = 64
MAX_LANGUAGE_CHARS = 128
REQUIRED_METADATA = (
    "dataset", "provider", "skill", "judge", "git", "cli_version", "settings",
)


def _safe_text(value):
    text = " ".join(str(value).split())
    text = html.escape(text, quote=True)
    for character in "\\`*_[]|":
        text = text.replace(character, "\\" + character)
    return text


def _is_hex(value, lengths):
    return (isinstance(value, str) and len(value) in lengths and
            all(character in "0123456789abcdef" for character in value.lower()))


def _strict_json(value):
    if isinstance(value, dict):
        return all(isinstance(key, str) and _strict_json(item) for key, item in value.items())
    if isinstance(value, list):
        return all(_strict_json(item) for item in value)
    if isinstance(value, float):
        return math.isfinite(value)
    return value is None or isinstance(value, (str, int, bool))


def _validate_metadata(metadata):
    if not isinstance(metadata, dict) or any(key not in metadata for key in REQUIRED_METADATA):
        raise ValueError("unavailable provenance: required metadata is missing")
    if not _strict_json(metadata):
        raise ValueError("invalid provenance: metadata types")
    for key in ("dataset", "skill", "judge"):
        value = metadata[key]
        if not isinstance(value, dict) or not isinstance(value.get("path"), str):
            raise ValueError(f"unavailable provenance: {key}")
        if not value["path"] or len(value["path"]) > 4096 or not _is_hex(value.get("sha256"), {64}):
            raise ValueError(f"invalid provenance: {key}")
    judge_files = metadata["judge"].get("files")
    if judge_files is not None:
        if (not isinstance(judge_files, list) or not judge_files or
                any(not isinstance(item, dict) or set(item) != {"path", "sha256"} or
                    not isinstance(item["path"], str) or not item["path"] or
                    not _is_hex(item["sha256"], {64}) for item in judge_files)):
            raise ValueError("invalid provenance: judge files")
        paths = [item["path"] for item in judge_files]
        if paths != sorted(set(paths)):
            raise ValueError("invalid provenance: judge files")
    git = metadata["git"]
    if (not isinstance(git, dict) or type(git.get("dirty")) is not bool or
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
    if (not isinstance(metadata["cli_version"], str) or
            not metadata["cli_version"].strip() or len(metadata["cli_version"]) > 4096):
        raise ValueError("invalid provenance: CLI version")
    if not isinstance(metadata["settings"], dict):
        raise ValueError("unavailable provenance: settings")
    if metadata["provider"] not in ("claude", "codex"):
        raise ValueError("invalid provenance: provider")


def _compatibility_identity(metadata):
    return {key: value for key, value in metadata.items() if key != "dataset"}


def _load_artifacts(paths):
    if len(paths) > MAX_ARTIFACTS:
        raise ValueError(f"too many artifacts (maximum {MAX_ARTIFACTS})")
    paths = sorted((Path(path) for path in paths), key=lambda item: str(item.resolve()))
    sizes = [path.stat().st_size for path in paths]
    if any(size > MAX_ARTIFACT_BYTES for size in sizes):
        raise ValueError(f"artifact too large (maximum {MAX_ARTIFACT_BYTES} bytes)")
    if sum(sizes) > MAX_TOTAL_ARTIFACT_BYTES:
        raise ValueError(
            f"total artifact bytes exceed {MAX_TOTAL_ARTIFACT_BYTES} byte publication limit"
        )
    artifacts = []
    row_count = 0
    seen_ids = set()
    compatibility = None
    for path in paths:
        document = load_json(path, MAX_ARTIFACT_BYTES)
        if isinstance(document, list):
            raise ValueError("legacy artifact provenance is unknown; publication refused")
        if (not isinstance(document, dict) or type(document.get("schema_version")) is not int or
                document["schema_version"] != 1):
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
        if (not isinstance(timing, dict) or not valid_iso_time(timing.get("started_at")) or
                not isinstance(timing.get("elapsed_seconds"), (int, float)) or
                isinstance(timing.get("elapsed_seconds"), bool) or
                not math.isfinite(timing["elapsed_seconds"]) or timing["elapsed_seconds"] < 0):
            raise ValueError("artifact timing is unavailable or invalid")
        if "provider_usage" in document and not isinstance(document["provider_usage"], dict):
            raise ValueError("artifact provider usage is invalid")
        if "provider_usage" in document:
            validate_usage(document["provider_usage"])
        row_count += len(rows)
        if row_count > MAX_ROWS:
            raise ValueError(f"too many rows (maximum {MAX_ROWS})")
        for row in rows:
            validate_benchmark_row(row, require_result=True)
            language = row.get("language", "unknown")
            if not isinstance(language, str) or not language or len(language) > MAX_LANGUAGE_CHARS:
                raise ValueError("every artifact row must have a bounded string language")
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


def _required_languages(values):
    if type(values) not in (list, tuple) or not values:
        raise ValueError("required languages must be explicitly declared as a non-empty list")
    if any(not isinstance(value, str) or not value.strip() for value in values):
        raise ValueError("required languages must contain non-empty strings")
    if any(len(value) > MAX_LANGUAGE_CHARS for value in values):
        raise ValueError(
            f"required language exceeds {MAX_LANGUAGE_CHARS} characters"
        )
    if len(values) > MAX_LANGUAGES:
        raise ValueError(f"too many required languages (maximum {MAX_LANGUAGES})")
    if len(set(values)) != len(values):
        raise ValueError("required languages must not contain duplicates")
    return set(values)


def _build_report(
    paths, required_languages, coverage_threshold, decision_threshold=0.0,
    precision_threshold=0.0, recall_threshold=0.0, f1_threshold=0.0,
):
    thresholds = {
        "coverage": coverage_threshold,
        "decision": decision_threshold,
        "precision": precision_threshold,
        "recall": recall_threshold,
        "f1": f1_threshold,
    }
    for name, value in thresholds.items():
        if not 0 <= value <= 1:
            raise ValueError(f"{name} threshold must be between 0 and 1")
    required = _required_languages(required_languages)
    artifacts = _load_artifacts(paths)
    rows = [row for artifact in artifacts for row in artifact["rows"]]
    languages = sorted({row.get("language", "unknown") for row in rows})
    observed = set(languages)
    mismatch = []
    if required - observed:
        mismatch.append(
            "missing artifacts for required languages: "
            + ", ".join(_safe_text(value) for value in sorted(required - observed))
        )
    if observed - required:
        mismatch.append(
            "undeclared artifacts for languages: "
            + ", ".join(_safe_text(value) for value in sorted(observed - required))
        )
    if mismatch:
        raise ValueError("; ".join(mismatch))
    provenance = artifacts[0]["metadata"]
    git = provenance["git"]
    lines = [
        "# Evergreen benchmark report",
        "",
        "Publication status: **PASS**.",
        "",
        f"Required provider completion: **{_percent(coverage_threshold)}**.",
        f"Required semantic decision coverage: **{_percent(decision_threshold)}**.",
        f"Required precision / recall / F1: **{_percent(precision_threshold)} / "
        f"{_percent(recall_threshold)} / {_percent(f1_threshold)}**.",
        f"Required languages: **{', '.join(_safe_text(value) for value in sorted(required))}**.",
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
        f"| Provider | {_safe_text(provenance['provider'])} |",
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
        language_metrics = metrics.score(metrics.rows_from_transcript(language_rows))
        gate_values = {
            "provider coverage": language_metrics["provider_completion_rate"],
            "decision coverage": language_metrics["decision_rate"],
            "precision": language_metrics["precision"],
            "recall": language_metrics["recall"],
            "F1": language_metrics["f1"],
        }
        gate_thresholds = {
            "provider coverage": coverage_threshold,
            "decision coverage": decision_threshold,
            "precision": precision_threshold,
            "recall": recall_threshold,
            "F1": f1_threshold,
        }
        gate_passes = {
            name: threshold == 0 or (gate_values[name] is not None and
                                     gate_values[name] >= threshold)
            for name, threshold in gate_thresholds.items()
        }
        passed = all(gate_passes.values())
        passed_all = passed_all and passed
        lines.extend([
            "",
            f"## {_safe_text(language)}",
            "",
            f"Provider coverage: **{_percent(language_metrics['provider_completion_rate'])}** — "
            f"**{'PASS' if gate_passes['provider coverage'] else 'FAIL'}**.",
            f"Decision coverage: **{_percent(language_metrics['decision_rate'])}** — "
            f"**{'PASS' if gate_passes['decision coverage'] else 'FAIL'}**.",
            "",
            "| Provider coverage | Count |",
            "|---|---:|",
            f"| Attempted | {language_metrics['attempted']} |",
            f"| Completed | {language_metrics['provider_completed']} |",
            f"| Abstained | {language_metrics['provider_abstained']} |",
            "",
            "| Semantic coverage | Count |",
            "|---|---:|",
            f"| Decided | {language_metrics['decided']} |",
            f"| Unverified | {language_metrics['unverified']} |",
            "",
            "| Core result | Value |",
            "|---|---:|",
            f"| TP | {language_metrics['tp']} |",
            f"| FP | {language_metrics['fp']} |",
            f"| FN | {language_metrics['fn']} |",
            f"| TN | {language_metrics['tn']} |",
            f"| Precision | {_metric(language_metrics['precision'])} |",
            f"| Recall | {_metric(language_metrics['recall'])} |",
            f"| F1 | {_metric(language_metrics['f1'])} |",
            f"| Specificity | {_metric(language_metrics['specificity'])} |",
            f"| Accuracy | {_metric(language_metrics['accuracy'])} |",
            "",
            "| Quality gate | Required | Observed | Result |",
            "|---|---:|---:|---:|",
            *[
                f"| {_safe_text(name)} | {_percent(gate_thresholds[name])} | "
                f"{_metric(gate_values[name])} | "
                f"{'PASS' if gate_passes[name] else 'FAIL'} |"
                for name in gate_thresholds
            ],
            "",
            "| Under-promise (informational) | Count |",
            "|---|---:|",
            f"| Attempted | {language_metrics['under_attempted']} |",
            f"| Completed | {language_metrics['under_completed']} |",
            f"| Abstained | {language_metrics['under_abstained']} |",
            f"| Flagged | {language_metrics['under_flagged']} |",
        ])
    if not passed_all:
        lines[2] = "Publication status: **FAIL**."
    return "\n".join(lines) + "\n", passed_all


def render_markdown(
    paths, required_languages, coverage_threshold=1.0, decision_threshold=0.0,
    precision_threshold=0.0, recall_threshold=0.0, f1_threshold=0.0,
):
    return _build_report(
        paths, required_languages, coverage_threshold, decision_threshold,
        precision_threshold, recall_threshold, f1_threshold,
    )[0]


def coverage_passes(
    paths, required_languages, coverage_threshold, decision_threshold=0.0,
    precision_threshold=0.0, recall_threshold=0.0, f1_threshold=0.0,
):
    return _build_report(
        paths, required_languages, coverage_threshold, decision_threshold,
        precision_threshold, recall_threshold, f1_threshold,
    )[1]


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("artifacts", nargs="+")
    parser.add_argument("--markdown", type=Path, required=True)
    parser.add_argument("--require-language", action="append")
    parser.add_argument("--coverage-threshold", type=float, default=1.0)
    parser.add_argument("--decision-threshold", type=float, default=0.0)
    parser.add_argument("--precision-threshold", type=float, default=0.0)
    parser.add_argument("--recall-threshold", type=float, default=0.0)
    parser.add_argument("--f1-threshold", type=float, default=0.0)
    args = parser.parse_args(argv)
    try:
        markdown, passed = _build_report(
            args.artifacts, args.require_language, args.coverage_threshold,
            args.decision_threshold, args.precision_threshold,
            args.recall_threshold, args.f1_threshold,
        )
    except RecursionError:
        markdown = (
            "# Evergreen benchmark report\n\nPublication status: **FAIL**.\n\n"
            "Reason: artifact nesting exceeds safe limit.\n"
        )
        passed = False
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
