#!/usr/bin/env python3
"""Benchmark evergreen against a labeled code/doc-consistency dataset (DocPrism/CASCADE schema).

Each dataset line: {"id", "func", "code", "doc", "label": "consistent"|"inconsistent",
                    "category": null|"direct-mismatch"|"over-promise"|"under-promise"}

Each claim is put on TRIAL against the code (see the `judge` function). No single call is
trusted: a strong-model snap verdict, a cheap challenge that must fail to break it, three BLIND
independent prongs (defend / prove-wrong / hardest-broken), a cheap blind-spot surfacer, and —
only when the evidence isn't unanimous — a strong-model synthesis that decides. Fable is banned
from every role; strong defaults to Opus, cheap to Sonnet (EVAL_MODEL_STRONG / EVAL_MODEL_CHEAP).

  python3 eval/bench/run_bench.py                 # run the shipped dataset (trial, Opus+Sonnet)
  EVAL_MODEL_CHEAP=claude-haiku-4-5-20251001 python3 eval/bench/run_bench.py
  python3 eval/bench/run_bench.py --dataset path/to/cascade.jsonl
  python3 eval/bench/run_bench.py --rescore out/bench-default.json   # recompute, no API calls
  python3 eval/bench/run_bench.py --selftest      # exercise scoring + the trial flow, no API

Metrics are reported at BOTH a balanced (50/50) and a natural (10/90) class split, as medians
over ≥1000 resamples of the consistent class (CASCADE's protocol, arXiv:2604.19400). Real
doc-drift is rare, so the natural split is the headline. Under-promise (code does more than the
doc says) is treated as informational, not drift, and scored SEPARATELY.
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


def _fence(pair):
    return f"## Code (`{pair['func']}`)\n```{pair.get('language', 'python').lower()}\n{pair['code']}\n```\n\n## Documentation\n{pair['doc']}"


def claude_json(prompt, model, tools="", timeout=300):
    """Run one headless CLI call; return the first parsed JSON object printed, or None."""
    cmd = ["claude", "-p", prompt, "--allowedTools", tools]
    if model:
        cmd += ["--model", model]
    try:
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except subprocess.TimeoutExpired:
        return None
    got = None
    for line in out.splitlines():
        line = line.strip().strip("`")
        if line.startswith("{") and (got is None):
            try:
                got = json.loads(line)
            except json.JSONDecodeError:
                pass
    return got


# ── The trial ────────────────────────────────────────────────────────────────
# A documentation claim is put on trial against the real code. No single call is trusted:
#   1. snap    (strong)  — first-instinct verdict, logged; a weighted vote, never the last word.
#   2. challenge(cheap)  — hardest case the snap is WRONG (direction flips); it must survive.
#   3. prongs   (blind)  — three independent fresh reads (defend / prove-wrong / hardest-broken);
#                          they are told NOTHING of the snap, challenge, or their own tier, so a
#                          "confirming" prong can't rubber-stamp. Cheap if snap survived, strong
#                          if cracked. On the survived path a 2-2 tie of {snap+3 prongs} = the
#                          snap failed → escalate to the strong prongs.
#   4. blindspot(cheap)  — surfaces an angle everyone missed; it only RAISES, never decides.
#   5. synthesis(strong) — weighs it all into the verdict, but only when the evidence isn't
#                          unanimous. This is where "did the accusation beat its defense?" is
#                          judged — there is no separate immune rule (needs iterations we lack).

def snap_call(pair, model):
    prompt = f"""{skill_body()}

# Task
Read this documentation claim against the code and give your first-instinct verdict: is the doc
consistent with the code, or has it drifted? "Consistent" means the doc makes no claim the code
contradicts or fails to deliver; extra undocumented behavior is NOT an inconsistency.

{_fence(pair)}

Reply with exactly one line of JSON and nothing else:
{{"id": "{pair['id']}", "verdict": "consistent" | "inconsistent", "category": "direct-mismatch" | "over-promise" | "under-promise" | null, "why": "<cite the code>"}}"""
    return claude_json(prompt, model)


def challenge_call(pair, snap_verdict, model):
    attack = ("Argue the documentation is actually INCONSISTENT — find the specific code that "
              "breaks its claim." if snap_verdict == "consistent" else
              "Argue the documentation is actually CONSISTENT — give the reading of the code "
              "under which it holds.")
    prompt = f"""A first reviewer judged this documentation "{snap_verdict}". Your job is the
opposite: {attack} Make the hardest, best-cited case you can that the first reviewer was wrong.

Then judge your own case honestly. "cracks" is a HIGH bar: true only if your case rests on code
actually shown here and would change the verdict for a careful reviewer — not on unseen code, a
speculative edge case, or a merely-arguable alternative reading. Building the attack is your job
either way; most first verdicts survive a decent attack. When unsure, cracks=false.

