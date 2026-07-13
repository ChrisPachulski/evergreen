#!/usr/bin/env python3
"""Convert CASCADE's released dataset (arXiv:2604.19400) to the bench JSONL schema.

CASCADE ships per-commit directories: <lang>/<org>/<repo>/<sha>/<n>/analyzed.json (a list of
method entries) + inconsistency.txt (one True/False label per entry, same order).

  git clone https://github.com/TobiasKiecker/CASCADE
  unzip CASCADE/PaperEvaluation/dataset.zip -d cascade_dataset
  python3 eval/bench/cascade_to_jsonl.py cascade_dataset > eval/bench/cascade.jsonl
"""
import argparse
import json
from pathlib import Path

try:
    from .java_context import PROTOCOL, derive_context
except ImportError:  # Direct script execution.
    from java_context import PROTOCOL, derive_context


def method_source(entry):
    sig = entry["signature"]
    head = "".join(sig.get("modifier", [])) + (sig.get("returns") or "") + " " + sig["name"]
    return f"{head}({', '.join(sig.get('params', []))}) {entry['code']}"


def converted_rows(root, mirror_root=None):
    for aj in sorted(Path(root).rglob("analyzed.json")):
        entries = json.loads(aj.read_text())
        labels = [l.strip() for l in (aj.parent / "inconsistency.txt").read_text().splitlines()
                  if l.strip()]
        assert len(entries) == len(labels), aj
        rel = aj.parent.relative_to(root)
        for i, (e, lab) in enumerate(zip(entries, labels)):
            row = {
                "id": f"{'/'.join(rel.parts[1:])}#{i}",
                "func": e["signature"]["name"],
                "code": method_source(e),
                "doc": e["doc"],
                "label": "inconsistent" if lab == "True" else "consistent",
                "category": None,
                "language": e.get("language", "Java"),
            }
            if mirror_root is not None:
                row["context"] = derive_context(row, mirror_root)
            yield row


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("root", type=Path)
    parser.add_argument("--context-protocol", choices=("none", PROTOCOL), default="none")
    parser.add_argument("--mirror-root", type=Path)
    args = parser.parse_args(argv)
    if (args.context_protocol == PROTOCOL) != (args.mirror_root is not None):
        raise ValueError(f"{PROTOCOL} requires --mirror-root; none forbids it")
    mirror = args.mirror_root if args.context_protocol == PROTOCOL else None
    for row in converted_rows(args.root, mirror):
        print(json.dumps(row))


if __name__ == "__main__":
    main()
