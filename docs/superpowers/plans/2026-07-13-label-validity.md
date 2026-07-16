# Label Validity Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a deterministic, standard-library human-label audit that measures the current benchmark's label error without allowing LLM votes or automation to masquerade as human truth.

**Architecture:** A pure core module loads and hashes frozen benchmark artifacts, selects the approved risk-stratified sample, and creates HMAC-blinded packets in an external work directory. A separate statistics module validates two independent human annotation files plus third-review work, calculates agreement and sampling-weighted error, applies the approved escalation gates, and can rescore a completely relabeled frozen transcript through an immutable overlay. The CLI orchestrates these pure functions but never invokes a model, writes a judgment, or publishes a human-validity claim on its own.

**Tech Stack:** Python 3.11 standard library (`argparse`, `dataclasses`, `hashlib`, `hmac`, `json`, `math`, `random`, `secrets`, `statistics`), JSON/JSONL, `unittest`.

## Global Constraints

- Run this workstream in an isolated worktree or branch; it owns only the new label-audit modules, label schema, focused tests, rubric, and label-audit documentation.
- The root integrator owns `AGENTS.md`, `eval/bench/artifact.py`, `eval/bench/report.py`, `eval/bench/results-current.md`, root `README.md`, and cross-workstream release documentation.
- Automation must never claim an annotator is human, infer a human label, fill a missing judgment, adjudicate semantic truth, or substitute LLM consensus for human judgment.
- The `sample` command is terminal at the automation boundary: it writes blinded packets, prints `HUMAN JUDGMENT REQUIRED`, and exits successfully without creating annotation or adjudication records.
- No label-audit command may invoke `claude`, `codex`, an API, a network client, or a provider-backed benchmark.
- Packets, coordinator mappings, split keys, annotations before publication, and third-party code/documentation remain outside the repository in an absolute external work directory with owner-only permissions.
- Public or checked-in manifests may contain identifiers, hashes, counts, sampling weights, and judgments only after the audit is complete; they must not contain source code, documentation text, free-form model reasoning, private HMAC keys, or annotator PII.
- Two blinded humans independently label every selected item. A third human independently labels every disagreement, every `insufficient-context` result, and a deterministic 10% sample of initial agreements.
- Packets conceal heuristic labels, `old`/`new` markers, LLM votes, Evergreen outcomes/reasoning, sample strata, and other annotators' work.
- Human verdicts are exactly `consistent`, `inconsistent`, or `insufficient-context`. An inconsistent judgment requires category, documentation claim, code evidence, and rationale.
- The initial retained-row sample is exactly all 130 published nominal positives, all 264 current nominal false positives, 25 deterministic true negatives per language, and the one Rust abstention: 520 unique rows.
- Add 20 discarded candidates per CoDoc language only when the exact historical source pool is available and hash-bound. Missing TypeScript, Rust, or Go pools remain `unverified`; regenerated lookalikes must never be presented as the historical pool.
- The sampled audit passes only with complete adjudication, no unresolved rows, overall Cohen's kappa at least `0.70` with bootstrap lower bound at least `0.60`, per-language kappa at least `0.60`, weighted label-error upper bound at most `5%` overall and `10%` per language, no census stratum above `5%` error, and no more than `5%` clearly usable discarded candidates excluded solely because truth opposed the heuristic.
- Any failed threshold produces `escalate`; missing historical selection evidence produces `unverified`; neither status may be rendered as a pass.
- A sampled pass may be called `human-audited` only. Only complete, independently double-labeled and adjudicated coverage of the recoverable 2,365-row source pool may be called `human-validated`.
- Detector development may consume only a repository-grouped development export. The locked holdout remains external and hidden until the candidate judge is frozen.
- Preserve the 0.4.0 benchmark artifacts and report byte-for-byte as a named historical baseline. Corrections are overlays bound by SHA-256, never in-place edits.
- Do not run another full paid benchmark in this plan. Frozen transcript rescoring and synthetic fixtures are sufficient.
- Use separately staged plain commits. Never combine `git add` and `git commit` in one shell call.

## Frozen 0.4.0 Inputs

The implementation tests use synthetic fixtures. Packet generation uses these immutable completed artifacts from commit `cb24647f7c62b9704d10c97e615005d924c005f2`:

| Language | Rows | Artifact filename | SHA-256 |
|---|---:|---|---|
| Java | 885 | `bench-cascade-java-trial-codex-gpt-5.6-sol.rows-885.a03f1b33c3f3e13ee60226298dd1dc83d611ba98ad625d86f22157544175a090.json` | `a03f1b33c3f3e13ee60226298dd1dc83d611ba98ad625d86f22157544175a090` |
| Python | 332 | `bench-codocbench-validated-trial-codex-gpt-5.6-sol.rows-332.b56b5541ad9e9ad31df3f756e6eb05509241b44651d159396ed0e93ffb566cc9.json` | `b56b5541ad9e9ad31df3f756e6eb05509241b44651d159396ed0e93ffb566cc9` |
| TypeScript | 284 | `bench-codocbench-ts-validated-trial-codex-gpt-5.6-sol.rows-284.16ea13944ce98c9fa62026328903e1babd82a92daf547bd0d45a79b662cd1c94.json` | `16ea13944ce98c9fa62026328903e1babd82a92daf547bd0d45a79b662cd1c94` |
| Rust | 304 | `bench-codocbench-rust-validated-trial-codex-gpt-5.6-sol.rows-304.88b499de0f0fcf037d54e00dc9c9d36183e6adb4c731f2441f300623a2929e0d.json` | `88b499de0f0fcf037d54e00dc9c9d36183e6adb4c731f2441f300623a2929e0d` |
| Go | 299 | `bench-codocbench-go-validated-trial-codex-gpt-5.6-sol.rows-299.b04a027dee961480a1fe3f8505b1b9bfb5d43ffd80b3e6b50861639246ed91ef.json` | `b04a027dee961480a1fe3f8505b1b9bfb5d43ffd80b3e6b50861639246ed91ef` |

The archive root is `~/evergreen-benchmark-archive/cb24647f7c62b9704d10c97e615005d924c005f2`. The tracked Python historical source pool is `eval/bench/out/codocbench-derived.jsonl`: 400 rows, 691,384 bytes, SHA-256 `6cbccfb5eb88f2a7e826e3e5f3595fb59274e04a2711c7c097d8faac4926fdae`. TypeScript, Rust, and Go historical derived pools are unavailable; their missing dropped-row counts are 76, 56, and 61 respectively.

