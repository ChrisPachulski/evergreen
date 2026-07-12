# Codex Benchmark Provider Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a minimal Codex provider to Evergreen's reproducible benchmark harness and complete a fresh five-language run.

**Architecture:** Keep every trial prompt and verdict rule unchanged. Select the CLI only at the existing model-call boundary, and bind provider identity into metadata and output filenames so incompatible artifacts cannot resume.

**Tech Stack:** Python standard library, `unittest`, Claude CLI, Codex CLI.

## Global Constraints

- `EVAL_PROVIDER` accepts only `claude` or `codex` and defaults to `claude`.
- Codex runs are non-interactive, ephemeral, read-only, schema-constrained, and fail closed on tool events.
- The publication threshold remains `0.99` for each of the five required languages.
- Claude outage artifacts are never resumed, filtered, or relabeled.
- No new dependency is added.

---

### Task 1: Provider call boundary and provenance

**Files:**
- Modify: `tests/test_bench.py`
- Modify: `tests/test_bench_artifact.py`
- Modify: `eval/bench/trial.py`
- Modify: `eval/bench/runner.py`
- Modify: `eval/bench/artifact.py`
- Modify: `eval/bench/report.py`

**Interfaces:**
- Consumes: `EVAL_PROVIDER`, `EVAL_MODEL_STRONG`, `EVAL_MODEL_CHEAP`.
- Produces: `model_json(prompt, model, provider=...)`, provider-bound metadata and artifact names.

- [ ] Write failing tests for safe Codex invocation, provider validation, parsing, provenance, and filenames.
- [ ] Implement the minimum provider branch at the current CLI boundary.
- [ ] Run focused tests, the full suite, and benchmark self-test.
- [ ] Commit and push the tested adapter before long-running evaluation.

### Task 2: Fresh five-language Codex benchmark

**Files:**
- Modify after the gate passes: `eval/bench/results-current.md`
- Modify after the gate passes: `eval/bench/README.md`

**Interfaces:**
- Consumes: five checked-in JSONL datasets, clean committed judge code, Codex CLI authentication.
- Produces: five complete Codex artifacts and one report with exact provenance.

- [ ] Run a one-row smoke trial and freeze a clean commit/tree.
- [ ] Run all five datasets fresh with fixed settings.
- [ ] Generate the report with all required artifacts and `--coverage-threshold 0.99`.
- [ ] Verify provenance, coverage, metrics, and documentation.
- [ ] Run release gates, commit the report, push `main`, and verify GitHub CI.