{_fence(pair)}

Reply with exactly one line of JSON and nothing else:
{{"cracks": true | false, "why": "<your strongest case, citing the code>"}}"""
    # cracks=true  → the first verdict does NOT survive the challenge (it was contestable)
    return claude_json(prompt, model)


PRONGS = {
    "defend": "Make the strongest case the documentation is STILL TRUE for this code. What reading makes it hold? Cite the code.",
    "prove-wrong": "Try to PROVE the documentation wrong: find the exact code token or behavior that breaks its claim. If none exists, say so.",
    "hardest-broken": "Make the hardest case the documentation genuinely MISREPRESENTS what the code does.",
}


def prong_call(pair, role, model):
    # BLIND: the prong sees only the claim + code + its assigned angle — never the snap, the
    # challenge, or its tier. That blindness is what stops a "confirming" prong rubber-stamping.
    prompt = f"""{skill_body()}

# Task ({role})
{PRONGS[role]} Then, judging strictly from the code, give your honest verdict: is the doc
consistent with the code? Code doing MORE than the doc says is consistent (informational).

{_fence(pair)}

Reply with exactly one line of JSON and nothing else:
{{"role": "{role}", "verdict": "consistent" | "inconsistent", "why": "<cite the code>"}}"""
    return claude_json(prompt, model)


def run_prongs(pair, model):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=3) as pool:
        return list(pool.map(lambda r: prong_call(pair, r, model), PRONGS))


def blindspot_call(pair, model):
    prompt = f"""Three reviewers just judged whether this documentation matches the code. Your
only job: name ONE angle they could ALL have missed — a reading of the code, an edge case, a
claim in the doc — strong enough to FLIP the verdict. The bar is HIGH: the angle must rest on
code actually shown here and could change the outcome on its own. An interesting observation, a
nuance, or anything resting on unseen code is NOT a missed angle. Most trials have none — the
expected answer is null. You are surfacing a candidate, not deciding.

{_fence(pair)}

Reply with exactly one line of JSON and nothing else:
{{"missed_angle": "<the verdict-flipping angle, or null>"}}"""
    return claude_json(prompt, model)


def synthesis_call(pair, snap, challenge, prongs, blindspot, model):
    ev = {"snap": snap, "challenge": challenge, "prongs": prongs, "blindspot": blindspot}
    prompt = f"""{skill_body()}

# Task
You are the final judge. Below is the full record of a trial over one documentation claim: a
first-instinct snap verdict (a weighted vote, not binding), a challenge that tried to break it,
three independent reviewers, and a blind-spot candidate. Weigh them and give the verdict.

A "drift" finding stands only if the accusation beat its strongest defense — do not flag on an
objection a reasonable consistent reading survives. If the blind-spot angle genuinely changes
the picture, account for it. Code doing more than the doc says is consistent, not drift.

{_fence(pair)}

## Trial record
{json.dumps(ev, indent=1)}

