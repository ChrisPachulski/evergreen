# Detector Quality Remediation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Raise Evergreen's human-adjudicated precision, recall, and F1 to at least `0.60` in Python, Java, TypeScript, Rust, and Go without weakening prove-or-drop semantics or spending on a full benchmark before offline and small-development gates pass.

**Architecture:** Separate model-stage collection from a pure, versioned resolver so the published `v1` trial can be reproduced offline and a stricter `v2` policy can be tested without rewriting history. `v2` introduces a first-class semantic `unverified` outcome, a neutral evidence-sufficiency prong, and bounded label-blind Java source context; human-adjudicated repository-grouped development and holdout manifests prevent tuning against provisional CoDoc labels.

**Tech Stack:** Python 3.10+ standard library, `unittest`, Git object reads, JSON/JSONL, existing Codex/Claude CLI adapters.

## Global Constraints

- Use an isolated worktree when executing this plan. Preserve unrelated work and never edit a managed plugin marketplace/cache checkout.
- Follow strict red-green-refactor: add one failing test, run it and confirm the expected failure, write the minimum implementation, rerun focused tests, then refactor only while green.
- Add no third-party dependency and no Java parser. Context extraction uses bounded Git reads and conservative exact/whitespace-normalized matching.
- Keep the frozen `0.4.0` result at commit `cb24647f7c62b9704d10c97e615005d924c005f2` reproducible. Never overwrite, relabel, or silently rescore its stored final verdicts.
- Treat all existing CoDoc `consistent` and `inconsistent` labels as provisional. They are not a prompt-optimization target, even when all three LLM annotators agreed.
- Consume a repository-grouped human split manifest from the label-validity workstream. Do not reveal or load holdout labels until the candidate prompt, resolver, context builder, and implementation commit are frozen.
- Require at least 20 human-adjudicated positive rows and 20 human-adjudicated negative rows per language before applying the practical metric gate. If a language has fewer, stop and expand adjudication rather than interpreting an unstable percentage.
- Keep CASCADE Java results separate from CoDoc-derived language results. Never pool their confusion matrices.
- Distinguish provider/infrastructure abstention from semantic `unverified`. Neither may be converted to `consistent` or `inconsistent` for scoring.
- A publishable candidate must clear, per language: provider completion `>= 0.99`, semantic decision coverage `>= 0.99`, precision `>= 0.60`, recall `>= 0.60`, and F1 `>= 0.60` on untouched human labels.
- Do not publish duplicated CoDoc code/doc text or free-form model reasoning. The public audit artifact may contain stable IDs, hashes, structured stage outcomes, and final outcomes only.
- Do not run any paid model call until Tasks 1–6 pass. After that, run only the bounded development canary in Task 7. Do not run the full five-language paid evaluation until the canary passes every gate.
- Once judge behavior changes, describe the existing `cb24647` report as the **0.4.0 baseline**, not the current judge, until a new frozen five-language report passes.
- Stage and commit in separate shell calls. Do not push from this plan unless the user separately requests it.

---

## File Map

### New files

- `eval/bench/resolver.py` — validates structured stage records and resolves them under immutable `v1` or evidence-aware `v2` semantics.
- `eval/bench/replay.py` — read-only offline replay/diff CLI for stored artifacts and resolver-policy comparisons.
- `eval/bench/split_manifest.py` — validates public ID-only development/holdout manifests and prevents project leakage.
- `eval/bench/java_context.py` — builds bounded label-blind Java source context from local Git objects without checking out or modifying source repositories.
- `tests/test_bench_resolver.py` — resolver parity, semantic-status, and fail-closed tests.
- `tests/test_bench_replay.py` — replay CLI and stored-final parity tests.
- `tests/test_bench_split_manifest.py` — complete coverage and repository-group isolation tests.
- `tests/test_bench_java_context.py` — deterministic Git context extraction, ambiguity, and limits tests.

### Existing files to modify

- `eval/bench/trial.py` — prompt schemas, balanced prong roles, context envelope, stage collection, and resolver dispatch.
- `eval/bench/runner.py` — resolver selection, semantic result storage, and replay-safe settings.
- `eval/bench/artifact.py` — judge-module provenance, artifact validation, and context/resolver metadata.
- `eval/bench/metrics.py` — separate provider completion, semantic decision coverage, unverified count, and confusion-matrix decisions.
- `eval/bench/report.py` — per-language quality thresholds and explicit unverified reporting.
- `eval/bench/frozen_run.py` — declare and bind resolver protocol for paid runs.
- `eval/bench/cascade_to_jsonl.py` — optional label-blind Java context augmentation from a local mirror root.
- `eval/bench/model-output.schema.json` — keep the outer Codex wrapper but document that the inner payload is validated by `resolver.py`.
- `tests/test_bench.py` — v2 prompt isolation, stage routing, and runner integration.
- `tests/test_bench_artifact.py` — resolver/context provenance and resume incompatibility.
- `tests/test_bench_frozen.py` — frozen resolver selection and paid-run refusal boundaries.
- `skills/evergreen/SKILL.md` — production trial roles and evidence-sufficiency semantics after the cheap gate passes.
- `skills/evergreen/DIGEST.md` — compact version of the same proven policy.
- `AGENTS.md` — flat-host version of the same proven policy.
- `docs/DESIGN.md` — resolver/context boundary and outcome semantics.
- `README.md`, `eval/README.md`, `eval/bench/README.md` — baseline/current wording, protocol, and quality gates.
- `eval/bench/results-current.md` — retain the old matrix as the named 0.4.0 baseline until Task 8 produces a passing replacement.

---

### Task 1: Freeze and validate the human evaluation split

**Files:**
- Create: `eval/bench/split_manifest.py`
- Create: `tests/test_bench_split_manifest.py`

**Interfaces:**
- Consumes: an ID-only JSON manifest with schema `{"schema_version": 1, "datasets": [{"sha256": str, "language": str}], "rows": [{"id": str, "dataset_sha256": str, "project": str, "split": "dev" | "holdout"}]}` and the corresponding JSONL datasets.
- Produces: `load_split_manifest(path: Path, datasets: list[Path]) -> dict[str, str]`, mapping row ID to `dev` or `holdout` only after validating exact dataset hashes, complete row coverage, unique IDs, and one split per project.
- Does not consume labels. Private human labels are joined by ID only in Tasks 7 and 8.

- [ ] **Step 1: Write the failing manifest-contract tests**

```python
# tests/test_bench_split_manifest.py
import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from eval.bench.split_manifest import load_split_manifest


class SplitManifestTests(unittest.TestCase):
    def write_dataset(self, root, name, rows):
        path = root / name
        payload = "".join(json.dumps(row, sort_keys=True) + "\n" for row in rows)
        path.write_text(payload)
        return path, hashlib.sha256(payload.encode()).hexdigest()

    def test_accepts_complete_project_grouped_id_only_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            rows = [
                {"id": "org/a/f#1", "func": "f", "code": "x", "doc": "x",
                 "label": "consistent", "category": None, "language": "Java"},
                {"id": "org/b/g#1", "func": "g", "code": "y", "doc": "y",
                 "label": "inconsistent", "category": None, "language": "Java"},
            ]
            dataset, digest = self.write_dataset(root, "java.jsonl", rows)
            manifest = root / "split.json"
            manifest.write_text(json.dumps({
                "schema_version": 1,
                "datasets": [{"sha256": digest, "language": "Java"}],
                "rows": [
                    {"id": "org/a/f#1", "dataset_sha256": digest,
                     "project": "org/a", "split": "dev"},
                    {"id": "org/b/g#1", "dataset_sha256": digest,
                     "project": "org/b", "split": "holdout"},
                ],
            }))

            self.assertEqual(load_split_manifest(manifest, [dataset]), {
                "org/a/f#1": "dev", "org/b/g#1": "holdout"
            })

    def test_rejects_project_leakage_between_dev_and_holdout(self):
        # Construct two IDs with project="org/a" but opposite splits.
        # The asserted error is the public safety contract.
        with self.assertRaisesRegex(ValueError, "project appears in both"):
            load_split_manifest(self.manifest, [self.dataset])
```