---

### Task 1: Frozen input model and provenance validation

**Files:**
- Create: `eval/bench/label_audit_core.py`
- Create: `tests/test_label_audit.py`

**Interfaces:**
- Produces: `AuditItem`, `ArtifactInput`, `SourcePool`, `canonical_language(value: str) -> str`, `load_artifact(path: Path) -> ArtifactInput`, `load_source_pool(path: Path, language: str) -> SourcePool`, and `sha256_file(path: Path) -> str`.
- Consumes: the existing artifact document shape (`metadata`, `rows`, `schema_version`, `timing`) and row fields already validated by `eval.bench.artifact`.
- Invariant: an item key is the tuple `(canonical_language, id)`; duplicate keys, mixed-language artifacts, malformed results, symlinks, non-regular files, or filename/digest disagreement fail closed.

- [ ] **Step 1: Write failing input and provenance tests**

Add `LabelAuditInputTests` using `unittest`, `tempfile.TemporaryDirectory`, and synthetic artifact helpers. Include these exact cases:

```python
class LabelAuditInputTests(unittest.TestCase):
    def test_load_artifact_normalizes_language_and_binds_file_hash(self):
        path = self.write_artifact([
            row("a", "Python", "inconsistent", "complete", "inconsistent"),
            row("b", "Python", "consistent", "complete", "consistent"),
        ])
        loaded = core.load_artifact(path)
        self.assertEqual(loaded.language, "python")
        self.assertEqual(loaded.row_count, 2)
        self.assertEqual(loaded.sha256, hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(loaded.items[0].key, ("python", "a"))

    def test_load_artifact_rejects_duplicate_ids_and_mixed_languages(self):
        for rows, message in (
            ([row("a", "Python", "consistent"), row("a", "Python", "consistent")], "duplicate"),
            ([row("a", "Python", "consistent"), row("b", "Go", "consistent")], "one language"),
        ):
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                core.load_artifact(self.write_artifact(rows))

    def test_load_source_pool_marks_missing_source_identity_unverified(self):
        pool = self.write_jsonl([derived_row("a#0-old", "typescript", source=None)])
        loaded = core.load_source_pool(pool, "typescript")
        self.assertEqual(loaded.provenance_status, "unverified")
        self.assertEqual(loaded.rows[0]["source_status"], "missing")
```

The helper `row()` must always include `code`, `doc`, `func`, `label`, `category`, `language`, and a `got` object. Add rejection tests for absent `got`, an unknown label, an invalid final status/verdict combination, a symlink, and an empty artifact.

- [ ] **Step 2: Run the focused tests and confirm they fail**

Run:

```bash
python3 -m unittest tests.test_label_audit.LabelAuditInputTests -v
```

Expected: import failure for `eval.bench.label_audit_core`.

- [ ] **Step 3: Implement immutable input records and bounded readers**

Implement these public records and constants. Use `eval.bench.artifact.read_bytes` for bounded, symlink-safe reads rather than adding another filesystem reader.

```python
LANGUAGES = ("java", "python", "typescript", "rust", "go")
FINAL_STATUSES = {"complete", "abstain"}
FINAL_VERDICTS = {"consistent", "inconsistent"}
MAX_AUDIT_INPUT_BYTES = 64 * 1024 * 1024

@dataclass(frozen=True)
class AuditItem:
    id: str
    language: str
    code: str
    doc: str
    func: str
    label: str
    category: str | None
    final_status: str
    final_verdict: str | None
    artifact_sha256: str

    @property
    def key(self) -> tuple[str, str]:
        return self.language, self.id

@dataclass(frozen=True)
class ArtifactInput:
    path: Path
    sha256: str
    language: str
    row_count: int
    dataset_sha256: str
    items: tuple[AuditItem, ...]

@dataclass(frozen=True)
class SourcePool:
    path: Path
    sha256: str
    language: str
    row_count: int
    provenance_status: str
    rows: tuple[dict, ...]
```

`canonical_language` must case-fold and map `ts` to `typescript`; all other undeclared values raise `ValueError`. `load_artifact` must calculate the byte hash before parsing, validate `metadata.dataset.sha256`, derive legacy verdicts only through the same rules as `metrics.rows_from_transcript`, and never trust the filename as the digest. If a filename contains `.rows-N.<digest>.json`, require both `N` and `digest` to match the content.

- [ ] **Step 4: Run focused tests and the existing artifact tests**

Run:

```bash
python3 -m unittest tests.test_label_audit.LabelAuditInputTests tests.test_bench_artifact -v
```

Expected: all tests pass; no provider subprocess starts.

- [ ] **Step 5: Stage and commit Task 1**

```bash
git add eval/bench/label_audit_core.py tests/test_label_audit.py
```

```bash
git commit -m "feat(eval): bind label audits to frozen inputs"
```

### Task 2: Risk-stratified sampling and HMAC-blinded packets

**Files:**
- Modify: `eval/bench/label_audit_core.py`
- Modify: `tests/test_label_audit.py`

**Interfaces:**
- Consumes: `tuple[ArtifactInput, ...]`, optional `dict[str, SourcePool]`, a 32-byte private blind key, `audit_id`, `seed=20260713`, `tn_per_language=25`, and `discarded_per_language=20`.
- Produces: `SampleSelection`, `build_sample(...) -> SampleSelection`, `blind_id(...) -> str`, and `write_blinded_packets(...) -> PacketOutputs`.
- Packet files: `annotator-a.packet.json`, `annotator-b.packet.json`, and `adjudicator-source.packet.json` in an absolute external work directory. The third file is a private source packet used later to create the selected adjudication packet; it is not given to the adjudicator yet.
- Coordinator file: `coordinator.json`, mode `0600`, containing mappings, strata, source hashes, and inclusion probabilities. It remains outside Git.

- [ ] **Step 1: Write failing sampling-count and blinding tests**

Create a five-language synthetic matrix large enough to exercise all strata. Tests must assert census inclusion, a fixed-size TN sample, no duplicates, stable selection under input-order reversal, and private-field absence:

