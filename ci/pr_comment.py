#!/usr/bin/env python3
"""Validate an Evergreen result envelope and render its PR comment."""

import argparse
import html
from pathlib import Path
import sys

try:
    from .result_protocol import load_validated_result
except ImportError:  # Direct script execution.
    from result_protocol import load_validated_result


MARKER = "<!-- evergreen-report -->"
MAX_RENDER_TEXT = 500
MARKDOWN_CONTROLS = "\\`*_[]()|"


def _safe(value: object, limit: int = MAX_RENDER_TEXT) -> str:
    text = str(value if value is not None else "").replace("\r", " ").replace("\n", " ")
    if len(text) > limit:
        text = text[: limit - 1] + "…"
    text = html.escape(text, quote=False)
    for char in MARKDOWN_CONTROLS:
        text = text.replace(char, f"\\{char}")
    return text


def _counts(result: dict) -> list[str]:
    claims = result["claims"]
    return [
        "| certified | drift | unverified | total |",
        "|---:|---:|---:|---:|",
        f"| {claims['certified']} | {claims['drift']} | {claims['unverified']} | {claims['total']} |",
    ]


def render_result(result: dict | None, errors: list[str]) -> str:
    """Render a validated result or validation errors as bounded Markdown."""
    inconclusive = result is None or bool(errors) or result.get("status") == "inconclusive"
    lines = [MARKER, "## 🌲 evergreen — documentation review", ""]
    lines.append(f"**Status:** {'⚠️ inconclusive' if inconclusive else '✅ complete'}")

    if result is not None:
        lines.extend(
            [
                f"**Range:** {_safe(result['base'])} → {_safe(result['head'])}",
                "",
                *_counts(result),
                "",
            ]
        )

    if inconclusive:
        lines.append("⚠️ evergreen: review inconclusive — no clean certification was issued.")
    elif result["claims"]["drift"] == 0 and result["claims"]["unverified"] == 0:
        lines.append("✅ evergreen: docs still match the code.")
    else:
        lines.append("Evergreen completed the review; findings or unverified claims require attention.")

    all_errors = list(errors)
    if result is not None:
        all_errors.extend(result.get("errors", []))
    if all_errors:
        lines.extend(["", "### Errors"])
        lines.extend(f"- {_safe(error)}" for error in all_errors[:100])

    if result is not None and result.get("findings"):
        lines.extend(
            [
                "",
                "### Findings",
                "",
                "| severity | citations | claim | why | action |",
                "|---|---|---|---|---|",
            ]
        )
        for finding in result["findings"]:
            citations = (
                f"{finding['doc_path']}:{finding['doc_line']} ↔ "
                f"{finding['code_path']}:{finding['code_line']}"
            )
            lines.append(
                "| "
                + " | ".join(
                    [
                        _safe(finding["severity"]),
                        _safe(citations),
                        _safe(finding["claim"]),
                        _safe(finding["why"]),
                        _safe(finding["fix_or_flag"]),
                    ]
                )
                + " |"
            )

    if result is not None and result.get("unverified"):
        lines.extend(
            [
                "",
                "### Unverified claims",
                "",
                "| citation | claim | reason |",
                "|---|---|---|",
            ]
        )
        for item in result["unverified"]:
            citation = f"{item['doc_path']}:{item['doc_line']}"
            lines.append(
                f"| {_safe(citation)} | {_safe(item['claim'])} | {_safe(item['reason'])} |"
            )

    if result is not None:
        runtime = result["runtime"]
        lines.extend(
            [
                "",
                "---",
                f"<sub>provider: {_safe(runtime['provider'])} · model: {_safe(runtime['model'])} · "
                f"CLI: {_safe(runtime['cli_version'])}</sub>",
            ]
        )
    lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    argv = sys.argv[1:] if argv is None else argv
    if argv == ["--selftest"]:
        print("pr_comment selftest moved to tests.test_pr_comment")
        return 0

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", required=True)
    args = parser.parse_args(argv)

    result, errors = load_validated_result(sys.stdin.read(), args.repo, args.base, args.head)
    sys.stdout.write(render_result(result, errors))
    return 2 if result is None or errors or result["status"] == "inconclusive" else 0


if __name__ == "__main__":
    raise SystemExit(main())