Add separate tests for a missing row, unknown row, duplicate ID, wrong dataset hash, unknown split, malformed project, and a manifest that contains `label`, `code`, `doc`, `reasoning`, or `verdict` fields.

- [ ] **Step 2: Run the focused test and verify RED**

Run: `python3 -m unittest tests.test_bench_split_manifest -v`

Expected: import failure for `eval.bench.split_manifest`.

- [ ] **Step 3: Implement the minimum strict loader**

```python
# eval/bench/split_manifest.py
import hashlib
import json
from pathlib import Path

ALLOWED_TOP = {"schema_version", "datasets", "rows"}
ALLOWED_DATASET = {"sha256", "language"}
ALLOWED_ROW = {"id", "dataset_sha256", "project", "split"}
SPLITS = {"dev", "holdout"}


def _sha256(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def load_split_manifest(path: Path, datasets: list[Path]) -> dict[str, str]:
    document = json.loads(Path(path).read_text())
    if not isinstance(document, dict) or set(document) != ALLOWED_TOP:
        raise ValueError("split manifest has unknown or missing fields")
    if document["schema_version"] != 1:
        raise ValueError("unsupported split manifest schema")
    declared = document["datasets"]
    if not isinstance(declared, list) or any(
        not isinstance(item, dict) or set(item) != ALLOWED_DATASET for item in declared
    ):
        raise ValueError("split manifest datasets are malformed")
    actual = {_sha256(dataset): Path(dataset) for dataset in datasets}
    if set(actual) != {item["sha256"] for item in declared}:
        raise ValueError("split manifest dataset hashes do not match")

    expected_ids = set()
    for dataset in actual.values():
        expected_ids.update(json.loads(line)["id"] for line in dataset.read_text().splitlines()
                            if line.strip())

    result = {}
    project_splits = {}
    for row in document["rows"]:
        if not isinstance(row, dict) or set(row) != ALLOWED_ROW:
            raise ValueError("split manifest row has forbidden or missing fields")
        if row["dataset_sha256"] not in actual or row["split"] not in SPLITS:
            raise ValueError("split manifest row has invalid dataset or split")
        if row["id"] in result:
            raise ValueError("split manifest contains duplicate row id")
        prior = project_splits.setdefault(row["project"], row["split"])
        if prior != row["split"]:
            raise ValueError("project appears in both dev and holdout")
        result[row["id"]] = row["split"]
    if set(result) != expected_ids:
        raise ValueError("split manifest does not exactly cover dataset rows")
    return result
```

Use the existing bounded regular-file reader from `eval/bench/artifact.py` instead of unbounded `read_bytes`/`read_text` in the final implementation. The sketch fixes the public shape; the implementation must set explicit manifest and dataset byte/row ceilings matching the benchmark loader.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run: `python3 -m unittest tests.test_bench_split_manifest -v`

Expected: all split-manifest tests pass with `OK`.

- [ ] **Step 5: Validate the label-workstream manifest without revealing holdout labels**

Run:

```bash
python3 -m eval.bench.split_manifest \
  eval/bench/splits/human-v1-public.json \
  eval/bench/cascade-java.jsonl \
  eval/bench/codocbench-validated.jsonl \
  eval/bench/codocbench-ts-validated.jsonl \
  eval/bench/codocbench-rust-validated.jsonl \
  eval/bench/codocbench-go-validated.jsonl
```

Expected: `split manifest valid: 2104 rows; projects do not cross dev/holdout` and no labels printed.

If the label-validity workstream chooses different checked-in filenames, update only the command and manifest paths after verifying their hashes; do not weaken the schema.

- [ ] **Step 6: Commit the split validator**

```bash
git add eval/bench/split_manifest.py tests/test_bench_split_manifest.py
git commit -m "test(eval): freeze repository-grouped quality splits"
```

---

### Task 2: Extract immutable resolver `v1` and prove exact offline parity

**Files:**
- Create: `eval/bench/resolver.py`
- Create: `tests/test_bench_resolver.py`
- Modify: `eval/bench/trial.py:418-507`
- Modify: `eval/bench/artifact.py:27-31`

**Interfaces:**
- Produces: `resolve_v1(stages: dict) -> dict` with the existing keys `final_status`, `final_verdict`, `verdict`, `category`, `why`, `contested`, and `stages`.
- Produces: `resolve(stages: dict, resolver_id: str) -> dict`; initially accepts only `v1`.
- Consumes the already-collected stage envelope. It never invokes a model.
- `trial.judge(...)` remains responsible for deciding which stages to call, then delegates final interpretation to `resolve_v1`.

- [ ] **Step 1: Write failing `v1` characterization tests**

```python
# tests/test_bench_resolver.py
import unittest
from eval.bench.resolver import resolve_v1


def ok(value):
    return {"status": "ok", "value": value}


class ResolverV1Tests(unittest.TestCase):
    def test_unanimous_record_uses_snap_without_synthesis(self):
        snap = {"verdict": "consistent", "category": None, "why": "return x"}
        stages = {
            "snap": ok(snap),
            "challenge": ok({"cracks": False, "why": "no direct contradiction"}),
            "prongs": [ok({"role": role, "verdict": "consistent", "why": "matches"})
                       for role in ("defend", "prove-wrong", "hardest-broken")],
            "blindspot": ok({"missed_angle": None}),
        }
        result = resolve_v1(stages)
        self.assertEqual(result["final_status"], "complete")
        self.assertEqual(result["final_verdict"], "consistent")
        self.assertNotIn("synthesis", result["stages"])

    def test_contested_record_uses_stored_synthesis(self):
        stages = self.contested_stages(
            synthesis={"verdict": "inconsistent", "category": "direct-mismatch",
                       "why": "doc says one; code returns two"}
        )
        self.assertEqual(resolve_v1(stages)["final_verdict"], "inconsistent")

    def test_missing_required_synthesis_abstains(self):
        result = resolve_v1(self.contested_stages(synthesis=None))
        self.assertEqual(result["final_status"], "abstain")
        self.assertIsNone(result["final_verdict"])
```

Also characterize escalated-prong preference, a blind-spot-triggered synthesis, malformed stage values, and the exact `contested` Boolean.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python3 -m unittest tests.test_bench_resolver -v`

Expected: import failure for `eval.bench.resolver`.

- [ ] **Step 3: Implement `resolve_v1` as a pure extraction of current behavior**

```python
# eval/bench/resolver.py
VERDICTS = {"consistent", "inconsistent"}
RESOLVERS = {"v1"}


def _value(stages, name):
    result = stages.get(name)
    if not isinstance(result, dict) or result.get("status") != "ok":
        return None
    value = result.get("value")
    return value if isinstance(value, dict) else None


def _abstain(stages, reason):
    return {"final_status": "abstain", "final_verdict": None, "verdict": None,
            "category": None, "why": reason, "contested": False, "stages": stages}


def resolve_v1(stages):
    snap = _value(stages, "snap")
    challenge = _value(stages, "challenge")
    blindspot = _value(stages, "blindspot")
    prong_records = stages.get("prongs_escalated") or stages.get("prongs")
    prongs = [item.get("value") for item in prong_records or []
              if isinstance(item, dict) and item.get("status") == "ok"]
    if (not snap or snap.get("verdict") not in VERDICTS or
            not challenge or type(challenge.get("cracks")) is not bool or
            not blindspot or "missed_angle" not in blindspot or len(prongs) != 3 or
            any(not isinstance(item, dict) or item.get("verdict") not in VERDICTS
                for item in prongs)):
        return _abstain(stages, "trial record is incomplete")
    missed = bool(blindspot["missed_angle"])
    votes = [snap["verdict"], *(item["verdict"] for item in prongs)]
    source = snap
    if missed or len(set(votes)) != 1:
        source = _value(stages, "synthesis")
        if not source or source.get("verdict") not in VERDICTS:
            return _abstain(stages, "synthesis response is missing required fields")
    verdict = source["verdict"]
    return {"final_status": "complete", "final_verdict": verdict, "verdict": verdict,
            "category": source.get("category"), "why": source.get("why"),
            "contested": challenge["cracks"] or missed, "stages": stages}