```python
def test_build_sample_censuses_risky_strata_and_samples_tn_per_language(self):
    artifacts = synthetic_five_language_inputs(tn_count=30)
    selection = core.build_sample(
        artifacts, source_pools={}, audit_id="audit-1", seed=20260713,
        tn_per_language=25, discarded_per_language=20,
    )
    self.assertEqual(selection.count("nominal_positive"), 5)
    self.assertEqual(selection.count("nominal_false_positive"), 5)
    self.assertEqual(selection.count("true_negative_sample"), 125)
    self.assertEqual(selection.count("abstention"), 1)
    self.assertEqual(selection.missing_discarded_languages,
                     ("go", "python", "rust", "typescript"))

def test_packets_hide_every_outcome_and_use_opaque_ids(self):
    outputs = core.write_blinded_packets(selection, work_dir, blind_key=b"k" * 32)
    packet = json.loads(outputs.annotator_a.read_text())
    serialized = json.dumps(packet)
    for forbidden in ("label", "final_verdict", "stratum", "old", "new", "votes"):
        self.assertNotIn(forbidden, serialized)
    self.assertRegex(packet["items"][0]["blind_id"], r"^item-[0-9a-f]{24}$")
    self.assertEqual(stat.S_IMODE(outputs.coordinator.stat().st_mode), 0o600)
```

Also test that output inside the repository is refused, a relative output path is refused, blind keys shorter than 32 bytes are refused, source pools with wrong language/hash are refused, and two annotator packets contain the same items in different deterministic orders.

- [ ] **Step 2: Run the sampling tests and confirm failure**

Run:

```bash
python3 -m unittest tests.test_label_audit.LabelAuditSamplingTests -v
```

Expected: failures naming undefined `build_sample` and `write_blinded_packets`.

- [ ] **Step 3: Implement deterministic strata and inclusion probabilities**

Add these records and signature:

```python
@dataclass(frozen=True)
class SelectedItem:
    item: AuditItem
    stratum: str
    inclusion_probability: float
    source_kind: str

@dataclass(frozen=True)
class SampleSelection:
    audit_id: str
    seed: int
    selected: tuple[SelectedItem, ...]
    missing_discarded_languages: tuple[str, ...]
    input_hashes: tuple[tuple[str, str], ...]

    def count(self, stratum: str) -> int:
        return sum(row.stratum == stratum for row in self.selected)

@dataclass(frozen=True)
class PacketOutputs:
    annotator_a: Path
    annotator_b: Path
    adjudicator_source: Path
    coordinator: Path
    blind_key: Path

def build_sample(
    artifacts: tuple[ArtifactInput, ...],
    source_pools: dict[str, SourcePool],
    *, audit_id: str, seed: int = 20260713,
    tn_per_language: int = 25,
    discarded_per_language: int = 20,
) -> SampleSelection:
    return select_approved_strata(
        validate_artifact_set(artifacts), source_pools,
        audit_id=audit_id, seed=seed,
        tn_per_language=tn_per_language,
        discarded_per_language=discarded_per_language,
    )
```

Keep `validate_artifact_set` and `select_approved_strata` private to this module. The former enforces one artifact for each declared language and globally unique item keys; the latter implements the five ordered rules immediately below and returns the immutable records above.

Use these exact stratum rules in precedence order:

1. `label == "inconsistent"` -> `nominal_positive`, probability `1.0`.
2. `label == "consistent" and final_status == "complete" and final_verdict == "inconsistent"` -> `nominal_false_positive`, probability `1.0`.
3. `final_status != "complete"` -> `abstention`, probability `1.0`.
4. Remaining consistent-complete rows -> stable-sort by item key, sample exactly 25 per language with `random.Random(f"{seed}:{language}")`, and store probability `25 / eligible_count`.
5. For each available CoDoc source pool, find rows absent from the retained artifact, split them by `-old` and `-new`, and select 10 from each with stable per-language/per-side seeds. If one side has fewer than 10, fail rather than silently change the approved design. Each discarded row's probability is `10 / side_count`.

Assert the real retained matrix counts `(130, 264, 125, 1)` when all five frozen artifacts are supplied. Do not hard-code those counts into generic fixture behavior.

- [ ] **Step 4: Implement operational blinding and safe external writes**

Create opaque IDs with `HMAC-SHA256(blind_key, audit_id + NUL + language + NUL + id)`, truncated to 24 hex characters and prefixed `item-`. Derive annotator ordering independently with `HMAC-SHA256(blind_key, b"annotator-a" + blind_id)` and `annotator-b`. Packets contain only:

```json
{
  "schema_version": 1,
  "audit_id": "evergreen-0.4.0-label-audit",
  "rubric_sha256": "<64 lowercase hex characters>",
  "items": [{
    "blind_id": "item-0123456789abcdef01234567",
    "language": "python",
    "code": "1 | def example():\n2 |     return 1",
    "documentation": "1 | Returns one."
  }]
}
```

Number code and documentation lines during packet creation. Do not include original IDs, function names outside the supplied code, labels, categories, votes, model results, rationales, or strata. Write through a temporary regular file followed by `os.replace`; set directory mode `0700` and coordinator/key-file mode `0600`.

- [ ] **Step 5: Run the sampling, input, and existing benchmark tests**

Run:

```bash
python3 -m unittest tests.test_label_audit.LabelAuditSamplingTests tests.test_label_audit.LabelAuditInputTests tests.test_bench -v
```

Expected: all tests pass with zero subprocess provider calls.

- [ ] **Step 6: Stage and commit Task 2**

```bash
git add eval/bench/label_audit_core.py tests/test_label_audit.py
```

```bash
git commit -m "feat(eval): generate blinded label audit packets"
```

### Task 3: Human annotation schema, validation, and third-review selection

**Files:**
- Create: `eval/bench/human-label.schema.json`
- Modify: `eval/bench/label_audit_core.py`
- Modify: `tests/test_label_audit.py`

**Interfaces:**
- Produces: `load_annotations(path: Path, packet: Path) -> AnnotationSet`, `select_third_review(first: AnnotationSet, second: AnnotationSet, *, rate: float, seed: int) -> tuple[str, ...]`, `write_third_packet(coordinator: Path, blind_ids: tuple[str, ...], destination: Path) -> Path`, and `combine_human_labels(selection: SampleSelection, first: AnnotationSet, second: AnnotationSet, third: AnnotationSet, third_ids: tuple[str, ...]) -> CombinedLabels`.
- The schema documents the interchange shape; Python validation is authoritative because the project has no JSON Schema runtime dependency.
- Human identity is represented only by a pseudonymous `annotator_id` plus explicit self-attestations. Validation proves shape and declared independence, not biological authorship.

- [ ] **Step 1: Write failing schema and boundary tests**

Add tests for one valid annotation set and each invalid condition:

