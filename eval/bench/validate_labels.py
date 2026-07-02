#!/usr/bin/env python3
"""Validate derived drift labels with a three-LLM majority vote (CCIBench's method).

Heuristic labels are ~half noise if trusted raw (CCISolver: 45.67% of JITDATA positives
mislabeled), so every derived pair is judged independently by three annotator models with a
NEUTRAL prompt (deliberately not evergreen's ruleset — we don't grade our own exam with our own
rubric). A pair is kept only when >=2/3 annotators confirm the derived label. Reports per-class
confirmation rates, pairwise Cohen's kappa, and Fleiss' kappa.

  python3 eval/bench/validate_labels.py derived.jsonl --out validated.jsonl
  EVAL_CONCURRENCY=8 python3 eval/bench/validate_labels.py derived.jsonl --out validated.jsonl

Annotator setup, honestly: three LLMs, no human pass. Kappa below is inter-LLM agreement, not
human agreement — see RESULTS.md for the caveat.
"""
import argparse
import json
import os
import subprocess
from itertools import combinations
from pathlib import Path

ANNOTATORS = ["claude-fable-5", "claude-opus-4-8", "claude-sonnet-5"]
BATCH = 10  # ponytail: CLI startup dominates per-call latency, so judge 10 pairs per call

HEADER = """You will judge {n} code/documentation pairs for consistency.

For each pair, the question is: does the documentation make any claim that the code contradicts
or fails to deliver? Judge only what the documentation asserts against what the code does. Code
that does MORE than the documentation mentions is NOT an inconsistency by itself. Judge each
pair independently.

Reply with exactly one line of JSON PER PAIR, in order, and nothing else:
{{"id": "<the pair's id>", "verdict": "consistent" | "inconsistent"}}
"""

PAIR_TMPL = """
---
# Pair id: {id}

## Code (`{func}`)
```{lang_lower}
{code}
```

## Documentation
{doc}
"""


def ask_batch(batch, model):
    """Judge a batch of pairs in one CLI call. Returns {id: verdict}."""
    prompt = HEADER.format(n=len(batch)) + "".join(
        PAIR_TMPL.format(id=p["id"], func=p["func"], code=p["code"], doc=p["doc"],
                         lang_lower=p.get("language", "python").lower()) for p in batch)
    try:
        out = subprocess.run(["claude", "-p", prompt, "--model", model, "--allowedTools", ""],
                             capture_output=True, text=True, timeout=1200).stdout
    except subprocess.TimeoutExpired:
        return {}
    got = {}
    for line in out.splitlines():
        line = line.strip().strip("`")
        if line.startswith("{") and '"verdict"' in line:
            try:
                v = json.loads(line)
                if v.get("verdict") in ("consistent", "inconsistent"):
                    got[v.get("id")] = v["verdict"]
            except (json.JSONDecodeError, KeyError):
                pass
    return got


def cohen_kappa(a, b):
    pairs = [(x, y) for x, y in zip(a, b) if x and y]
    if not pairs:
        return float("nan")
    po = sum(x == y for x, y in pairs) / len(pairs)
    cats = {"consistent", "inconsistent"}
    pe = sum((sum(x == c for x, _ in pairs) / len(pairs)) *
             (sum(y == c for _, y in pairs) / len(pairs)) for c in cats)
    return (po - pe) / (1 - pe) if pe < 1 else 1.0


def fleiss_kappa(rows):
    """rows: list of per-item verdict lists (only items with all annotators answering)."""
    full = [r for r in rows if all(r)]
    if not full:
        return float("nan")
    n = len(full[0])
    cats = ["consistent", "inconsistent"]
    counts = [[r.count(c) for c in cats] for r in full]
    p_i = [(sum(c * c for c in row) - n) / (n * (n - 1)) for row in counts]
    p_bar = sum(p_i) / len(full)
    p_j = [sum(row[j] for row in counts) / (len(full) * n) for j in range(len(cats))]
    pe = sum(p * p for p in p_j)
    return (p_bar - pe) / (1 - pe) if pe < 1 else 1.0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("derived_jsonl")
    ap.add_argument("--out", required=True)
    a = ap.parse_args()
    pairs = [json.loads(l) for l in open(a.derived_jsonl) if l.strip()]
    votes_path = Path(a.out).with_suffix(".votes.json")
    votes = json.loads(votes_path.read_text()) if votes_path.exists() else {}
    todo = []
    for m in ANNOTATORS:
        need = [p for p in pairs if votes.get(p["id"], {}).get(m) is None]
        todo += [(need[i:i + BATCH], m) for i in range(0, len(need), BATCH)]
    workers = int(os.environ.get("EVAL_CONCURRENCY", "1"))
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for (batch, m), got in zip(todo, pool.map(lambda t: ask_batch(*t), todo)):
            for p in batch:
                votes.setdefault(p["id"], {})[m] = got.get(p["id"])
            done = sum(1 for pv in votes.values() for x in pv.values() if x)
            print(f"  [{done}/{len(pairs) * len(ANNOTATORS)}] {m:20} batch of {len(batch)}: "
                  f"{len([p for p in batch if got.get(p['id'])])} answered", flush=True)
            votes_path.write_text(json.dumps(votes, indent=1))
    votes_path.write_text(json.dumps(votes, indent=1))

    kept, dropped = [], []
    for p in pairs:
        vs = [votes[p["id"]].get(m) for m in ANNOTATORS]
        confirm = sum(v == p["label"] for v in vs if v)
        (kept if confirm >= 2 else dropped).append(p)  # two-thirds keep rule
    with open(a.out, "w") as f:
        for p in kept:
            f.write(json.dumps(p) + "\n")

    by_label = lambda ps, lab: [p for p in ps if p["label"] == lab]
    print(f"\nkept {len(kept)}/{len(pairs)} "
          f"(inconsistent {len(by_label(kept, 'inconsistent'))}/{len(by_label(pairs, 'inconsistent'))}, "
          f"consistent {len(by_label(kept, 'consistent'))}/{len(by_label(pairs, 'consistent'))})")
    cols = {m: [votes[p["id"]].get(m) for p in pairs] for m in ANNOTATORS}
    for m1, m2 in combinations(ANNOTATORS, 2):
        print(f"  Cohen's kappa {m1} vs {m2}: {cohen_kappa(cols[m1], cols[m2]):.3f}")
    print(f"  Fleiss' kappa (3 annotators): "
          f"{fleiss_kappa([[cols[m][i] for m in ANNOTATORS] for i in range(len(pairs))]):.3f}")


if __name__ == "__main__":
    main()