def resolve(stages, resolver_id):
    if resolver_id == "v1":
        return resolve_v1(stages)
    raise ValueError("resolver must be v1")
```

Move only final interpretation out of `trial.judge`; do not change prompts, stage order, escalation, model choice, or stored shape in this task. Add `resolver.py` to `artifact.JUDGE_MODULES` so provenance changes if policy changes.

- [ ] **Step 4: Run resolver and existing trial tests**

Run:

```bash
python3 -m unittest tests.test_bench_resolver tests.test_bench -v
python3 eval/bench/run_bench.py --selftest
```

Expected: all tests pass; self-test prints `selftest ok`.

- [ ] **Step 5: Commit the behavior-preserving extraction**

```bash
git add eval/bench/resolver.py eval/bench/trial.py eval/bench/artifact.py \
  tests/test_bench_resolver.py
git commit -m "refactor(eval): extract immutable trial resolver v1"
```

---

### Task 3: Add a read-only offline replay and policy-diff CLI

**Files:**
- Create: `eval/bench/replay.py`
- Create: `tests/test_bench_replay.py`
- Modify: `eval/bench/runner.py:216-222`
- Modify: `eval/bench/README.md:45-55`

**Interfaces:**
- Produces: `replay_rows(rows: list[dict], resolver_id: str) -> list[dict]`.
- Produces CLI: `python3 eval/bench/replay.py ARTIFACT... --resolver v1 --expect-stored`.
- Produces diagnostic CLI: `--compare-snap`, which reports snap-only matrices without changing artifacts or declaring snap a production resolver.
- Produces selection CLI: `python3 eval/bench/replay.py select-split --manifest PATH --split dev|holdout --labels PATH --dataset PATH... --output-dir PATH`. It selects IDs first, then joins the matching private labels, writes one JSONL per language, and refuses any ID outside the selected public split.
- Reads versioned artifacts through existing bounded readers. It never writes source artifacts and never invokes a provider CLI.

- [ ] **Step 1: Write failing replay parity tests**

```python
# tests/test_bench_replay.py
import unittest
from eval.bench.replay import replay_rows


class ReplayTests(unittest.TestCase):
    def test_v1_replay_reproduces_stored_final(self):
        row = {
            "id": "pair-1", "language": "python", "label": "consistent",
            "category": None,
            "got": {
                "final_status": "complete", "final_verdict": "consistent",
                "stages": self.unanimous_consistent_stages(),
            },
        }
        replayed = replay_rows([row], "v1")
        self.assertEqual(replayed[0]["got"]["final_verdict"], "consistent")
        self.assertEqual(row["got"]["final_verdict"], "consistent")

    def test_expect_stored_reports_a_mismatch_without_mutating_input(self):
        row = self.row(stored="inconsistent", stages=self.unanimous_consistent_stages())
        with self.assertRaisesRegex(ValueError, "pair-1: stored=inconsistent replayed=consistent"):
            replay_rows([row], "v1", expect_stored=True)
```

Add CLI tests for multiple artifacts, malformed records, unknown resolver, output ordering, `--compare-snap`, and proof that `trial.model_json` is never called.

Add selection tests that prove project membership is checked before labels are opened, a label for a holdout ID is rejected during `--split dev`, an unknown label ID is rejected, all selected IDs receive exactly one label, the output is one-language-per-file, and an optional `--context-dataset Java=PATH` may replace only the Java row's `context` field while every other source field stays byte-for-byte equal.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python3 -m unittest tests.test_bench_replay -v`

Expected: import failure for `eval.bench.replay`.

- [ ] **Step 3: Implement bounded replay**

```python
# eval/bench/replay.py
import argparse
import copy
from pathlib import Path

from .artifact import artifact_rows, load_json, MAX_ARTIFACT_BYTES
from .resolver import resolve


def replay_rows(rows, resolver_id, expect_stored=False):
    replayed = copy.deepcopy(rows)
    for original, row in zip(rows, replayed):
        got = original.get("got") or {}
        decision = resolve(got.get("stages") or {}, resolver_id)
        if expect_stored and (got.get("final_status"), got.get("final_verdict")) != (
                decision["final_status"], decision["final_verdict"]):
            raise ValueError(
                f"{original.get('id')}: stored={got.get('final_verdict')} "
                f"replayed={decision['final_verdict']}"
            )
        row["got"] = decision
    return replayed
```

The CLI must print only counts, matrices, IDs of mismatches, resolver ID, and artifact hashes. It must not print code, documentation, or free-form stage reasoning.

Implement `select-split` with `load_split_manifest`, bounded JSONL reads, and this order: validate the public manifest and source datasets; compute the selected ID set; validate an optional context dataset has the same IDs and identical non-`context` fields; only then open the requested private label file; require exact label coverage; write language files atomically. Never accept a `--split` value other than `dev` or `holdout`.

- [ ] **Step 4: Run focused tests and the frozen five-artifact parity check**

Run:

```bash
python3 -m unittest tests.test_bench_replay -v
python3 eval/bench/replay.py \
  /Users/cujo253/evergreen-benchmark-archive/cb24647f7c62b9704d10c97e615005d924c005f2/bench-cascade-java-trial-codex-gpt-5.6-sol.rows-885.a03f1b33c3f3e13ee60226298dd1dc83d611ba98ad625d86f22157544175a090.json \
  /Users/cujo253/evergreen-benchmark-archive/cb24647f7c62b9704d10c97e615005d924c005f2/bench-codocbench-validated-trial-codex-gpt-5.6-sol.rows-332.b56b5541ad9e9ad31df3f756e6eb05509241b44651d159396ed0e93ffb566cc9.json \
  /Users/cujo253/evergreen-benchmark-archive/cb24647f7c62b9704d10c97e615005d924c005f2/bench-codocbench-ts-validated-trial-codex-gpt-5.6-sol.rows-284.16ea13944ce98c9fa62026328903e1babd82a92daf547bd0d45a79b662cd1c94.json \
  /Users/cujo253/evergreen-benchmark-archive/cb24647f7c62b9704d10c97e615005d924c005f2/bench-codocbench-rust-validated-trial-codex-gpt-5.6-sol.rows-304.88b499de0f0fcf037d54e00dc9c9d36183e6adb4c731f2441f300623a2929e0d.json \
  /Users/cujo253/evergreen-benchmark-archive/cb24647f7c62b9704d10c97e615005d924c005f2/bench-codocbench-go-validated-trial-codex-gpt-5.6-sol.rows-299.b04a027dee961480a1fe3f8505b1b9bfb5d43ffd80b3e6b50861639246ed91ef.json \
  --resolver v1 --expect-stored --compare-snap
```

Expected parity line: `v1 parity: 2103 completed rows reproduced; 0 differences; 1 stored abstention preserved`.

Expected snap diagnostic:

| Language | Frozen v1 P/R/F1 | Snap diagnostic P/R/F1 |
|---|---:|---:|
| Java | .202 / .343 / .254 | .235 / .400 / .296 |
| Python | .129 / 1.000 / .228 | .155 / 1.000 / .269 |
| TypeScript | .213 / 1.000 / .352 | .291 / 1.000 / .451 |
| Rust | .464 / .684 / .553 | .515 / .895 / .654 |
| Go | .261 / .750 / .387 | .235 / .750 / .358 |

Do not interpret these provisional-label metrics as truth; this command proves replay and demonstrates that the multi-stage policy needs adjudicated testing.

- [ ] **Step 5: Commit replay tooling**

```bash
git add eval/bench/replay.py eval/bench/runner.py eval/bench/README.md \
  tests/test_bench_replay.py
git commit -m "feat(eval): replay trial decisions without model calls"
```

---

### Task 4: Add semantic `unverified` and honest quality gates

**Files:**
- Modify: `eval/bench/resolver.py`
- Modify: `eval/bench/metrics.py`
- Modify: `eval/bench/report.py`
- Modify: `eval/bench/artifact.py`
- Modify: `tests/test_bench_resolver.py`
- Modify: `tests/test_bench.py`
- Modify: `tests/test_bench_artifact.py`