```python
def valid_judgment(blind_id="item-a", verdict="consistent"):
    return {
        "blind_id": blind_id,
        "verdict": verdict,
        "category": None,
        "documentation_claim": "The documentation promises one.",
        "code_evidence": "Line 2 returns one.",
        "rationale": "The observed return matches the claim.",
        "missing_context": None,
    }

def test_inconsistent_requires_allowed_category_and_all_evidence(self):
    judgment = valid_judgment(verdict="inconsistent")
    for field, value, message in (
        ("category", None, "category"),
        ("documentation_claim", "", "documentation_claim"),
        ("code_evidence", "", "code_evidence"),
        ("rationale", "", "rationale"),
    ):
        broken = {**judgment, field: value}
        with self.subTest(field=field), self.assertRaisesRegex(ValueError, message):
            core.validate_judgment(broken)

def test_loader_cannot_establish_that_an_attestation_is_true(self):
    loaded = core.load_annotations(valid_path, packet_path)
    self.assertEqual(loaded.trust_status, "self-attested-human")
    self.assertFalse(loaded.humanity_verified)
```

Test exact packet coverage, duplicate/unknown blind IDs, rubric/audit mismatch, same annotator ID across initial sets, missing independence/model-free attestations, `insufficient-context` without `missing_context`, and any unexpected key. Test that no validator imports or invokes provider code.

- [ ] **Step 2: Run annotation tests and confirm failure**

Run:

```bash
python3 -m unittest tests.test_label_audit.LabelAuditAnnotationTests -v
```

Expected: schema file absent and annotation interfaces undefined.

- [ ] **Step 3: Add the closed annotation schema**

Create a Draft 2020-12 JSON Schema with `additionalProperties: false` at every object level. Require:

```json
{
  "schema_version": 1,
  "audit_id": "evergreen-0.4.0-label-audit",
  "rubric_sha256": "^[0-9a-f]{64}$",
  "annotator": {
    "annotator_id": "H01",
    "human_judgment": true,
    "worked_independently": true,
    "used_model_assistance": false
  },
  "judgments": []
}
```

Judgment verdict enum: `consistent`, `inconsistent`, `insufficient-context`. Category enum: `direct-mismatch`, `over-promise`, or null. Enforce cross-field rules in Python: inconsistent requires a non-null category; consistent requires null category; insufficient-context requires null category and a non-empty `missing_context`; every judgment requires non-empty claim, evidence, and rationale so agreement remains auditable.

- [ ] **Step 4: Implement third-review selection without semantic automation**

`select_third_review` returns the union of:

- every blind ID where verdicts differ;
- every blind ID where either verdict is `insufficient-context`; and
- exactly `ceil(0.10 * agreement_count)` agreements selected by stable seeded sampling.

The third packet contains the original blinded code/documentation but not the first two labels, rationales, or selection reason. `combine_human_labels` uses majority among decisive independent labels. A row with no two matching decisive labels is `unresolved`; automation must not break the tie. The returned record must preserve all three submitted judgments and identify whether review was triggered by disagreement, uncertainty, or agreement QA.

Use these immutable records so Task 4 receives a stable interface:

```python
@dataclass(frozen=True)
class AnnotationSet:
    audit_id: str
    rubric_sha256: str
    annotator_id: str
    trust_status: str
    humanity_verified: bool
    judgments: tuple[dict, ...]

@dataclass(frozen=True)
class CombinedLabel:
    blind_id: str
    final_verdict: str | None
    final_category: str | None
    unresolved: bool
    review_reason: str | None
    submitted_judgments: tuple[dict, ...]

@dataclass(frozen=True)
class CombinedLabels:
    audit_id: str
    rubric_sha256: str
    labels: tuple[CombinedLabel, ...]
```

- [ ] **Step 5: Run focused tests**

Run:

```bash
python3 -m unittest tests.test_label_audit.LabelAuditAnnotationTests -v
```

Expected: all annotation, blinding, exact-coverage, and unresolved-row tests pass.

- [ ] **Step 6: Stage and commit Task 3**

```bash
git add eval/bench/human-label.schema.json eval/bench/label_audit_core.py tests/test_label_audit.py
```

```bash
git commit -m "feat(eval): validate independent human judgments"
```

### Task 4: Agreement, weighted label error, and fail-closed gates

**Files:**
- Create: `eval/bench/label_audit_stats.py`
- Modify: `tests/test_label_audit.py`

**Interfaces:**
- Consumes: `SampleSelection`, two `AnnotationSet` values, and `CombinedLabels`.
- Produces: `cohen_kappa(a: Sequence[str], b: Sequence[str]) -> float`, `bootstrap_kappa_ci(a: Sequence[str], b: Sequence[str], strata: Sequence[str], *, replicates: int = 10000, seed: int = 20260713) -> tuple[float, float]`, `wilson_interval(errors: int, n: int) -> tuple[float, float]`, `weighted_error(rows: Sequence[AuditResult]) -> Estimate`, `evaluate_gate(inputs: GateInputs) -> GateResult`, and `render_audit_report(inputs: GateInputs, result: GateResult) -> str`.
- Gate statuses are exactly `pass`, `escalate`, and `unverified`; incomplete annotation input raises before gate evaluation.

- [ ] **Step 1: Write failing statistic and threshold tests**

Use hand-calculated fixtures rather than comparing a function to itself:

```python
class LabelAuditStatisticsTests(unittest.TestCase):
    def test_cohen_kappa_known_matrix(self):
        a = ["consistent", "consistent", "inconsistent", "inconsistent"]
        b = ["consistent", "inconsistent", "inconsistent", "inconsistent"]
        self.assertAlmostEqual(stats.cohen_kappa(a, b), 0.5)

    def test_weighted_error_uses_inverse_inclusion_probability(self):
        rows = [
            audit_result(error=True, inclusion_probability=1.0),
            audit_result(error=False, inclusion_probability=0.25),
        ]
        self.assertAlmostEqual(stats.weighted_error(rows).point, 0.2)

    def test_gate_escalates_on_each_threshold_independently(self):
        passing = passing_gate_inputs()
        mutations = {
            "overall kappa": {"overall_kappa": 0.69},
            "kappa lower": {"overall_kappa_lower": 0.59},
            "language kappa": {"language_kappa": {**passing.language_kappa, "java": 0.59}},
            "overall error": {"overall_error_upper": 0.051},
            "language error": {"language_error_upper": {**passing.language_error_upper, "go": 0.101}},
            "census error": {"max_census_error": 0.051},
            "discard selection": {"discarded_usable_rate": 0.051},
        }
        for reason, changes in mutations.items():
            with self.subTest(reason=reason):
                result = stats.evaluate_gate(dataclasses.replace(passing, **changes))
                self.assertEqual(result.status, "escalate")
                self.assertIn(reason, result.reasons)
```

