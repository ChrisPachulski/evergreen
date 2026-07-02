#!/usr/bin/env python3
"""Benchmark evergreen against a labeled code/doc-consistency dataset (DocPrism/CASCADE schema).

Each dataset line: {"id", "func", "code", "doc", "label": "consistent"|"inconsistent",
                    "category": null|"direct-mismatch"|"over-promise"|"under-promise"}

For each pair we hand the model the code + doc and the shipped SKILL ruleset, ask for a
consistent/inconsistent verdict, and score against the label. Positive class = inconsistent.

  python3 eval/bench/run_bench.py                 # run the shipped dataset
  EVAL_MODEL=claude-haiku-4-5-20251001 python3 eval/bench/run_bench.py
  python3 eval/bench/run_bench.py --dataset path/to/cascade.jsonl
  python3 eval/bench/run_bench.py --rescore out/bench-default.json   # recompute, no API calls
  python3 eval/bench/run_bench.py --selftest      # prove the scoring math, no API calls

Metrics are reported at BOTH a balanced (50/50) and a natural (10/90) class split, as medians
over ≥1000 resamples of the consistent class — CASCADE's protocol (arXiv:2604.19400), mirrored
so the numbers line up. Real doc-drift is rare, so the natural split is the headline; balanced
numbers overstate precision by the prevalence gap.

evergreen deliberately treats under-promise (code does more than the doc says) as
informational, not drift — so under-promise pairs are scored SEPARATELY, not as misses.
"""
import json
import os
import random
import statistics
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
```{pair.get('language', 'python').lower()}
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
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=300).stdout
    except subprocess.TimeoutExpired:
        return None
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
    precision = tp / (tp + fp) if (tp + fp) else 1.0
    recall = tp / (tp + fn) if (tp + fn) else 1.0
    return {
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0,
        "specificity": tn / (tn + fp) if (tn + fp) else 1.0,
        "accuracy": (tp + tn) / n,
        "flag_rate": (tp + fp) / n,
        "under_flagged": sum(r["verdict"] == "inconsistent" for r in under),
        "under_total": len(under),
    }


def split_metrics(rows, pos_frac, resamples=1000, seed=0):
    """Median metrics at a fixed prevalence: keep every inconsistent core pair, resample the
    consistent class to the target ratio (CASCADE's protocol, arXiv:2604.19400)."""
    core = [r for r in rows if r["category"] in CORE_CATEGORIES]
    pos = [r for r in core if r["label"] == "inconsistent"]
    neg = [r for r in core if r["label"] == "consistent"]
    n_neg = round(len(pos) * (1 - pos_frac) / pos_frac)
    with_repl = n_neg > len(neg)  # tiny sets can't seat 9x consistent pairs; bootstrap instead
    rng = random.Random(seed)
    samples = [score(pos + (rng.choices(neg, k=n_neg) if with_repl
                            else rng.sample(neg, n_neg))) for _ in range(resamples)]
    med = lambda k: statistics.median(s[k] for s in samples)
    return {"n_pos": len(pos), "n_neg": n_neg, "resamples": resamples, "with_replacement": with_repl,
            **{k: med(k) for k in ("precision", "recall", "f1", "specificity", "flag_rate")}}


def report(rows, label=""):
    m = score(rows)
    n = m["tp"] + m["fp"] + m["fn"] + m["tn"]
    nat = split_metrics(rows, 0.10)
    bal = split_metrics(rows, 0.50)
    print(f"\ncore set (consistent + direct-mismatch + over-promise), n={n}{label}")
    print(f"  NATURAL 10/90 split (headline; {nat['n_pos']} inconsistent + {nat['n_neg']} consistent"
          f"{', consistent bootstrapped WITH replacement' if nat['with_replacement'] else ''},"
          f" medians over {nat['resamples']} resamples):")
    print(f"    precision {nat['precision']:.2f}  recall {nat['recall']:.2f}  F1 {nat['f1']:.2f}"
          f"  specificity {nat['specificity']:.2f}  flag-rate {nat['flag_rate']:.2f}")
    print(f"  balanced 50/50 split ({bal['n_pos']}+{bal['n_neg']}, medians over {bal['resamples']} resamples):")
    print(f"    precision {bal['precision']:.2f}  recall {bal['recall']:.2f}  F1 {bal['f1']:.2f}"
          f"  specificity {bal['specificity']:.2f}  flag-rate {bal['flag_rate']:.2f}")
    print(f"  raw full set: precision {m['precision']:.2f}  recall {m['recall']:.2f}"
          f"  accuracy {m['accuracy']:.2f}  flag-rate {m['flag_rate']:.2f}"
          f"  |  TP {m['tp']}  FP {m['fp']}  FN {m['fn']}  TN {m['tn']}")
    print(f"under-promise (informational by design, not scored as drift): "
          f"flagged {m['under_flagged']}/{m['under_total']}")
    print("baseline regime: the peer is DocPrism (arXiv:2511.00215) — 0.62 precision @ 15% flag"
          " rate, multi-language, no fine-tuning. Fine-tuned single-language SOTA (F1 0.88-0.94)"
          " is a different regime and out of scope.")


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
    assert abs(m["f1"] - 2/3) < 1e-9 and m["specificity"] == 1.0, m
    assert m["under_flagged"] == 1 and m["under_total"] == 1, m
    # No-false-positive edge: no flags at all → precision defined as 1.0.
    assert score([{"label": "consistent", "category": None, "verdict": "consistent"}])["precision"] == 1.0
    # Natural-split resampling: 2 flagged positives + 18 clean negatives at 10/90 is exact.
    clean = [{"label": "inconsistent", "category": None, "verdict": "inconsistent"}] * 2 + \
            [{"label": "consistent", "category": None, "verdict": "consistent"}] * 18
    nat = split_metrics(clean, 0.10, resamples=50)
    assert nat["n_pos"] == 2 and nat["n_neg"] == 18 and not nat["with_replacement"], nat
    assert nat["precision"] == nat["recall"] == nat["f1"] == nat["specificity"] == 1.0, nat
    # A false-positive-prone judge collapses at natural prevalence: 2 TP + 1 FP in 6 negatives.
    # At 10/90, 18 negatives bootstrapped from 6 → median FP ≈ 3, precision ≈ 2/(2+3) = 0.4.
    noisy = [{"label": "inconsistent", "category": None, "verdict": "inconsistent"}] * 2 + \
            [{"label": "consistent", "category": None, "verdict": "inconsistent"}] + \
            [{"label": "consistent", "category": None, "verdict": "consistent"}] * 5
    nat = split_metrics(noisy, 0.10, resamples=500)
    assert nat["with_replacement"], nat
    assert 0.25 < nat["precision"] < 0.55, nat  # far below the balanced-set 2/3
    bal = split_metrics(noisy, 0.50, resamples=500)
    assert bal["precision"] > nat["precision"], (bal, nat)
    print("selftest ok")


def rows_from_transcript(transcript):
    return [{"label": t["label"], "category": t["category"],
             "verdict": (t.get("got") or {}).get("verdict", "consistent")} for t in transcript]


def main():
    if "--selftest" in sys.argv:
        return selftest()
    if "--rescore" in sys.argv:
        path = Path(sys.argv[sys.argv.index("--rescore") + 1])
        return report(rows_from_transcript(json.loads(path.read_text())), f", rescored from {path.name}")
    ds = Path(sys.argv[sys.argv.index("--dataset") + 1]) if "--dataset" in sys.argv \
        else Path(os.environ.get("EVAL_DATASET", HERE / "dataset.jsonl"))
    model = os.environ.get("EVAL_MODEL", "")
    pairs = [json.loads(l) for l in ds.read_text().splitlines() if l.strip()]
    out_dir = HERE / "out"; out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"bench-{ds.stem}-{model or 'default'}.json"
    done = {t["id"]: t for t in json.loads(out_path.read_text())} if out_path.exists() else {}
    todo = [p for p in pairs if p["id"] not in done]  # resumable: crash loses nothing scored
    workers = int(os.environ.get("EVAL_CONCURRENCY", "1"))
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for p, v in zip(todo, pool.map(lambda p: run_pair(p, model), todo)):
            done[p["id"]] = {**p, "got": v}
            verdict = (v or {}).get("verdict", "consistent")
            mark = "✓" if (verdict == "inconsistent") == (p["label"] == "inconsistent") else "✗"
            print(f"  {mark} [{len(done)}/{len(pairs)}] {p['id']:40} label={p['label']:12} verdict={verdict}",
                  flush=True)
            if len(done) % 25 == 0:
                out_path.write_text(json.dumps(list(done.values()), indent=2))
    transcript = [done[p["id"]] for p in pairs]
    out_path.write_text(json.dumps(transcript, indent=2))
    report(rows_from_transcript(transcript), f", model={model or 'CLI default'}")


if __name__ == "__main__":
    main()