**Interfaces:**
- A provider-complete semantic result has `final_status="complete"` and `semantic_status="decided" | "unverified"`.
- A decided result has `final_verdict="consistent" | "inconsistent"`; an unverified result has `final_verdict=None`.
- Infrastructure failure remains `final_status="abstain"`, `semantic_status="not-evaluated"`, `final_verdict=None`.
- `metrics.score(rows)` adds `provider_completed`, `provider_abstained`, `provider_completion_rate`, `decided`, `unverified`, and `decision_rate` while retaining the existing confusion-matrix keys.
- `report.py` adds `--decision-threshold`, `--precision-threshold`, `--recall-threshold`, and `--f1-threshold`, applied independently per language.

- [ ] **Step 1: Write failing semantic-status and anti-gaming tests**

```python
def test_unverified_is_provider_complete_but_not_a_confusion_matrix_decision(self):
    rows = [
        {"language": "Java", "label": "inconsistent", "category": None,
         "final_status": "complete", "semantic_status": "unverified",
         "final_verdict": None},
        {"language": "Java", "label": "consistent", "category": None,
         "final_status": "complete", "semantic_status": "decided",
         "final_verdict": "consistent"},
    ]
    result = metrics.score(rows)
    self.assertEqual(result["provider_completed"], 2)
    self.assertEqual(result["unverified"], 1)
    self.assertEqual(result["decided"], 1)
    self.assertEqual(result["decision_rate"], 0.5)
    self.assertEqual((result["tp"], result["fp"], result["fn"], result["tn"]),
                     (0, 0, 0, 1))

def test_report_rejects_high_metrics_obtained_by_unverifying_hard_rows(self):
    with self.assertRaisesRegex(ValueError, "Java decision coverage"):
        report.validate_quality(
            self.rows_with_one_decision_and_nine_unverified(),
            coverage_threshold=0.99, decision_threshold=0.99,
            precision_threshold=0.60, recall_threshold=0.60, f1_threshold=0.60,
        )
```

Also test malformed combinations: `decided` with null verdict, `unverified` with a verdict, `abstain` marked decided, and legacy v1 rows without `semantic_status` mapping to decided only when their old final verdict is valid.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python3 -m unittest tests.test_bench_resolver tests.test_bench tests.test_bench_artifact -v`

Expected: failures for missing semantic metrics and quality arguments.

- [ ] **Step 3: Implement semantic accounting without changing v1 replay**

```python
# eval/bench/metrics.py, inside score
def provider_complete(row):
    return row.get("final_status") in (None, "complete")


def semantic_decision(row):
    status = row.get("semantic_status")
    verdict = row.get("final_verdict") if row.get("final_status") is not None \
        else row.get("verdict")
    if status is None:  # legacy v1 artifact
        return verdict in VERDICTS
    return status == "decided" and verdict in VERDICTS


provider_rows = [row for row in rows if provider_complete(row)]
decided_rows = [row for row in provider_rows if semantic_decision(row)]
unverified_rows = [row for row in provider_rows
                   if row.get("semantic_status") == "unverified"]
```

Calculate the confusion matrix only from `decided_rows`. Define `decision_rate = decided / provider_completed`, not `decided / attempted`, so provider failure and semantic insufficiency remain separately visible.

- [ ] **Step 4: Run focused tests and verify GREEN**

Run:

```bash
python3 -m unittest tests.test_bench_resolver tests.test_bench tests.test_bench_artifact -v
python3 eval/bench/run_bench.py --selftest
```

Expected: all tests pass and the self-test prints `selftest ok`.

- [ ] **Step 5: Commit outcome and gate semantics**

```bash
git add eval/bench/resolver.py eval/bench/metrics.py eval/bench/report.py \
  eval/bench/artifact.py tests/test_bench_resolver.py tests/test_bench.py \
  tests/test_bench_artifact.py
git commit -m "feat(eval): separate unverified claims from provider failures"
```

---

### Task 5: Implement evidence-aware resolver `v2` and balanced trial prompts

**Files:**
- Modify: `eval/bench/resolver.py`
- Modify: `eval/bench/trial.py:289-389,418-507`
- Modify: `eval/bench/model-output.schema.json`
- Modify: `tests/test_bench_resolver.py`
- Modify: `tests/test_bench.py`

**Interfaces:**
- `resolve_v2(stages: dict) -> dict` accepts `consistent`, `inconsistent`, or `unverified` semantic verdicts.
- Every snap, prong, and synthesis value has exactly `verdict`, `proof`, `claim`, `evidence`, plus the existing role/category fields where applicable.
- `proof` is exactly `direct`, `delegated`, or `requires-unseen-code`.
- `inconsistent` is valid only with `proof="direct"`, a nonempty atomic `claim`, nonempty code `evidence`, and category `direct-mismatch` or `over-promise`.
- `consistent` is decided only with `proof="direct"`; otherwise the resolver returns semantic `unverified`.
- The three v2 prongs are `defend`, `prove-wrong`, and `evidence-auditor`. Remove `hardest-broken` from v2 only; retain its name in v1 replay records.
- Any landed challenge, non-null blind spot, vote disagreement, or non-direct vote forces synthesis. No landed challenge may take the unanimous fast path.

- [ ] **Step 1: Write failing resolver-v2 tests**

```python
class ResolverV2Tests(unittest.TestCase):
    def test_direct_unanimous_inconsistency_is_decided(self):
        stages = self.v2_stages(
            snap=self.v2_value("inconsistent", "direct"),
            prongs=[self.v2_value("inconsistent", "direct", role=role)
                    for role in ("defend", "prove-wrong", "evidence-auditor")],
            challenge={"cracks": False, "proof": "direct", "why": "defense fails"},
            blindspot={"missed_angle": None},
        )
        result = resolve_v2(stages)
        self.assertEqual(result["semantic_status"], "decided")
        self.assertEqual(result["final_verdict"], "inconsistent")

    def test_delegated_claim_is_unverified_not_consistent(self):
        stages = self.v2_stages(
            snap=self.v2_value("unverified", "delegated"),
            prongs=[self.v2_value("unverified", "delegated", role=role)
                    for role in ("defend", "prove-wrong", "evidence-auditor")],
            challenge={"cracks": False, "proof": "delegated", "why": "callee absent"},
            blindspot={"missed_angle": None},
            synthesis=self.v2_value("unverified", "delegated"),
        )
        result = resolve_v2(stages)
        self.assertEqual(result["final_status"], "complete")
        self.assertEqual(result["semantic_status"], "unverified")
        self.assertIsNone(result["final_verdict"])

    def test_landed_challenge_requires_synthesis_even_when_votes_are_unanimous(self):
        stages = self.v2_unanimous_stages(challenge_cracks=True)
        del stages["synthesis"]
        result = resolve_v2(stages)
        self.assertEqual(result["final_status"], "abstain")
        self.assertIn("synthesis", result["why"])

    def test_inconsistent_without_direct_proof_is_unverified(self):
        result = resolve_v2(self.v2_synthesis_stages(
            verdict="inconsistent", proof="requires-unseen-code"
        ))
        self.assertEqual(result["semantic_status"], "unverified")
        self.assertIsNone(result["final_verdict"])
```

Add prompt-capture tests that assert all v2 stages request the structured fields, contain the inert JSON envelope, state that ordinary summaries describe documented/default behavior unless explicitly universal, and prohibit treating a hypothetical optional input or unseen callee as a direct contradiction.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python3 -m unittest tests.test_bench_resolver tests.test_bench.PromptIsolationTests -v`

Expected: failures because `resolve_v2` and the `evidence-auditor` prompt do not exist.

- [ ] **Step 3: Implement strict v2 record validation**