Also test undefined κ, unresolved rows, missing language strata, incomplete third review, missing discarded pools, exactly-on-boundary passes, deterministic bootstrap output, zero-error Wilson upper bounds, and prevalence-sensitive positive/negative agreement.

- [ ] **Step 2: Run statistic tests and confirm failure**

Run:

```bash
python3 -m unittest tests.test_label_audit.LabelAuditStatisticsTests -v
```

Expected: import failure for `eval.bench.label_audit_stats`.

- [ ] **Step 3: Implement deterministic agreement and uncertainty calculations**

Calculate initial-human agreement before third review. Exclude `insufficient-context` from binary κ but report its count and rate; any unresolved final row prevents a pass. Use 10,000 stratified bootstrap replicates with seed `20260713`; resample within language and sampling stratum so the interval respects the design. If either annotator has only one binary class in a requested slice, return `None` and make the gate escalate rather than substituting perfect agreement.

Report overall and per-language:

- raw agreement;
- Cohen's κ and 95% bootstrap interval;
- positive agreement `2a / (2a + b + c)`;
- negative agreement `2d / (2d + b + c)`;
- uncertainty and third-review rates.

- [ ] **Step 4: Implement sampling-weighted error and exact gate precedence**

Estimate error with inverse-probability weights:

```python
point = sum((1.0 if row.label_error else 0.0) / row.inclusion_probability for row in rows) \
        / sum(1.0 / row.inclusion_probability for row in rows)
```

Use stratified bootstrap for the aggregate interval and Wilson intervals for unweighted per-stratum diagnostics. Census strata (`nominal_positive`, `nominal_false_positive`, `abstention`) use exact point rates. Evaluate in this order:

1. Missing/incomplete annotations or unresolved final labels -> raise `ValueError`; do not issue a gate result.
2. Any missing historical discarded pool -> `unverified`, even if numerical thresholds pass.
3. Any numerical threshold failure -> `escalate`.
4. All evidence and thresholds satisfied -> `pass`, qualification `human-audited`.
5. Qualification `human-validated` is available only when coordinator evidence proves every recoverable source-pool key has a final human label.

The Markdown report must label LLM comparisons `LLM-majority versus adjudicated human labels`, never `inter-annotator human agreement`. It must include input hashes, sample counts/probabilities, missing source pools, all thresholds, every failure reason, and the statement `Human status is self-attested and not machine-verifiable.`

Define the result boundary explicitly:

```python
@dataclass(frozen=True)
class Estimate:
    point: float
    lower: float
    upper: float

@dataclass(frozen=True)
class GateResult:
    status: str
    qualification: str | None
    reasons: tuple[str, ...]
```

`AuditResult` carries language, stratum, existing label, final human verdict/category, label-error boolean, and inclusion probability. `GateInputs` carries all calculated overall/per-language agreement and error values, census rates, discarded-usable rate, unresolved count, source-pool completeness, and census-coverage flag; make every field required so a caller cannot omit a threshold silently.

- [ ] **Step 5: Run statistics and complete focused suite**

Run:

```bash
python3 -m unittest tests.test_label_audit -v
```

Expected: all label-audit tests pass deterministically on repeated runs.

- [ ] **Step 6: Stage and commit Task 4**

```bash
git add eval/bench/label_audit_stats.py tests/test_label_audit.py
```

```bash
git commit -m "feat(eval): gate benchmark labels on human evidence"
```

### Task 5: Immutable relabel overlays, exact rescoring, and repository holdout splits

**Files:**
- Modify: `eval/bench/label_audit_core.py`
- Modify: `eval/bench/label_audit_stats.py`
- Modify: `tests/test_label_audit.py`

**Interfaces:**
- Produces: `build_overlay(artifact: ArtifactInput, labels: CombinedLabels) -> dict`, `rescore_overlay(artifact: ArtifactInput, overlay: dict) -> dict`, `repository_key(item: AuditItem) -> str`, and `split_by_repository(labels: CombinedLabels, coordinator: Path, *, split_key: bytes, development_fraction: float = 0.60) -> SplitResult`.
- Overlay metadata binds source artifact SHA-256, human-label package SHA-256, rubric SHA-256, audit ID, coverage, and generation mode (`sample` or `census`).
- Exact rescoring is legal only at 100% artifact-row coverage. Sample overlays return weighted audit estimates through Task 4 and are refused by `rescore_overlay`.

- [ ] **Step 1: Write failing overlay and leakage tests**

```python
def test_exact_rescore_requires_full_label_coverage_and_preserves_source(self):
    original = artifact_path.read_bytes()
    with self.assertRaisesRegex(ValueError, "100% human-label coverage"):
        core.rescore_overlay(loaded_artifact, partial_overlay)
    result = core.rescore_overlay(loaded_artifact, full_overlay)
    self.assertEqual(result["matrix"], {"tp": 1, "fp": 0, "fn": 0, "tn": 1})
    self.assertEqual(artifact_path.read_bytes(), original)

def test_repository_split_never_places_one_repository_in_both_sets(self):
    split = core.split_by_repository(labels, split_key=b"s" * 32, development_fraction=0.60)
    self.assertFalse(set(split.development_repositories) & set(split.holdout_repositories))
    self.assertEqual(set(split.development_ids) | set(split.holdout_ids), set(labels.ids))
```

Test artifact-hash mismatch, overlay-label-package mismatch, duplicate overlay keys, changed code/doc item hash, sampled exact-rescore refusal, stable split under row-order reversal, and minimum per-language/class coverage. A split that cannot place at least one positive and one negative from each language in holdout must fail with an explicit sparse-data error.

- [ ] **Step 2: Run overlay tests and confirm failure**

Run:

```bash
python3 -m unittest tests.test_label_audit.LabelAuditOverlayTests -v
```

Expected: undefined overlay and split interfaces.

- [ ] **Step 3: Implement content-bound overlays and offline scoring**

Each overlay row contains only:

```json
{
  "id": "owner/project/function#0-old",
  "language": "python",
  "item_sha256": "<hash of canonical language, id, code, and doc>",
  "human_verdict": "inconsistent",
  "human_category": "direct-mismatch"
}
```