Reply with exactly one line of JSON and nothing else:
{{"verdict": "consistent" | "inconsistent", "category": "direct-mismatch" | "over-promise" | "under-promise" | null, "why": "<the deciding reasoning, citing the code>"}}"""
    return claude_json(prompt, model)


def judge(pair, models, stages=(), run_test=None):
    """Put one claim on trial (the 5-step process). models: {strong, cheap}."""
    strong, cheap = models["strong"], models["cheap"]
    trail = {}

    snap = snap_call(pair, strong)                                    # 1. snap (strong)
    snap_v = (snap or {}).get("verdict", "consistent")
    trail["snap"] = snap

    ch = challenge_call(pair, snap_v, cheap)                          # 2. challenge (cheap)
    trail["challenge"] = ch
    cracked = bool(ch and ch.get("cracks"))

    prongs = run_prongs(pair, strong if cracked else cheap)           # 3. blind prongs
    trail["prongs"] = prongs
    pv = [(p or {}).get("verdict") for p in prongs]

    if not cracked:  # survived path: tally snap + 3 prongs; a 2-2 tie means the snap failed
        votes = [snap_v] + pv
        if votes.count("inconsistent") == votes.count("consistent"):
            cracked = True
            prongs = run_prongs(pair, strong)                         # escalate to strong prongs
            trail["prongs_escalated"] = prongs
            pv = [(p or {}).get("verdict") for p in prongs]

    bs = blindspot_call(pair, cheap)                                  # 4. blind-spot (cheap)
    trail["blindspot"] = bs
    missed = bool(bs and bs.get("missed_angle"))

    all_votes = [snap_v] + pv
    if not missed and len(set(all_votes)) == 1:      # everyone agrees, nothing missed → done
        verdict, category, why = snap_v, (snap or {}).get("category"), (snap or {}).get("why")
    else:                                            # 5. synthesis (strong), only when contested
        syn = synthesis_call(pair, snap, ch, prongs, bs, strong)
        trail["synthesis"] = syn
        verdict = (syn or {}).get("verdict", snap_v)
        category, why = (syn or {}).get("category"), (syn or {}).get("why")

    return {"verdict": verdict, "category": category, "why": why, "contested": cracked or missed,
            "stages": trail}


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

    # The trial: stub the CLI so no API is touched. `scripted` feeds each stage a canned reply
    # keyed by what the prompt is asking, so we can drive the flow deterministically.
    global claude_json
    real_json = claude_json
    def scripted(replies):
        def fake(prompt, model, tools="", timeout=300):
            if "final judge" in prompt:    return replies["synthesis"]   # embeds the record; check first
            if '"cracks"' in prompt:       return replies["challenge"]
            if '"role"' in prompt:         return replies["prong"](prompt)
            if '"missed_angle"' in prompt: return replies["blindspot"]
            return replies["snap"]         # the snap call
        return fake
    pair = {"id": "x", "func": "f", "code": "def f(): pass", "doc": "d", "language": "python"}
    M = {"strong": "", "cheap": ""}
    con = lambda: {"verdict": "consistent"}
    inc = lambda: {"verdict": "inconsistent", "category": "direct-mismatch", "why": "q"}
    try:
        # snap=drift, challenge can't crack it, all prongs agree drift, no blind spot → unanimous
        # drift with NO synthesis call.
        claude_json = scripted({"snap": inc(), "challenge": {"cracks": False},
                                "prong": lambda p: inc(), "blindspot": {"missed_angle": None},
                                "synthesis": con()})
        g = judge(pair, M)
        assert g["verdict"] == "inconsistent" and "synthesis" not in g["stages"], g
        # snap=drift survives challenge, but prongs split 2 consistent / 1 inconsistent →
        # with the snap that's a 2-2 tie → escalate + synthesis decides (here: consistent).
        flips = iter(["consistent", "consistent", "inconsistent",     # cheap prongs (tie)
                      "consistent", "consistent", "inconsistent"])    # escalated strong prongs
        claude_json = scripted({"snap": inc(), "challenge": {"cracks": False},
                                "prong": lambda p: {"verdict": next(flips)},
                                "blindspot": {"missed_angle": None}, "synthesis": con()})
        g = judge(pair, M)
        assert g["stages"].get("prongs_escalated") and g["contested"], g
        assert g["verdict"] == "consistent", g          # synthesis had the last word
        # a surfaced blind spot forces synthesis even when everyone agreed
        claude_json = scripted({"snap": con(), "challenge": {"cracks": False},
                                "prong": lambda p: con(),
                                "blindspot": {"missed_angle": "edge case"}, "synthesis": inc()})
        g = judge(pair, M)
        assert "synthesis" in g["stages"] and g["verdict"] == "inconsistent", g
    finally:
        claude_json = real_json
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
    # Two tiers: strong (snap + escalated prongs + synthesis) and cheap (challenge, prongs,
    # blind-spot). Fable is banned from every role in this project.
    strong = os.environ.get("EVAL_MODEL_STRONG", "claude-opus-4-8")
    cheap = os.environ.get("EVAL_MODEL_CHEAP", "claude-sonnet-5")
    for role, m in (("strong", strong), ("cheap", cheap)):
        assert "fable" not in m.lower(), f"Fable is banned from this project ({role}={m})"
    models = {"strong": strong, "cheap": cheap}
    pairs = [json.loads(l) for l in ds.read_text().splitlines() if l.strip()]
    out_dir = HERE / "out"; out_dir.mkdir(exist_ok=True)
    out_path = out_dir / f"bench-{ds.stem}-trial-{strong}.json"
    done = {t["id"]: t for t in json.loads(out_path.read_text())} if out_path.exists() else {}
    todo = [p for p in pairs if p["id"] not in done]  # resumable: crash loses nothing scored
    workers = int(os.environ.get("EVAL_CONCURRENCY", "1"))
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for p, v in zip(todo, pool.map(lambda p: judge(p, models), todo)):
            done[p["id"]] = {**p, "got": v}
            verdict = (v or {}).get("verdict", "consistent")
            path = "contested" if (v or {}).get("contested") else "clear"
            mark = "✓" if (verdict == "inconsistent") == (p["label"] == "inconsistent") else "✗"
            print(f"  {mark} [{len(done)}/{len(pairs)}] {p['id']:40} label={p['label']:12} "
                  f"verdict={verdict:12} [{path}]", flush=True)
            if len(done) % 25 == 0:
                out_path.write_text(json.dumps(list(done.values()), indent=2))
    transcript = [done[p["id"]] for p in pairs]
    out_path.write_text(json.dumps(transcript, indent=2))
    report(rows_from_transcript(transcript), f", trial, strong={strong} cheap={cheap}")


if __name__ == "__main__":
    main()