```python
# eval/bench/resolver.py
V2_VERDICTS = {"consistent", "inconsistent", "unverified"}
PROOFS = {"direct", "delegated", "requires-unseen-code"}
DRIFT_CATEGORIES = {"direct-mismatch", "over-promise"}


def _valid_v2_value(value, role=None):
    if not isinstance(value, dict) or value.get("verdict") not in V2_VERDICTS:
        return False
    if value.get("proof") not in PROOFS:
        return False
    if role is not None and value.get("role") != role:
        return False
    if not isinstance(value.get("claim"), str) or not value["claim"].strip():
        return False
    if not isinstance(value.get("evidence"), str) or not value["evidence"].strip():
        return False
    if value["verdict"] == "inconsistent":
        return value["proof"] == "direct" and value.get("category") in DRIFT_CATEGORIES
    return value.get("category") is None


def _semantic_unverified(stages, source):
    return {"final_status": "complete", "semantic_status": "unverified",
            "final_verdict": None, "verdict": None, "category": None,
            "why": source.get("evidence"), "contested": True, "stages": stages}
```

`resolve_v2` must validate every role by exact name, choose escalated prongs when present, force synthesis under the four declared conditions, validate the selected source, then return a decided result only when the selected source has direct proof.

- [ ] **Step 4: Replace the duplicate prosecution prompt in v2**

Use this exact role text in `trial.py`:

```python
PRONGS_V2 = {
    "defend": (
        "Find the strongest ordinary reading under which the documentation remains true. "
        "Do not invent unseen behavior."
    ),
    "prove-wrong": (
        "Find one atomic documentation claim directly contradicted by code shown in the "
        "pair or trusted context. Hypothetical inputs and absent callees are not direct proof."
    ),
    "evidence-auditor": (
        "Decide whether the supplied code is sufficient to settle the claim. Mark delegated "
        "or unseen behavior unverified; classify a verdict only when the evidence is direct."
    ),
}
```

Every v2 verdict prompt must include:

```text
Documentation is an ordinary behavioral summary unless it explicitly says every input,
configuration, platform, or edge case. Code doing more than the documentation says is consistent.
A finding requires one atomic claim and code shown here that makes that claim impossible. Missing
callee bodies, hypothetical optional inputs, or merely arguable wording are not direct proof; use
unverified with proof delegated or requires-unseen-code.
```

Keep the current inert-envelope and Codex no-tools boundaries unchanged.

- [ ] **Step 5: Integrate resolver selection without changing v1**

Add `resolver_id` to the `models/settings` dictionary. `trial.judge` uses `PRONGS` and `resolve_v1` for `v1`, and `PRONGS_V2` and `resolve_v2` for `v2`. Default to `v1` until Task 7 explicitly freezes `v2`; an absent resolver value must therefore preserve old local self-tests.

- [ ] **Step 6: Run focused and full cheap tests**

Run:

```bash
python3 -m unittest tests.test_bench_resolver tests.test_bench -v
python3 eval/bench/run_bench.py --selftest
python3 -m unittest discover -s tests -p 'test_*.py'
```

Expected: all commands pass; no provider process appears in the test output or process list.

- [ ] **Step 7: Commit resolver v2 and prompt protocol**

```bash
git add eval/bench/resolver.py eval/bench/trial.py \
  eval/bench/model-output.schema.json tests/test_bench_resolver.py tests/test_bench.py
git commit -m "feat(eval): require direct evidence for semantic drift"
```

---

### Task 6: Build bounded, label-blind Java context from Git objects

**Files:**
- Create: `eval/bench/java_context.py`
- Create: `tests/test_bench_java_context.py`
- Modify: `eval/bench/cascade_to_jsonl.py`
- Modify: `eval/bench/trial.py:16-100`
- Modify: `eval/bench/runner.py:166-178`
- Modify: `eval/bench/artifact.py`
- Modify: `tests/test_bench.py`
- Modify: `tests/test_bench_artifact.py`

**Interfaces:**
- Produces: `build_context(pair: dict, mirror_root: Path, *, max_candidates: int = 128, max_bytes: int = 65536, window_lines: int = 200) -> dict`.
- Context shape: `{"status": "available", "source": {"repo": str, "commit": str, "path": str, "sha256": str}, "snippets": [{"kind": "enclosing-java-source", "path": str, "start_line": int, "end_line": int, "sha256": str, "text": str}]}`.
- Failure shape: `{"status": "unavailable", "reason": "repository-missing" | "commit-missing" | "source-not-found" | "source-ambiguous" | "source-too-large"}`.
- Reads only local bare/non-bare Git objects with `git -C REPO show`, `grep`, and `ls-tree` argv lists. It never clones, fetches, checks out, or writes to the source mirror.
- `cascade_to_jsonl.py --mirror-root PATH --with-context` appends the context object to each output row. The complete resulting JSONL remains the hashed dataset input, so no separate mutable context file exists.

- [ ] **Step 1: Write failing deterministic context tests using a temporary Git repository**

```python
# tests/test_bench_java_context.py
import hashlib
import subprocess
import tempfile
import unittest
from pathlib import Path

from eval.bench.java_context import build_context


class JavaContextTests(unittest.TestCase):
    def test_extracts_bounded_enclosing_source_at_exact_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repo = root / "org" / "repo"
            repo.mkdir(parents=True)
            subprocess.run(["git", "-C", str(repo), "init", "-q"], check=True)
            source = repo / "Example.java"
            source.write_text(
                "class Example {\n"
                "  private static final int LIMIT = 9;\n"
                "  int parseInt(char[] ch, int off, int len) { return LIMIT; }\n"
                "}\n"
            )
            subprocess.run(["git", "-C", str(repo), "add", "Example.java"], check=True)
            subprocess.run(["git", "-C", str(repo), "-c", "user.name=t", "-c",
                            "user.email=t@example.invalid", "commit", "-qm", "fixture"],
                           check=True)
            commit = subprocess.check_output(
                ["git", "-C", str(repo), "rev-parse", "HEAD"], text=True
            ).strip()
            pair = {"id": f"org/repo/{commit}/1#0", "func": "parseInt",
                    "code": "int parseInt(char[] ch, int off, int len) { return LIMIT; }",
                    "doc": "returns a parsed integer", "language": "Java"}

            context = build_context(pair, root, max_bytes=4096, window_lines=20)

            self.assertEqual(context["status"], "available")
            self.assertEqual(context["source"]["path"], "Example.java")
            self.assertIn("LIMIT = 9", context["snippets"][0]["text"])
            self.assertEqual(context["snippets"][0]["sha256"], hashlib.sha256(
                context["snippets"][0]["text"].encode()
            ).hexdigest())
```

Add tests for repository missing, commit missing, two matching Java files producing `source-ambiguous`, no normalized match, output over `max_bytes`, symlink mirror escape, subprocess timeout, malicious ID components, and proof that labels/categories do not affect output bytes.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python3 -m unittest tests.test_bench_java_context -v`

Expected: import failure for `eval.bench.java_context`.

- [ ] **Step 3: Implement conservative source location and bounded window extraction**

```python
# eval/bench/java_context.py
import hashlib
import re
import subprocess
from pathlib import Path

from .artifact import _process_bytes

SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9._-]+$")
SAFE_COMMIT = re.compile(r"^[0-9a-f]{40}$")
MAX_GIT_LIST_BYTES = 1024 * 1024
MAX_JAVA_SOURCE_BYTES = 4 * 1024 * 1024


def _parts(pair_id):
    parts = pair_id.split("/")
    if (len(parts) < 4 or not SAFE_COMPONENT.fullmatch(parts[0]) or
            not SAFE_COMPONENT.fullmatch(parts[1]) or not SAFE_COMMIT.fullmatch(parts[2])):
        raise ValueError("CASCADE pair id has unsafe components")
    return parts[0], parts[1], parts[2]


def _normalized(text):
    return " ".join(text.split())


def _git(repo, limit, *args):
    return _process_bytes(["git", "-C", str(repo), *args], limit).decode(
        "utf-8", "strict"
    )


