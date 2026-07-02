#!/usr/bin/env python3
"""Benchmark evergreen against a labeled code/doc-consistency dataset (DocPrism/CASCADE schema).

Each dataset line: {"id", "func", "code", "doc", "label": "consistent"|"inconsistent",
                    "category": null|"direct-mismatch"|"over-promise"|"under-promise"}

For each pair we hand the model the code + doc and the shipped SKILL ruleset, ask for a
consistent/inconsistent verdict, and score against the label. Positive class = inconsistent.

  python3 eval/bench/run_bench.py                 # run the shipped dataset
  EVAL_MODEL=claude-haiku-4-5-20251001 python3 eval/bench/run_bench.py
  python3 eval/bench/run_bench.py --dataset path/to/docprism.jsonl   # when it releases
  python3 eval/bench/run_bench.py --selftest      # prove the scoring math, no API calls

evergreen deliberately treats under-promise (code does more than the doc says) as
informational, not drift — so under-promise pairs are scored SEPARATELY, not as misses.
"""
import json
import os
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).parent
SKILL = HERE.parent.parent / "skills" / "evergreen" / "SKILL.md"
CORE_CATEGORIES = {None, "direct-mismatch", "over-promise"}


def skill_body():
    lines = SKILL.read_text().splitlines()
    if lines and lines[0].strip() == "---":
        end = next(i for i in range(1, len(lines)) if lines[i].strip() == "---")
        lines = lines[end + 1:]
    return "\n".join(lines)


def judge_prompt(pair):
    return f"""{skill_body()}

# Task
Judge whether this documentation is consistent with the code it describes, per the ruleset
above. Prove any inconsistency against the code. "Consistent" means the doc makes no claim the
code contradicts or fails to deliver; extra undocumented behavior is not an inconsistency.

## Code (`{pair['func']}`)
```python
{pair['code']}
```

## Documentation
{pair['doc']}

Reply with exactly one line of JSON and nothing else:
{{"id": "{pair['id']}", "verdict": "consistent" | "inconsistent", "category": "direct-mismatch" | "over-promise" | "under-promise" | null, "why": "<one line citing the code>"}}"""


def run_pair(pair, model):
    cmd = ["claude", "-p", judge_prompt(pair), "--allowedTools", ""]
    if model:
        cmd += ["--model", model]
    out = subprocess.run(cmd, capture_output=True, text=True, timeout=300).stdout
    verdict = None
    for line in out.splitlines():
        line = line.strip().strip("`")
        if line.startswith("{") and '"verdict"' in line:
            try:
                verdict = json.loads(line)
            except json.JSONDecodeError:
                pass
    return verdict


def score(rows):
    """rows: list of {label, category, verdict}. Returns core metrics + under-promise tally."""
    core = [r for r in rows if r["category"] in CORE_CATEGORIES]
    under = [r for r in rows if r["category"] == "under-promise"]
    tp = sum(r["label"] == "inconsistent" and r["verdict"] == "inconsistent" for r in core)
    fp = sum(r["label"] == "consistent" and r["verdict"] == "inconsistent" for r in core)
    fn = sum(r["label"] == "inconsistent" and r["verdict"] == "consistent" for r in core)
    tn = sum(r["label"] == "consistent" and r["verdict"] == "consistent" for r in core)
    n = len(core) or 1
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": tp / (tp + fp) if (tp + fp) else 1.0,
        "recall": tp / (tp + fn) if (tp + fn) else 1.0,
        "accuracy": (tp + tn) / n,
        "flag_rate": (tp + fp) / n,
        "under_flagged": sum(r["verdict"] == "inconsistent" for r in under),
        "under_total": len(under),
    }


def selftest():
    # Perfect core predictions; both under-promise pairs flagged (worst case for evergreen).
    rows = [
        {"label": "consistent", "category": None, "verdict": "consistent"},
        {"label": "consistent", "category": None, "verdict": "consistent"},
        {"label": "inconsistent", "category": "direct-mismatch", "verdict": "inconsistent"},
        {"label": "inconsistent", "category": "over-promise", "verdict": "consistent"},  # a miss
        {"label": "inconsistent", "category": "under-promise", "verdict": "inconsistent"},
    ]
    m = score(rows)
    assert m["tp"] == 1 and m["fn"] == 1 and m["fp"] == 0 and m["tn"] == 2, m
    assert m["precision"] == 1.0 and m["recall"] == 0.5, m
    assert m["under_flagged"] == 1 and m["under_total"] == 1, m
    # No-false-positive edge: no flags at all → precision defined as 1.0.
    assert score([{"label": "consistent", "category": None, "verdict": "consistent"}])["precision"] == 1.0
    print("selftest ok")


def main():
    if "--selftest" in sys.argv:
        return selftest()
    ds = Path(sys.argv[sys.argv.index("--dataset") + 1]) if "--dataset" in sys.argv \
        else Path(os.environ.get("EVAL_DATASET", HERE / "dataset.jsonl"))
    model = os.environ.get("EVAL_MODEL", "")
    pairs = [json.loads(l) for l in ds.read_text().splitlines() if l.strip()]
    rows, transcript = [], []
    for i, p in enumerate(pairs, 1):
        v = run_pair(p, model)
        verdict = (v or {}).get("verdict", "consistent")
        rows.append({"label": p["label"], "category": p["category"], "verdict": verdict})
        transcript.append({**p, "got": v})
        mark = "✓" if (verdict == "inconsistent") == (p["label"] == "inconsistent") else "✗"
        print(f"  {mark} {p['id']:20} label={p['label']:12} verdict={verdict}")
    m = score(rows)
    out_dir = HERE / "out"; out_dir.mkdir(exist_ok=True)
    (out_dir / f"bench-{model or 'default'}.json").write_text(json.dumps(transcript, indent=2))
    print(f"\ncore set (consistent + direct-mismatch + over-promise), n={m['tp']+m['fp']+m['fn']+m['tn']}, "
          f"model={model or 'CLI default'}")
    print(f"  precision {m['precision']:.2f}  recall {m['recall']:.2f}  "
          f"accuracy {m['accuracy']:.2f}  flag-rate {m['flag_rate']:.2f}")
    print(f"  TP {m['tp']}  FP {m['fp']}  FN {m['fn']}  TN {m['tn']}")
    print(f"under-promise (informational by design, not scored as drift): "
          f"flagged {m['under_flagged']}/{m['under_total']}")


if __name__ == "__main__":
    main()
