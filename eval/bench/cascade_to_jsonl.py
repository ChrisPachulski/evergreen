#!/usr/bin/env python3
"""Convert CASCADE's released dataset (arXiv:2604.19400) to the bench JSONL schema.

CASCADE ships per-commit directories: <lang>/<org>/<repo>/<sha>/<n>/analyzed.json (a list of
method entries) + inconsistency.txt (one True/False label per entry, same order).

  git clone https://github.com/TobiasKiecker/CASCADE
  unzip CASCADE/PaperEvaluation/dataset.zip -d cascade_dataset
  python3 eval/bench/cascade_to_jsonl.py cascade_dataset > eval/bench/cascade.jsonl
"""
import json
import sys
from pathlib import Path


def method_source(entry):
    sig = entry["signature"]
    head = "".join(sig.get("modifier", [])) + (sig.get("returns") or "") + " " + sig["name"]
    return f"{head}({', '.join(sig.get('params', []))}) {entry['code']}"


def main(root):
    for aj in sorted(Path(root).rglob("analyzed.json")):
        entries = json.loads(aj.read_text())
        labels = [l.strip() for l in (aj.parent / "inconsistency.txt").read_text().splitlines()
                  if l.strip()]
        assert len(entries) == len(labels), aj
        rel = aj.parent.relative_to(root)
        for i, (e, lab) in enumerate(zip(entries, labels)):
            print(json.dumps({
                "id": f"{'/'.join(rel.parts[1:])}#{i}",
                "func": e["signature"]["name"],
                "code": method_source(e),
                "doc": e["doc"],
                "label": "inconsistent" if lab == "True" else "consistent",
                "category": None,
                "language": e.get("language", "Java"),
            }))


if __name__ == "__main__":
    main(sys.argv[1])