def build_context(pair, mirror_root, *, max_candidates=128, max_bytes=65536,
                  window_lines=200):
    owner, project, commit = _parts(pair["id"])
    repo = (Path(mirror_root) / owner / project).resolve()
    root = Path(mirror_root).resolve()
    if root not in repo.parents or not repo.is_dir():
        return {"status": "unavailable", "reason": "repository-missing"}
    try:
        _git(repo, 128, "cat-file", "-e", f"{commit}^{{commit}}")
    except (OSError, UnicodeError):
        return {"status": "unavailable", "reason": "commit-missing"}
    try:
        listed = _git(
            repo, MAX_GIT_LIST_BYTES, "grep", "-l", "-F", f"{pair['func']}",
            commit, "--", "*.java"
        )
    except (OSError, UnicodeError, ValueError, subprocess.TimeoutExpired):
        return {"status": "unavailable", "reason": "source-not-found"}
    paths = sorted({line for line in listed.splitlines() if line})
    if len(paths) > max_candidates:
        return {"status": "unavailable", "reason": "source-ambiguous"}
    target = _normalized(pair["code"])
    matches = []
    for path in paths:
        try:
            source = _git(repo, MAX_JAVA_SOURCE_BYTES, "show", f"{commit}:{path}")
        except (OSError, UnicodeError, ValueError):
            continue
        if target in _normalized(source):
            matches.append((path, source))
    if not matches:
        return {"status": "unavailable", "reason": "source-not-found"}
    if len(matches) != 1:
        return {"status": "unavailable", "reason": "source-ambiguous"}
    path, source = matches[0]
    lines = source.splitlines(keepends=True)
    anchor_pattern = re.compile(rf"\b{re.escape(pair['func'])}\s*\(")
    anchors = [i for i, line in enumerate(lines) if anchor_pattern.search(line)]
    windows = []
    for anchor in anchors:
        start = max(0, anchor - window_lines)
        end = min(len(lines), anchor + window_lines + 1)
        text = "".join(lines[start:end])
        if target in _normalized(text):
            windows.append((start, end, text))
    if len(windows) != 1:
        return {"status": "unavailable", "reason": "source-ambiguous"}
    start, end, text = windows[0]
    if len(text.encode()) > max_bytes:
        return {"status": "unavailable", "reason": "source-too-large"}
    return {
        "status": "available",
        "source": {"repo": f"{owner}/{project}", "commit": commit, "path": path,
                   "sha256": hashlib.sha256(source.encode()).hexdigest()},
        "snippets": [{"kind": "enclosing-java-source", "path": path,
                      "start_line": start + 1, "end_line": end,
                      "sha256": hashlib.sha256(text.encode()).hexdigest(), "text": text}],
    }
```

Keep this as a conservative exact-source window; do not add a Java parser or recursive callee crawler unless a measured, label-blind context-availability failure proves the window insufficient. Do not call `shell=True`.

- [ ] **Step 4: Add context to the inert pair envelope with strict limits**

`trial._validated_pair_data` must accept optional `context`, validate the exact keys and status variants above, cap its canonical JSON encoding at `65536` bytes, and include it inside the existing SHA-256/byte-count envelope. A row without context remains valid and receives `{"status":"unavailable","reason":"not-supplied"}` only in the prompt data; do not mutate the original row.

- [ ] **Step 5: Bind context to dataset and artifact provenance**

Because context is embedded in JSONL, the existing dataset SHA-256 binds its complete content. Add `context_protocol: "java-git-window-v1" | "none"` to runner settings and metadata, and make resume reject a mismatch. Add `resolver.py` and `java_context.py` to `JUDGE_MODULES` because both influence a v2 decision.

- [ ] **Step 6: Run focused and full cheap tests**

Run:

```bash
python3 -m unittest tests.test_bench_java_context tests.test_bench \
  tests.test_bench_artifact -v
python3 eval/bench/run_bench.py --selftest
python3 -m unittest discover -s tests -p 'test_*.py'
```

Expected: all tests pass; no network access and no provider process.

- [ ] **Step 7: Build a Java development-context dataset without reading labels**

Run:

```bash
python3 eval/bench/cascade_to_jsonl.py \
  "$HOME/benchmark-sources/cascade-dataset" \
  --mirror-root "$HOME/benchmark-sources/git-mirrors" \
  --with-context > "$HOME/evergreen-private-labels/cascade-java-context-v1.jsonl"
shasum -a 256 "$HOME/evergreen-private-labels/cascade-java-context-v1.jsonl"
```

Expected: 885 rows; each row has context status; the command prints no labels, model outputs, or reasoning to stdout beyond JSONL. Record the SHA-256 in the private split manifest before any model call.

If fewer than 99% of rows have `context.status="available"`, stop. Report the exact unavailable-reason counts and improve source location only with another deterministic, label-blind Git strategy; do not use labels to choose files.

- [ ] **Step 8: Commit Java context support**

```bash
git add eval/bench/java_context.py eval/bench/cascade_to_jsonl.py eval/bench/trial.py \
  eval/bench/runner.py eval/bench/artifact.py tests/test_bench_java_context.py \
  tests/test_bench.py tests/test_bench_artifact.py
git commit -m "feat(eval): add bounded label-blind Java context"
```

---

### Task 7: Bind resolver v2 into frozen runs and clear all cheap gates

**Files:**
- Modify: `eval/bench/runner.py`
- Modify: `eval/bench/frozen_run.py`
- Modify: `eval/bench/artifact.py`
- Modify: `tests/test_bench_frozen.py`
- Modify: `tests/test_bench_artifact.py`
- Modify: `eval/bench/README.md`

**Interfaces:**
- `frozen_run.py --resolver v1|v2` defaults to `v1` until the implementation commit explicitly freezes `v2`.
- `frozen_run.py --split dev|holdout` is required with resolver `v2`; every input row ID must belong to that split in the public manifest. The derived input JSONL has its own dataset hash, while the manifest hash binds the predeclared membership.
- Paid artifacts include `settings.resolver`, `settings.context_protocol`, resolver/Judge SHA-256, dataset SHA-256, and split-manifest SHA-256.
- Resume requires exact equality of all those fields.
- `--selftest`, replay, split validation, and Java context construction remain provider-free.

- [ ] **Step 1: Write failing frozen-run provenance tests**

```python
def test_frozen_v2_run_binds_resolver_context_and_split_hashes(self):
    metadata = artifact.artifact_metadata(
        self.dataset, self.repo,
        {"provider": "codex", "models": {"strong": "gpt", "cheap": "gpt"},
         "concurrency": 1, "resolver": "v2",
         "context_protocol": "java-git-window-v1",
         "split_manifest_sha256": "a" * 64},
    )
    self.assertEqual(metadata["settings"]["resolver"], "v2")
    self.assertEqual(metadata["settings"]["context_protocol"], "java-git-window-v1")

def test_resume_rejects_v1_artifact_for_v2_run(self):
    with self.assertRaisesRegex(ValueError, "provenance does not match"):
        artifact.resume_state(self.v1_document, self.v2_metadata, self.dataset_rows)
```

Add tests rejecting an unknown resolver, missing split hash for v2, Java v2 without context protocol, non-Java context protocol, and direct runner invocation that bypasses `frozen_run.py`.

- [ ] **Step 2: Run focused tests and verify RED**

Run: `python3 -m unittest tests.test_bench_frozen tests.test_bench_artifact -v`

Expected: failures for missing resolver/context/split provenance.

- [ ] **Step 3: Implement the minimum CLI and metadata wiring**

Add these arguments to `frozen_run.py`:

```python
parser.add_argument("--resolver", choices=("v1", "v2"), default="v1")
parser.add_argument("--split-manifest")
parser.add_argument("--split", choices=("dev", "holdout"))
parser.add_argument("--context-protocol", choices=("none", "java-git-window-v1"),
                    default="none")
```

For `v2`, require `--split-manifest` and `--split`, validate every dataset row ID belongs to the declared split before launching, hash both manifest and derived dataset with the existing regular-file helper, and export the frozen values through the existing inherited launcher handshake. Keep the one-language lane, global lock, archive mirroring, disk floor, and Git identity checks unchanged.

- [ ] **Step 4: Run every cheap gate before any provider call**

Run:

```bash
python3 -m unittest tests.test_bench_split_manifest tests.test_bench_resolver \
  tests.test_bench_replay tests.test_bench_java_context tests.test_bench \
  tests.test_bench_artifact tests.test_bench_frozen -v
