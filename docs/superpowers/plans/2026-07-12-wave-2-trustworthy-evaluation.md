# Wave 2 Trustworthy Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make every published Evergreen metric reproducible from the judge, dataset, model, and completion record that actually produced it.

**Architecture:** The benchmark introduces an explicit abstain/error outcome, bounded retries, completion coverage, and artifact metadata. A report generator derives README-ready tables from committed artifacts instead of hand-copied numbers. Language runs remain separate.

**Tech Stack:** Python 3 standard library, Claude CLI, JSON/JSONL.

## Global Constraints

- Model failures are never scored as consistent.
- Confusion matrices report completion coverage and exclude abstentions only while displaying their count.
- No aggregate metric may hide per-language performance.
- Published results identify skill/judge commit, dataset hash, model/CLI, concurrency, timeout, cost when available, and protocol version.
- Do not claim best-in-class leadership unless current-judge artifacts support it.

---

### Task 1: First-class abstentions and bounded retries

**Files:**
- Modify: `eval/bench/run_bench.py`
- Create: `tests/test_bench.py`

**Interfaces:**
- `claude_json(...) -> {"status": "ok", "value": dict} | {"status": "abstain", "reason": str}`.
- Every pair result stores `final_status`, `final_verdict`, and stage status.
- Score output includes `attempted`, `completed`, `abstained`, and `completion_rate`.

- [ ] Write failing tests for timeout, malformed response, missing snap/prong/synthesis, bounded retry success, abstention scoring, and rescore parity.
- [ ] Run `python3 -m unittest tests.test_bench -v` and confirm failure.
- [ ] Refactor the runner minimally so missing stages cannot default to `consistent`; retry transient failures at most twice with jitter-free deterministic test injection.
- [ ] Run focused tests and `python3 eval/bench/run_bench.py --selftest`.
- [ ] Commit with `fix(eval): score model failures as abstentions`.

### Task 2: Reproducible artifact metadata and generated reports

**Files:**
- Create: `eval/bench/artifact.py`
- Create: `eval/bench/report.py`
- Create: `tests/test_bench_artifact.py`
- Modify: `eval/bench/run_bench.py`

**Interfaces:**
- `artifact_metadata(dataset: Path, repo: Path, settings: dict) -> dict`.
- `python3 eval/bench/report.py <artifact...> --markdown <path>` generates tables and validates coverage threshold.

- [ ] Write failing hash, metadata, report, threshold, and deterministic-order tests.
- [ ] Implement SHA-256 dataset/skill/judge hashing, Git commit capture, resolved CLI version capture, settings, timing, and optional provider usage fields.
- [ ] Generate per-language Markdown sections without cross-language aggregation.
- [ ] Run focused tests and commit with `feat(eval): make benchmark artifacts reproducible`.

### Task 3: Run and publish the current judge

**Files:**
- Create/replace: `eval/bench/out/bench-*-trial-*.json`
- Create: `eval/bench/results-current.md`
- Modify: `eval/bench/README.md`
- Modify: `eval/README.md`
- Modify: `README.md`

- [ ] Record the clean implementation commit and exact installed Claude CLI/model identifiers.
- [ ] Run current-judge Python, Java, TypeScript, Rust, and Go datasets with bounded concurrency and save complete artifacts. If a provider limit interrupts a run, resume only missing abstentions; never synthesize results.
- [ ] Run the report generator with the declared completion threshold and inspect every matrix.
- [ ] Replace hand-copied current metrics with generated output or explicit unavailable/pending language status.
- [ ] Run rescoring on every committed artifact and confirm identical counts.
- [ ] Commit artifacts and docs with `eval: publish reproducible current-judge results`.

### Task 4: Wave 2 verification

- [ ] Run:

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 eval/bench/run_bench.py --selftest
bash tests/action.sh
bash tests/hooks.sh
git diff --check
```

- [ ] Confirm no README result refers to an older stage schema than the linked artifact.
- [ ] Confirm weak or incomplete language results are stated without spin.