Before scoring, recompute every item hash, require exact artifact coverage, convert human labels into copies of `metrics.rows_from_transcript(...)`, and call existing `eval.bench.metrics.score`. Never rewrite the original artifact or its metadata. The result records both original and corrected matrices, source artifact hash, overlay hash, and rubric hash.

- [ ] **Step 4: Implement repository-grouped 60/40 development and holdout export**

Derive the repository from the first two slash-separated ID components (`owner/project` for both CASCADE and CoDoc). Assign complete repository groups by sorting `HMAC-SHA256(split_key, canonical_language + NUL + repository)` and greedily choosing the placement that minimizes absolute deviation from 60% development rows across `(language, human_verdict, human_category)` cells. Tie-break toward holdout. Validate no repository crosses sets and each language retains both binary classes in holdout.

Write both exports outside the repository with `0600` mode. Development output may be handed to detector-quality work. Holdout output and split key remain inaccessible to detector development until the root integrator records the frozen judge hash.

`SplitResult` contains development and holdout IDs, repository sets, per-language/class/category counts, source label-package SHA-256, and the two external output paths. It must not contain the private split key.

- [ ] **Step 5: Run overlay, statistics, and existing metrics tests**

Run:

```bash
python3 -m unittest tests.test_label_audit.LabelAuditOverlayTests tests.test_label_audit.LabelAuditStatisticsTests tests.test_bench -v
```

Expected: all tests pass and the source artifact hash remains unchanged.

- [ ] **Step 6: Stage and commit Task 5**

```bash
git add eval/bench/label_audit_core.py eval/bench/label_audit_stats.py tests/test_label_audit.py
```

```bash
git commit -m "feat(eval): rescore immutable human label overlays"
```

### Task 6: CLI orchestration and the hard human-judgment stop

**Files:**
- Create: `eval/bench/label_audit.py`
- Modify: `tests/test_label_audit.py`

**Interfaces:**
- CLI subcommands: `sample`, `check-labels`, `make-third-review`, `report`, `rescore`, and `split`.
- `sample` accepts repeated `--artifact`, optional repeated `--source-pool LANGUAGE=PATH`, `--work-dir`, `--audit-id`, `--seed`, and `--rubric`.
- The CLI has no provider/model option and imports no provider runner.

- [ ] **Step 1: Write failing CLI boundary tests**

Patch core functions rather than invoking real artifacts. Test:

```python
def test_sample_stops_at_human_boundary_without_annotation_files(self):
    completed = subprocess.run(
        [sys.executable, "eval/bench/label_audit.py", "sample",
         "--artifact", str(artifact), "--work-dir", str(external_dir),
         "--rubric", "eval/bench/human-audit/rubric-v1.md"],
        cwd=repo, capture_output=True, text=True,
    )
    self.assertEqual(completed.returncode, 0, completed.stderr)
    self.assertIn("HUMAN JUDGMENT REQUIRED", completed.stdout)
    self.assertFalse(any(external_dir.glob("*annotation*")))
    self.assertFalse(any(external_dir.glob("*adjudication*")))
```

Add `python -m ast`-style tests that inspect imports/calls and reject `subprocess`, `urllib`, `http`, `claude`, `codex`, and the benchmark runner from `label_audit.py`, `label_audit_core.py`, and `label_audit_stats.py`. Test every subcommand's required inputs, external-workdir refusal, output non-overwrite by default, and `--help` on Python 3.11.

- [ ] **Step 2: Run CLI tests and confirm failure**

Run:

```bash
python3 -m unittest tests.test_label_audit.LabelAuditCliTests -v
```

Expected: CLI file absent.

- [ ] **Step 3: Implement a thin CLI over the tested pure interfaces**

Map subcommands exactly:

- `sample`: load inputs, create a new 32-byte `blind.key` with `secrets.token_bytes(32)` and mode `0600`, write packets/coordinator, print counts and `HUMAN JUDGMENT REQUIRED`, then return `0`.
- `check-labels`: validate one annotation file against one packet; print self-attested trust status and return nonzero on schema/coverage error.
- `make-third-review`: validate two distinct initial annotation sets, select the third-review IDs, and write a blind third packet without prior judgments.
- `report`: require both initial sets and the complete third set, combine labels, evaluate statistics/gates, and atomically write JSON plus Markdown reports.
- `rescore`: require census overlay coverage and write an offline corrected matrix.
- `split`: require complete final human labels, an external output directory, and a private 32-byte split key.

No subcommand accepts free-form shell commands. Resolve all paths, reject symlinks and in-repository work outputs, and refuse overwrite unless an exact byte-identical output already exists.

- [ ] **Step 4: Run CLI and complete focused tests**

Run:

```bash
python3 -m unittest tests.test_label_audit -v
python3 eval/bench/label_audit.py --help
```

Expected: tests pass; help lists exactly the six subcommands and no provider/model arguments.

- [ ] **Step 5: Stage and commit Task 6**

```bash
git add eval/bench/label_audit.py tests/test_label_audit.py
```

```bash
git commit -m "feat(eval): add human label audit workflow"
```

### Task 7: Source provenance and human rubric

**Files:**
- Modify: `eval/bench/codocbench_to_jsonl.py:35-40`
- Create: `eval/bench/human-audit/source-pools.json`
- Create: `eval/bench/human-audit/rubric-v1.md`
- Create: `eval/bench/human-audit/README.md`
- Modify: `tests/test_label_audit.py`

**Interfaces:**
- Future derived rows add `source: {owner, project, file, commit, doc_version}` and `source_status: complete | incomplete` without changing historical files.
- `source-pools.json` records historical availability and hashes; a `missing` entry is evidence, not a recoverable path.
- Rubric SHA-256 is bound into every packet and annotation file.

- [ ] **Step 1: Write failing source-provenance tests**

Import `pair` from `codocbench_to_jsonl` and assert:

```python
def test_future_derived_rows_preserve_mining_provenance(self):
    source = mined_row(file="src/lib.rs", commit="0123456789ab")
    result = codocbench_to_jsonl.pair(source, 3, "old", "inconsistent")
    self.assertEqual(result["source"], {
        "owner": source["owner"],
        "project": source["project"],
        "file": "src/lib.rs",
        "commit": "0123456789ab",
        "doc_version": "old",
    })
    self.assertEqual(result["source_status"], "complete")
```

