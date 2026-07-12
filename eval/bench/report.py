#!/usr/bin/env python3
"""Generate deterministic, coverage-gated Markdown benchmark reports."""

import argparse
import json
from pathlib import Path

try:
    from . import run_bench
except ImportError:  # Direct script execution.
    import run_bench


def _load_rows(paths):
    rows = []
    for path in sorted((Path(path) for path in paths), key=lambda item: str(item.resolve())):
        rows.extend(run_bench.artifact_rows(json.loads(path.read_text())))
    return rows


def _percent(value):
    return f"{value:.1%}"


def _metric(value):
    return "unavailable" if value is None else f"{value:.3f}"


def render_markdown(paths, coverage_threshold=1.0):
    """Render one independently scored section per language."""
    if not 0 <= coverage_threshold <= 1:
        raise ValueError("coverage threshold must be between 0 and 1")
    rows = _load_rows(paths)
    languages = sorted({row.get("language", "unknown") for row in rows})
    lines = [
        "# Evergreen benchmark report",
        "",
        f"Required completion coverage: **{_percent(coverage_threshold)}**.",
    ]
    for language in languages:
        language_rows = [row for row in rows if row.get("language", "unknown") == language]
        metrics = run_bench.score(run_bench.rows_from_transcript(language_rows))
        passed = metrics["completion_rate"] >= coverage_threshold
        lines.extend([
            "",
            f"## {language}",
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
    return "\n".join(lines) + "\n"


def coverage_passes(paths, coverage_threshold):
    rows = _load_rows(paths)
    languages = sorted({row.get("language", "unknown") for row in rows})
    return bool(languages) and all(
        run_bench.score(run_bench.rows_from_transcript([
            row for row in rows if row.get("language", "unknown") == language
        ]))["completion_rate"] >= coverage_threshold
        for language in languages
    )


def main(argv=None):
    parser = argparse.ArgumentParser()
    parser.add_argument("artifacts", nargs="+")
    parser.add_argument("--markdown", type=Path, required=True)
    parser.add_argument("--coverage-threshold", type=float, default=1.0)
    args = parser.parse_args(argv)
    try:
        markdown = render_markdown(args.artifacts, args.coverage_threshold)
    except ValueError as error:
        parser.error(str(error))
    args.markdown.parent.mkdir(parents=True, exist_ok=True)
    args.markdown.write_text(markdown)
    return 0 if coverage_passes(args.artifacts, args.coverage_threshold) else 2


if __name__ == "__main__":
    raise SystemExit(main())
