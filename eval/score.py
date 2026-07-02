#!/usr/bin/env python3
"""Score a headless winnow run against manifest.tsv. Usage: python3 score.py <run-output.txt>"""
import csv
import json
import os
import sys
from pathlib import Path

HERE = Path(__file__).parent


def load_output(text):
    flags, left = [], []
    for line in text.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        if obj.get("type") == "flag":
            flags.append(obj)
        elif obj.get("type") == "left_alone":
            left.append(obj)
    return flags, left


def base(p):
    return os.path.basename(str(p or ""))


def main():
    text = Path(sys.argv[1]).read_text()
    flags, left = load_output(text)
    rows = list(csv.DictReader(open(HERE / "manifest.tsv"), delimiter="\t"))
    drift = [r for r in rows if r["kind"] == "drift"]
    decoys = [r for r in rows if r["kind"] == "decoy"]
    exempt = [r for r in rows if r["kind"] == "exempt"]

    def hit(row, f):
        return base(row["file"]) == base(f.get("file")) and \
            row["token"].lower() in str(f.get("claim", "")).lower()

    exempt_files = {base(r["file"]) for r in exempt}
    caught = [r for r in drift if any(hit(r, f) for f in flags)]
    fps = [r for r in decoys if any(hit(r, f) for f in flags)]
    violations = [f for f in flags if base(f.get("file")) in exempt_files]
    unmatched = [f for f in flags
                 if not any(hit(r, f) for r in drift + decoys)
                 and base(f.get("file")) not in exempt_files]
    honored = [r for r in exempt if any(base(l.get("file")) == base(r["file"]) for l in left)]
    true_flags = [f for f in flags if any(hit(r, f) for r in drift)]

    print(f"drift caught (recall):   {len(caught)}/{len(drift)}"
          f"  — missed: {', '.join(r['id'] for r in drift if r not in caught) or 'none'}")
    for r in caught:
        print(f"  ✓ {r['id']}  {r['note']}")
    print(f"decoy false positives:   {len(fps)}/{len(decoys)}"
          f"  — {', '.join(r['id'] for r in fps) or 'none'}")
    print(f"exempt-doc violations:   {len(violations)}/{len(exempt)} exempt files flagged")
    print(f"exempt explicitly left alone: {len(honored)}/{len(exempt)}")
    if unmatched:
        print(f"unseeded flags (audit these by hand): {len(unmatched)}")
        for f in unmatched:
            print(f"  ? {f.get('file')}:{f.get('line')} — {f.get('claim')}")
    denom = len(flags)
    if denom:
        print(f"precision (flags matching seeded drift / all flags): {len(true_flags)}/{denom}"
              f" = {len(true_flags) / denom:.2f}")
    print(f"recall: {len(caught) / len(drift):.2f}")
    # Non-zero exit when the run is clearly broken (no parseable output), so CI can notice.
    sys.exit(0 if flags or left else 1)


if __name__ == "__main__":
    main()