Test upstream rows without file/commit produce `source_status: incomplete` and explicit null fields rather than invented provenance. Test the source-pool registry's exact Python and CASCADE hashes plus missing counts for TypeScript/Rust/Go.

- [ ] **Step 2: Run source tests and confirm failure**

Run:

```bash
python3 -m unittest tests.test_label_audit.LabelAuditSourceTests -v
```

Expected: derived rows lack `source` and the source-pool registry is absent.

- [ ] **Step 3: Preserve provenance on future conversion**

Extend only the dictionary returned by `pair`; do not regenerate or rename historical datasets. Normalize commit to the supplied string without resolving network state. `source_status` is complete only when owner, project, file, and commit are non-empty strings.

- [ ] **Step 4: Add the exact historical source registry**

Create `source-pools.json` with these material facts:

```json
{
  "schema_version": 1,
  "pools": [
    {"language": "java", "status": "available", "row_count": 885, "sha256": "1c322acf6bc02ae304c062f0d53306e6e9ebb0334bd133afd57940922892ae0b", "path": "eval/bench/cascade-java.jsonl"},
    {"language": "python", "status": "available", "row_count": 400, "sha256": "6cbccfb5eb88f2a7e826e3e5f3595fb59274e04a2711c7c097d8faac4926fdae", "path": "eval/bench/out/codocbench-derived.jsonl"},
    {"language": "typescript", "status": "missing", "expected_row_count": 360, "retained_row_count": 284, "missing_discarded_count": 76},
    {"language": "rust", "status": "missing", "expected_row_count": 360, "retained_row_count": 304, "missing_discarded_count": 56},
    {"language": "go", "status": "missing", "expected_row_count": 360, "retained_row_count": 299, "missing_discarded_count": 61}
  ]
}
```

Do not add guessed paths or hashes for missing pools.

- [ ] **Step 5: Write the decision rubric and operator boundary**

The rubric must define:

- documentation is the claim and shown code is the evidence;
- direct contradiction and non-delivery are inconsistent;
- code doing more than documented is consistent/informational;
- unseen delegated behavior or missing surrounding context is `insufficient-context`, never guessed;
- categories `direct-mismatch` and `over-promise` with positive/negative examples drawn from `eval/bench/dataset.jsonl`, not from the audit sample;
- exact claim/evidence/rationale requirements;
- independent, model-free annotation and no discussion before submission;
- pseudonymous IDs and no annotator PII;
- the third-review procedure and majority/unresolved rules.

The operator README must state that tooling cannot verify humanity, annotation is external work, packets must not be committed, missing historical pools are unverified, a sample is not a census, and only the root integrator may update publication claims.

- [ ] **Step 6: Run source tests and verify documentation paths**

Run:

```bash
python3 -m unittest tests.test_label_audit.LabelAuditSourceTests -v
python3 -m json.tool eval/bench/human-audit/source-pools.json >/dev/null
git diff --check
```

Expected: all source tests pass, JSON parses, and no whitespace errors appear.

- [ ] **Step 7: Stage and commit Task 7**

```bash
git add eval/bench/codocbench_to_jsonl.py eval/bench/human-audit/source-pools.json eval/bench/human-audit/rubric-v1.md eval/bench/human-audit/README.md tests/test_label_audit.py
```

```bash
git commit -m "docs(eval): define human label evidence protocol"
```

### Task 8: Generate the real blinded packet set and stop for humans

**Files:**
- External only: `~/evergreen-human-audit/0.4.0/`
- Do not modify tracked files in this task.

**Interfaces:**
- Consumes: the five frozen artifact files, Python source pool, and rubric from Tasks 1-7.
- Produces externally: blind key, coordinator mapping, two initial packets, and the private adjudicator source packet.
- Terminal result: retained sample count 520, Python discarded sample count 20, total packet count 540, and missing discarded languages `go,rust,typescript`.

- [ ] **Step 1: Confirm the repository and artifact identities before generation**

Run:

```bash
git status --short
git rev-parse HEAD
shasum -a 256 ~/evergreen-benchmark-archive/cb24647f7c62b9704d10c97e615005d924c005f2/*.rows-{885,332,284,304,299}.*.json
shasum -a 256 eval/bench/out/codocbench-derived.jsonl eval/bench/human-audit/rubric-v1.md
```

Expected: worktree clean; the five artifact hashes and Python pool hash match the Frozen Inputs section. Record the rubric hash printed by the command; do not hand-copy a guessed value.

- [ ] **Step 2: Generate packets in the external work directory**

Run:

```bash
python3 eval/bench/label_audit.py sample \
  --audit-id evergreen-0.4.0-label-audit \
  --seed 20260713 \
  --artifact ~/evergreen-benchmark-archive/cb24647f7c62b9704d10c97e615005d924c005f2/bench-cascade-java-trial-codex-gpt-5.6-sol.rows-885.a03f1b33c3f3e13ee60226298dd1dc83d611ba98ad625d86f22157544175a090.json \
  --artifact ~/evergreen-benchmark-archive/cb24647f7c62b9704d10c97e615005d924c005f2/bench-codocbench-validated-trial-codex-gpt-5.6-sol.rows-332.b56b5541ad9e9ad31df3f756e6eb05509241b44651d159396ed0e93ffb566cc9.json \
  --artifact ~/evergreen-benchmark-archive/cb24647f7c62b9704d10c97e615005d924c005f2/bench-codocbench-ts-validated-trial-codex-gpt-5.6-sol.rows-284.16ea13944ce98c9fa62026328903e1babd82a92daf547bd0d45a79b662cd1c94.json \
  --artifact ~/evergreen-benchmark-archive/cb24647f7c62b9704d10c97e615005d924c005f2/bench-codocbench-rust-validated-trial-codex-gpt-5.6-sol.rows-304.88b499de0f0fcf037d54e00dc9c9d36183e6adb4c731f2441f300623a2929e0d.json \
  --artifact ~/evergreen-benchmark-archive/cb24647f7c62b9704d10c97e615005d924c005f2/bench-codocbench-go-validated-trial-codex-gpt-5.6-sol.rows-299.b04a027dee961480a1fe3f8505b1b9bfb5d43ffd80b3e6b50861639246ed91ef.json \
  --source-pool python=eval/bench/out/codocbench-derived.jsonl \
  --rubric eval/bench/human-audit/rubric-v1.md \
  --work-dir ~/evergreen-human-audit/0.4.0
```

Expected output includes:

```text
retained selected: 520
discarded selected: 20
selection evidence unverified: go,rust,typescript
HUMAN JUDGMENT REQUIRED
```