python3 eval/bench/run_bench.py --selftest
python3 -m unittest discover -s tests -p 'test_*.py'
bash tests/hooks.sh
bash tests/action.sh
git diff --check
```

Expected: every command exits `0`; unittest ends in `OK`; scripts print their normal pass summaries; `git diff --check` is silent.

- [ ] **Step 5: Run offline `v1` parity again after v2 integration**

Run the exact five-artifact replay command from Task 3.

Expected: `v1 parity: 2103 completed rows reproduced; 0 differences; 1 stored abstention preserved`.

- [ ] **Step 6: Enforce the no-paid-call gate**

Inspect the shell history/process log for this worktree and confirm no `claude -p`, `codex exec`, or `frozen_run.py` paid benchmark command ran during Tasks 1–6. Record this sentence in the commit message body: `Cheap gates completed before any provider-backed detector run.`

- [ ] **Step 7: Commit frozen v2 plumbing**

```bash
git add eval/bench/runner.py eval/bench/frozen_run.py eval/bench/artifact.py \
  eval/bench/README.md tests/test_bench_frozen.py tests/test_bench_artifact.py
git commit -m "feat(eval): freeze resolver and split provenance"
```

---

### Task 8: Run a bounded adjudicated development canary and freeze the candidate

**Files:**
- Modify before candidate freeze: `skills/evergreen/SKILL.md`
- Modify before candidate freeze: `skills/evergreen/DIGEST.md`
- Modify before candidate freeze: `AGENTS.md`
- Modify before candidate freeze: `docs/DESIGN.md`
- Modify before candidate freeze: `README.md`
- Modify before candidate freeze: `eval/README.md`
- Modify before candidate freeze: `eval/bench/README.md`
- Modify before candidate freeze: `eval/bench/results-current.md`

**Interfaces:**
- Consumes private development labels at `$HOME/evergreen-private-labels/human-v1-dev.labels.jsonl` and the public split manifest `eval/bench/splits/human-v1-public.json`.
- The private labels file has exactly one JSON object per line: `{"id": str, "label": "consistent" | "inconsistent", "category": null | "direct-mismatch" | "over-promise"}`.
- Produces no more than 200 provider-scored development rows total, with at least 20 adjudicated positives and 20 adjudicated negatives per language represented in the canary. If 200 rows cannot meet that condition, stop and expand adjudication before running.
- Produces one frozen candidate commit only if every language clears the practical and coverage gates.

- [ ] **Step 1: Validate the development labels and prove holdout labels remain inaccessible**

Run:

```bash
python3 eval/bench/split_manifest.py \
  eval/bench/splits/human-v1-public.json \
  eval/bench/cascade-java.jsonl \
  eval/bench/codocbench-validated.jsonl \
  eval/bench/codocbench-ts-validated.jsonl \
  eval/bench/codocbench-rust-validated.jsonl \
  eval/bench/codocbench-go-validated.jsonl
test -r "$HOME/evergreen-private-labels/human-v1-dev.labels.jsonl"
test ! -e "$HOME/evergreen-private-labels/human-v1-holdout.labels.jsonl"
```

Expected: public manifest validates, dev labels are readable, and the final command succeeds because holdout labels are not present in the execution environment.

- [ ] **Step 2: Build per-language development JSONL files after ID selection**

Use a new read-only `replay.py select-split` subcommand covered by Task 3 tests:

```bash
python3 eval/bench/replay.py select-split \
  --manifest eval/bench/splits/human-v1-public.json --split dev \
  --labels "$HOME/evergreen-private-labels/human-v1-dev.labels.jsonl" \
  --dataset eval/bench/cascade-java.jsonl \
  --dataset eval/bench/codocbench-validated.jsonl \
  --dataset eval/bench/codocbench-ts-validated.jsonl \
  --dataset eval/bench/codocbench-rust-validated.jsonl \
  --dataset eval/bench/codocbench-go-validated.jsonl \
  --context-dataset \
    "Java=$HOME/evergreen-private-labels/cascade-java-context-v1.jsonl" \
  --output-dir "$HOME/evergreen-private-labels/dev-v2"
```

Expected: five one-language JSONL files, no more than 200 total rows, at least 20 positives and 20 negatives per language, and a printed SHA-256 for each. The selector must determine split membership before opening labels, may replace only Java `context` from the verified derivative dataset, and must never read a holdout file.

- [ ] **Step 3: Synchronize the product skill with the exact v2 prompt policy**

Make these exact semantic changes in all three instruction surfaces before the canary, because `trial.py` embeds `SKILL.md` into its model prompt:

```text
Three blind reads use distinct roles: strongest consistent reading, direct contradiction search,
and evidence sufficiency. A drift finding requires one atomic documentation claim plus direct code
that makes it impossible. Delegated behavior, absent callees, hypothetical optional inputs, and
merely arguable wording are unverified, not drift or clean. A landed challenge always reaches the
final synthesis; it cannot pass through a unanimous fast path.
```

Update `SKILL.md`, `DIGEST.md`, and `AGENTS.md` together. Update `docs/DESIGN.md` with resolver `v1`/`v2`, Java context, semantic `unverified`, and label/holdout boundaries. Change living documentation from “current judge” to “0.4.0 baseline at `cb24647`”; keep every old number and provenance hash unchanged and do not present the development canary as a published benchmark.

- [ ] **Step 4: Run cheap tests again after changing the embedded skill text**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
bash tests/hooks.sh
bash tests/action.sh
python3 eval/bench/run_bench.py --selftest
git diff --check
```

Expected: all commands pass. If changing `SKILL.md` changes a prompt-capture expectation, update the test first, confirm the expected RED, then make the smallest synchronized instruction change.

- [ ] **Step 5: Commit and freeze a clean, remotely backed candidate before model calls**

```bash
git add skills/evergreen/SKILL.md skills/evergreen/DIGEST.md AGENTS.md docs/DESIGN.md \
  README.md eval/README.md eval/bench/README.md eval/bench/results-current.md
git commit -m "feat: require direct evidence before flagging doc drift"
git status --short
git rev-parse HEAD
git branch --show-current
```

Expected: clean worktree, a named non-managed branch, and the exact candidate commit already present as a remote ref tip. If the commit is not remotely backed, stop and request explicit push authority; do not bypass `frozen_run.py`.

- [ ] **Step 6: Run the five small development lanes serially**

For each file under `$HOME/evergreen-private-labels/dev-v2`, run one lane at a time:

```bash
python3 eval/bench/frozen_run.py \
  --dataset "$HOME/evergreen-private-labels/dev-v2/Java.jsonl" \
  --archive-dir "$HOME/evergreen-benchmark-archive" \
  --provider codex --strong-model gpt-5.6-sol --cheap-model gpt-5.6-sol \
  --concurrency 4 --resolver v2 \
  --split-manifest eval/bench/splits/human-v1-public.json \
  --split dev \
  --context-protocol java-git-window-v1
```

For Python, TypeScript, Rust, and Go, use `--context-protocol none`. Do not run lanes concurrently.

Expected: each lane completes with external archived checkpoints and exact candidate provenance.

- [ ] **Step 7: Apply the predeclared development gate**

Run:

```bash
python3 eval/bench/report.py "$HOME/evergreen-private-labels/dev-v2/out/"*.json \
  --require-language Java --require-language Python --require-language typescript \
  --require-language rust --require-language go \
  --coverage-threshold 0.99 --decision-threshold 0.99 \
  --precision-threshold 0.60 --recall-threshold 0.60 --f1-threshold 0.60 \
  --markdown "$HOME/evergreen-private-labels/dev-results-v2.md"
```

Expected: `Publication status: PASS` and every language independently meets all five thresholds.

If any language fails, stop. Use only the development labels and structured stage outcomes to diagnose; add one failing fixture first, change one prompt/policy/context behavior, rerun Tasks 5–7, synchronize all instruction surfaces, commit a new candidate, and repeat the bounded development canary. Never inspect holdout labels.

- [ ] **Step 8: Preserve the passing development report outside the repository**

Keep `$HOME/evergreen-private-labels/dev-results-v2.md` with the external archived canary artifacts. Do not edit or commit any repository file after the passing canary. Any change to `trial.py`, `resolver.py`, `SKILL.md`, the Git tree, datasets, prompts, or context invalidates the canary and requires a new candidate/run.

