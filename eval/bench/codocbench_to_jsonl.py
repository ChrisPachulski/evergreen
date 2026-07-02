#!/usr/bin/env python3
"""Derive a natural-prevalence doc-drift set from CoDocBench (arXiv:2502.00519).

CoDocBench rows are wild coupled code+docstring changes (4,573 Python functions, top-200 PyPI
projects) with no drift labels. We derive candidates:

  (old docstring, new code)  ->  candidate INCONSISTENT — the doc that lagged the code change
  (new docstring, new code)  ->  candidate CONSISTENT control, drawn from DISJOINT rows

Prevalence is imposed at ~10/90 (CASCADE's measured natural rate); candidates are then
label-validated by a three-LLM majority vote (validate_labels.py) — heuristic labels are ~half
noise if trusted raw (CCISolver measured 45.67% mislabeled positives in JITDATA).

  python3 eval/bench/codocbench_to_jsonl.py codocbench/dataset/codocbench.jsonl \
      --pos 60 --neg 540 --seed 0 > codocbench-derived.jsonl
"""
import argparse
import json
import re


def norm(s):
    return re.sub(r"\s+", " ", s or "").strip().lower()


def usable(row):
    old, new = row["version_data"][0], row["version_data"][1]
    return (not row.get("whitespace_only_docstring")
            and norm(old["docstring"]) != norm(new["docstring"])
            and norm(old["docstring"]) and norm(new["docstring"])
            and old["code"] != new["code"]
            and len(new["code"]) < 6000)


def pair(row, i, which, label):
    new = row["version_data"][1]
    doc = row["version_data"][0]["docstring"] if which == "old" else new["docstring"]
    return {"id": f"{row['owner']}/{row['project']}/{row['function']}#{i}-{which}",
            "func": row["function"], "code": new["code"], "doc": doc,
            "label": label, "category": None, "language": "Python"}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("codocbench_jsonl")
    ap.add_argument("--pos", type=int, default=60)
    ap.add_argument("--neg", type=int, default=540)
    ap.add_argument("--seed", type=int, default=0)
    a = ap.parse_args()
    rows = [json.loads(l) for l in open(a.codocbench_jsonl) if l.strip()]
    rows = [r for i, r in enumerate(rows) if usable(r)]
    import random
    rng = random.Random(a.seed)
    rng.shuffle(rows)
    assert len(rows) >= a.pos + a.neg, f"only {len(rows)} usable rows"
    for i, r in enumerate(rows[:a.pos]):
        print(json.dumps(pair(r, i, "old", "inconsistent")))
    for i, r in enumerate(rows[a.pos:a.pos + a.neg]):
        print(json.dumps(pair(r, a.pos + i, "new", "consistent")))


if __name__ == "__main__":
    main()