The command exits without annotation or adjudication files.

- [ ] **Step 3: Verify packet safety and stop automation**

Run:

```bash
find ~/evergreen-human-audit/0.4.0 -maxdepth 1 -type f -exec stat -f '%Lp %N' {} \;
git status --short
```

Expected: private key/coordinator files mode `600`, work directory mode `700`, no tracked worktree changes, and no files named as annotations or adjudications.

- [ ] **Step 4: Hand packets to two independent humans**

This step is intentionally non-automatable. Provide `annotator-a.packet.json` and `annotator-b.packet.json` separately with the exact rubric. Do not send the coordinator mapping, blind key, existing labels, model votes/results, or the other annotator's work. No agent, LLM, script, or default value may execute this checkbox on a human's behalf.

- [ ] **Step 5: Validate human submissions and prepare third review**

After two humans return complete files, run:

```bash
python3 eval/bench/label_audit.py check-labels \
  --packet ~/evergreen-human-audit/0.4.0/annotator-a.packet.json \
  --labels ~/evergreen-human-audit/0.4.0/annotator-a.annotations.json
python3 eval/bench/label_audit.py check-labels \
  --packet ~/evergreen-human-audit/0.4.0/annotator-b.packet.json \
  --labels ~/evergreen-human-audit/0.4.0/annotator-b.annotations.json
python3 eval/bench/label_audit.py make-third-review \
  --coordinator ~/evergreen-human-audit/0.4.0/coordinator.json \
  --first ~/evergreen-human-audit/0.4.0/annotator-a.annotations.json \
  --second ~/evergreen-human-audit/0.4.0/annotator-b.annotations.json \
  --out ~/evergreen-human-audit/0.4.0/annotator-c.packet.json
```

Expected: both validation commands report `self-attested-human`, exact packet coverage, different annotator IDs, and no schema errors. The third packet contains only selected blind items and no earlier judgments. Give it to a third independent human; automation stops again.

### Task 9: Human-return gate, development split, and root integration

**Files:**
- External inputs/outputs: `~/evergreen-human-audit/0.4.0/`
- Root-owned integration after human completion: `eval/bench/README.md`, `eval/bench/results-current.md`, `eval/README.md`, `README.md`
- Root-owned integration tests if shared behavior changes: `tests/test_bench_artifact.py`, `tests/test_bench.py`

**Interfaces:**
- Consumes only actual completed human annotation files and third-review file.
- Produces external JSON/Markdown audit report, overlay, and repository-grouped development/holdout exports.
- Root integration consumes hashes and conclusions, not private packet contents.

- [ ] **Step 1: Produce the audit report only after third-human completion**

Run:

```bash
python3 eval/bench/label_audit.py report \
  --coordinator ~/evergreen-human-audit/0.4.0/coordinator.json \
  --first ~/evergreen-human-audit/0.4.0/annotator-a.annotations.json \
  --second ~/evergreen-human-audit/0.4.0/annotator-b.annotations.json \
  --third ~/evergreen-human-audit/0.4.0/annotator-c.annotations.json \
  --json-out ~/evergreen-human-audit/0.4.0/audit-report.json \
  --markdown-out ~/evergreen-human-audit/0.4.0/audit-report.md
```

Expected: one of `pass`, `escalate`, or `unverified`; the current missing historical pools force `unverified` unless exact hash-bound pools were recovered before sampling.

- [ ] **Step 2: Obey the result without reinterpretation**

- `escalate`: generate census packets for every recoverable source-pool row and repeat independent double annotation plus third review. Do not tune or publish corrected metrics from a failed sample as if it passed.
- `unverified`: report the retained-set human estimates and the exact missing selection evidence; do not use `human-validated` or claim the source selection is reproducible.
- `pass`: call the sampled evidence `human-audited`, never `human-validated`.

- [ ] **Step 3: Create development and locked holdout exports**

After final human labels exist, create a private key and run:

```bash
python3 -c 'import os,secrets; p="~/evergreen-human-audit/0.4.0/split.key"; fd=os.open(p, os.O_WRONLY|os.O_CREAT|os.O_EXCL, 0o600); os.write(fd, secrets.token_bytes(32)); os.close(fd)'
python3 eval/bench/label_audit.py split \
  --coordinator ~/evergreen-human-audit/0.4.0/coordinator.json \
  --labels ~/evergreen-human-audit/0.4.0/audit-report.json \
  --split-key ~/evergreen-human-audit/0.4.0/split.key \
  --development-fraction 0.60 \
  --out-dir ~/evergreen-human-audit/0.4.0/split
```

Expected: no repository overlap and both classes in every language's holdout. Give only `development.json` to `/root/detector_quality`; retain `holdout.json` externally until the root integrator records the frozen resolver/judge hashes.

- [ ] **Step 4: Root integrator reconciles publication language**

The root integrator updates benchmark docs after reviewing the generated audit report. Preserve `eval/bench/results-current.md` as the 0.4.0 label-provisional baseline or archive it under an explicit historical name before replacing any current-report pointer. Documentation must distinguish `heuristic`, `LLM-screened`, `human-audited`, and `human-validated` exactly and state missing source evidence.

- [ ] **Step 5: Run the complete repository verification matrix**

Run:

```bash
python3 -m unittest discover -s tests -p 'test_*.py'
bash tests/hooks.sh
bash tests/action.sh
python3 eval/bench/run_bench.py --selftest
python3 -m json.tool eval/bench/human-label.schema.json >/dev/null
python3 -m json.tool eval/bench/human-audit/source-pools.json >/dev/null
git diff --check
```

Expected: every command exits `0`; no provider-backed benchmark runs; no external work packets are staged.

- [ ] **Step 6: Inspect the exact integration diff and Evergreen verdict**

Run:

```bash
git status --short
git diff --check
git diff -- eval/bench/README.md eval/bench/results-current.md eval/README.md README.md
```

Expected: only intended tooling, tests, rubric, source registry, and evidence-backed documentation changes. Verify every benchmark claim against the generated report and source hashes. Required final verdict: `evergreen: benchmark documentation matches the human-audit evidence and names every unverified boundary.`

- [ ] **Step 7: Stage and commit only root-reviewed integration files**

```bash
git add eval/bench/README.md eval/bench/results-current.md eval/README.md README.md
```

```bash
git commit -m "docs(eval): publish human label audit status"
```

Do not create this commit before actual humans complete the required judgments and the root integrator approves the wording.
