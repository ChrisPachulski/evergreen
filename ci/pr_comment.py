#!/usr/bin/env python3
"""Render a headless winnow run's raw output into a Markdown PR comment.

Reads `claude -p` output on stdin, extracts the fenced jsonl findings block (tolerant,
line-by-line — same parse shape as eval/score.py), and writes Markdown to stdout, led by a
hidden marker so the driver can upsert one comment. Stdlib only.

Self-check: python3 ci/pr_comment.py --selftest
"""
import json
import sys

MARKER = "<!-- evergreen-report -->"


def load_findings(text):
    """Every stripped line that parses as a JSON object with a 'file' is a finding. A line that
    doesn't parse is skipped — same tolerance as eval/score.py, so a stray prose line never breaks
    the render."""
    findings = []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict) and obj.get("file"):
            findings.append(obj)
    return findings


def _cell(s):
    # Keep table cells single-line and pipe-safe.
    return str(s if s is not None else "").replace("|", "\\|").replace("\n", " ").strip()


def render(text):
    findings = load_findings(text)
    out = [MARKER, "## 🌲 evergreen — doc-drift check", ""]

    if not findings:
        out.append("✅ evergreen: docs still match the code.")
        out.append("")
        return "\n".join(out)

    docs = sorted({_cell(f.get("file")) for f in findings if f.get("file")})
    n, m = len(findings), len(docs)
    out.append(
        f"**{n} finding{'s' if n != 1 else ''} across {m} doc{'s' if m != 1 else ''}** — "
        "each proven against the code. Nothing was rewritten; you keep the call."
    )
    out.append("")
    out.append("| severity | where | what's wrong (cited) | |")
    out.append("|---|---|---|---|")

    order = {"high": 0, "med": 1, "medium": 1, "low": 2}
    for f in sorted(findings, key=lambda x: order.get(str(x.get("severity", "")).lower(), 3)):
        sev = _cell(f.get("severity", "?"))
        line = f.get("line")
        where = _cell(f.get("file"))
        if line not in (None, ""):
            where = f"`{where}:{_cell(line)}`"
        else:
            where = f"`{where}`"
        claim = _cell(f.get("claim"))
        why = _cell(f.get("why"))
        what = f"{claim} — {why}" if claim and why else (claim or why)
        action = _cell(f.get("fix_or_flag", "flag")) or "flag"
        out.append(f"| {sev} | {where} | {what} | {action} |")

    out.append("")
    out.append("<sub>evergreen flags what it can prove; it never fails your build.</sub>")
    out.append("")
    return "\n".join(out)


def _selftest():
    two = """
here is some model preamble that should be ignored
{"severity":"high","category":"in_docs_not_code","file":"README.md","line":42,"claim":"--workers 8","why":"cli.py:12 defines --concurrency, not --workers","fix_or_flag":"fix"}
not json, skip me
{"severity":"low","category":"name_mismatch","file":"docs/usage.md","line":3,"claim":"utils.py","why":"file is helpers.py per helpers.py:1","fix_or_flag":"flag"}
```
"""
    md = render(two)
    assert md.startswith(MARKER), "must lead with the hidden marker"
    assert "2 findings across 2 docs" in md, "summary count wrong:\n" + md
    assert "`README.md:42`" in md and "`docs/usage.md:3`" in md, "rows missing:\n" + md
    assert md.index("high") < md.index("low"), "severity ordering wrong (high before low)"
    assert "\\|" not in md or "|" in md  # sanity: rendered as a table
    assert md.count("\n|") >= 4, "expected a header, divider, and two data rows"

    clean = render("the docs all check out\nno json here")
    assert clean.startswith(MARKER), "clean case must still carry the marker"
    assert "docs still match" in clean, "clean summary wrong:\n" + clean
    assert "| severity |" not in clean, "clean case must not emit a table"

    # pipe-injection in a claim must not break the table
    inj = '{"severity":"med","file":"a.md","line":1,"claim":"a | b","why":"c | d","fix_or_flag":"fix"}'
    assert "a \\| b" in render(inj), "pipe not escaped"

    print("pr_comment selftest: ok")


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--selftest":
        _selftest()
    else:
        sys.stdout.write(render(sys.stdin.read()))