- [ ] **Step 9: Run Evergreen's own documentation check and full suite**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
bash tests/hooks.sh
bash tests/action.sh
python3 eval/bench/run_bench.py --selftest
git diff --check
git diff -- skills/evergreen/SKILL.md skills/evergreen/DIGEST.md AGENTS.md \
  docs/DESIGN.md README.md eval/README.md eval/bench/README.md \
  eval/bench/results-current.md
```

Expected: all automated gates pass; the three instruction surfaces describe the same resolver-v2 rule; the old report is explicitly historical.

- [ ] **Step 10: Reconfirm the canary commit is still exact**

```bash
test -z "$(git status --short)"
test "$(git rev-parse HEAD)" = \
  "$(git rev-parse "origin/$(git branch --show-current)")"
```

Expected: both commands exit `0`. Task 9 must use this exact commit and tree.

---

### Task 9: Reveal the untouched holdout, run one full frozen evaluation, and publish only on PASS

**Files:**
- Modify only after PASS: `eval/bench/results-current.md`
- Modify only after PASS: `README.md`
- Modify only after PASS: `eval/README.md`
- Modify only after PASS: `eval/bench/README.md`

**Interfaces:**
- Consumes the frozen candidate commit from Task 8, public split manifest, and newly mounted private holdout labels at `$HOME/evergreen-private-labels/human-v1-holdout.labels.jsonl`.
- Produces five externally archived final artifacts and one generated Markdown report.
- The candidate code, prompts, resolver, context builder, datasets, split manifest, and settings may not change between holdout-label reveal and final report generation.

- [ ] **Step 1: Verify the frozen boundary before mounting labels**

Run:

```bash
git status --short
git rev-parse HEAD
git show-ref --verify "refs/remotes/origin/$(git branch --show-current)"
python3 -m unittest discover -s tests -p 'test_*.py'
python3 eval/bench/run_bench.py --selftest
```

Expected: clean worktree, candidate commit equals its remote ref tip, tests pass, and self-test prints `selftest ok`.

- [ ] **Step 2: Mount and validate holdout labels without editing the candidate**

Run:

```bash
test -r "$HOME/evergreen-private-labels/human-v1-holdout.labels.jsonl"
python3 eval/bench/replay.py select-split \
  --manifest eval/bench/splits/human-v1-public.json --split holdout \
  --labels "$HOME/evergreen-private-labels/human-v1-holdout.labels.jsonl" \
  --dataset eval/bench/cascade-java.jsonl \
  --dataset eval/bench/codocbench-validated.jsonl \
  --dataset eval/bench/codocbench-ts-validated.jsonl \
  --dataset eval/bench/codocbench-rust-validated.jsonl \
  --dataset eval/bench/codocbench-go-validated.jsonl \
  --context-dataset \
    "Java=$HOME/evergreen-private-labels/cascade-java-context-v1.jsonl" \
  --output-dir "$HOME/evergreen-private-labels/holdout-v2"
git status --short
```

Expected: five one-language holdout files with printed hashes; repository remains clean.

- [ ] **Step 3: Run exactly one serial five-language frozen holdout evaluation**

Use the Task 8 `frozen_run.py` command for each holdout language, changing only the dataset path, `--split holdout`, and context protocol. Keep provider `codex`, both models `gpt-5.6-sol`, concurrency `4`, resolver `v2`, and the same split manifest. Run Java, Python, TypeScript, Rust, and Go serially.

Expected: five complete or resumably complete artifacts, each mirrored to the external content-addressed archive. Do not retry completed decisions to improve a metric; resume only provider-abstained rows under exact provenance.

- [ ] **Step 4: Apply the final quality gate**

Run:

```bash
python3 eval/bench/report.py "$HOME/evergreen-private-labels/holdout-v2/out/"*.json \
  --require-language Java --require-language Python --require-language typescript \
  --require-language rust --require-language go \
  --coverage-threshold 0.99 --decision-threshold 0.99 \
  --precision-threshold 0.60 --recall-threshold 0.60 --f1-threshold 0.60 \
  --markdown eval/bench/results-current.md
```

Expected: every language reports provider completion `>= .99`, semantic decision coverage `>= .99`, precision `>= .60`, recall `>= .60`, F1 `>= .60`, and overall `Publication status: PASS`.

If this gate fails, do not publish the candidate as current and do not tune against holdout row reasoning. Restore the generated report from the external baseline copy by applying a new forward commit that keeps `0.4.0 baseline` wording, record the failed aggregate metrics without row-level inspection, and start a new split/adjudication cycle.

- [ ] **Step 5: Generate the public structured decision export through the auditability workstream**

Export only stable IDs/hashes and structured stage outcomes: snap verdict/category/proof, challenge cracks, prong role/verdict/proof, escalation presence, blindspot-present Boolean, synthesis verdict/category/proof, final status/semantic status/verdict/category. Exclude code, documentation, `why`, `claim`, `evidence`, and all free-form reasoning.

Run that workstream's verifier and confirm its recomputed report matches `results-current.md` exactly before linking it from documentation.

- [ ] **Step 6: Update current-result documentation only after PASS**

Replace baseline wording with the generated v2 current report. State the human-label protocol, project-group holdout, thresholds, unverified rate, exact commit/tree, resolver/context protocol, provider/CLI/models, and dataset/split hashes. Preserve the 0.4.0 baseline in a clearly historical subsection for comparison.

- [ ] **Step 7: Run final verification**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
bash tests/hooks.sh
bash tests/action.sh
python3 eval/bench/run_bench.py --selftest
python3 eval/bench/replay.py "$HOME/evergreen-private-labels/holdout-v2/out/"*.json \
  --resolver v2 --expect-stored
git diff --check
git status --short
```

Expected: all tests pass; replay reports zero differences; diff check is silent; only the generated report and intentional documentation/export files are modified.

- [ ] **Step 8: Commit the passing report without pushing**

```bash
git add eval/bench/results-current.md README.md eval/README.md eval/bench/README.md
git commit -m "eval: publish human-held-out resolver v2 benchmark"
```

Do not push until the user explicitly requests it.

---

## Final Done Criteria

- [ ] The public split manifest is ID-only, hash-bound, complete, and project-disjoint.
- [ ] Holdout labels remained unavailable until after candidate freeze.
- [ ] `resolve_v1` reproduces all 2,103 completed frozen decisions with zero differences and preserves the one Rust abstention.
- [ ] `resolve_v2` makes `unverified` first-class and rejects unsupported drift findings.
- [ ] `hardest-broken` is absent from v2 and replaced by `evidence-auditor`; v1 replay remains unchanged.
- [ ] Every landed v2 challenge forces synthesis.
- [ ] Java context is label-blind, deterministic, bounded to 65,536 bytes, exact-commit bound, and available for at least 99% of Java evaluation rows.
- [ ] Provider completion and semantic decision coverage are separate metrics; both must reach 99% per language.
- [ ] Precision, recall, and F1 each reach 0.60 per language on the untouched human holdout.
- [ ] No full paid run occurred before cheap gates and the bounded development canary passed.
- [ ] No CoDoc code/doc text or free-form reasoning appears in a public export.
- [ ] `SKILL.md`, `DIGEST.md`, and `AGENTS.md` state the same proven resolver-v2 policy.
- [ ] The old `cb24647` report remains honestly identified as the 0.4.0 baseline unless the v2 holdout report passes.
- [ ] The full test suite, hook integration, Action integration, benchmark self-test, replay parity, and `git diff --check` all pass.

## Executor Stop Conditions

- Stop if the human split has fewer than 20 positives or 20 negatives in any language.
- Stop if any project appears in both development and holdout.
- Stop if holdout labels are accessible before candidate freeze.
- Stop if `v1` replay differs from a stored frozen result.
- Stop if Java context availability is below 99% or requires label-aware source selection.
- Stop if any cheap test invokes a provider CLI.
- Stop if any development language misses one practical threshold; do not proceed to holdout.
- Stop if final holdout misses one threshold; do not promote or tune against holdout rows.
- Stop if repository, archive, Git identity, dataset, split, resolver, context protocol, model, provider, or concurrency provenance changes during a run.
