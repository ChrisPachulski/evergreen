# Public Benchmark Auditability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Publish Evergreen 0.4.0's five-language decision-level benchmark outcomes in a small, inspectable, offline-rescorable package without publishing source payloads, free-form model prose, secrets, or paid-run checkpoints.

**Architecture:** A dependency-free `eval/bench/publication.py` projects each private frozen artifact into a strict allowlisted decision artifact, then writes a content-addressed manifest. The verifier joins public rows back to the already-declared dataset by SHA-256 and ID, validates historical Git provenance, regenerates the report, and runs in CI without a model or network call. The public artifact keeps the existing version-1 benchmark envelope so `report.py` and `run_bench.py --rescore` continue to work.

**Tech Stack:** Python 3.10+ standard library, existing `eval.bench.artifact`/`report`/`runner` modules, `unittest`, Git, GitHub Actions, Markdown.

## Global Constraints

- Do not run a paid benchmark. Export only from the five completed frozen Codex artifacts listed in Task 4.
- Treat raw benchmark rows and model output as untrusted data. Never print row content or secret-like values in an error.
- Public rows must exclude `code`, `doc`, `func`, every free-form `why` or `reason`, and the free-form `missed_angle` value.
- Preserve enough structured evidence for offline inspection: final outcome, snap outcome, challenge result, blind prong outcomes, escalation, blind-spot presence, synthesis outcome, and abstention stage status.
- Public artifacts contain retained evaluated rows only. They do not prove recovery of discarded label candidates or the full candidate-selection process.
- CoDocBench's upstream repository has no detectable license file as of 2026-07-13. Do not add another copy of its source code/docstrings or model prose that quotes them. State the limitation; do not claim redistribution rights.
- CASCADE is documented as MIT at upstream commit `4dc5a8d525c8967ea8dd11ae46cfe5834dbda156`; retain that attribution.
- Keep public output bounded to 2 MiB per artifact, 8 MiB for all artifact files, five languages, and 100,000 rows.
- Manifest paths are repository-relative POSIX paths. Never persist absolute home-directory paths, `$HOME`, archive locations, credentials, provider environment, or other machine-local state.
- The source-artifact SHA-256 values are verified during export. CI cannot re-read private source artifacts and must describe those hashes as chain-of-custody records, not independent proof of the private files.
- Preserve exact benchmark semantics: required languages `Java`, `Python`, `go`, `rust`, `typescript`; completion threshold `0.99`; frozen commit `cb24647f7c62b9704d10c97e615005d924c005f2`; provider `codex`; CLI `codex-cli 0.144.1`; strong/cheap model `gpt-5.6-sol`; concurrency `4`.
- Keep `AGENTS.md`, plugin manifests, and final release identity root-owned. This workstream must not edit them. The root integration pass performs one combined minor-version decision after label-validity and detector-quality work land.
- Keep `eval/bench/out/codocbench-derived.jsonl` and `eval/bench/out/codocbench-validated.votes.json`; they are label-validity evidence and are outside this workstream's cleanup scope.

---

## File Map

- Create `eval/bench/publication.py`: strict projection, deterministic export, manifest generation, historical-provenance verification, report parity, and CLI.
- Create `tests/test_bench_publication.py`: projection, manifest, tamper, bounds, provenance, report, rescore, and CLI tests.
- Create `eval/bench/public/0.4.0/manifest.json`: content-addressed index for the evaluated release.
- Create five `eval/bench/public/0.4.0/*.json` decision artifacts listed in Task 4.
- Rename `eval/bench/results-current.md` to `eval/bench/results-0.4.0.md`: prevent a changed judge from inheriting the old matrix as “current.”
- Modify `.github/workflows/test.yml`: fetch benchmark provenance and run the offline verifier.
- Modify `.gitignore`: explain that raw/local outputs remain ignored while `eval/bench/public/` is intentionally tracked; do not add an unignore rule because the current ignore targets only `eval/bench/out/`.
- Modify `eval/bench/README.md`, `eval/README.md`, and `README.md`: publication, verification, licensing, and 0.4.0-baseline wording.
- Remove only the obsolete tracked raw/legacy output files named in Task 5.
- Do not modify `eval/bench/artifact.py`, `eval/bench/report.py`, `eval/bench/runner.py`, `AGENTS.md`, or any plugin manifest in this workstream.

---

### Task 1: Lock the public projection contract with failing tests

**Files:**
- Create: `tests/test_bench_publication.py`
- Create in Task 2: `eval/bench/publication.py`

**Interfaces:**
- Consumes private artifact envelope: `schema_version`, `metadata`, `timing`, optional `provider_usage`, and full `rows`.
- Produces public artifact envelope with the same top-level benchmark fields and projected rows.
- Produces `project_artifact(document: dict) -> dict`.
- Produces `project_row(row: dict) -> dict`.
- Produces `project_trial(stages: dict) -> dict`.
- Raises `ValueError` for malformed or unexpected result structure; never silently copies unknown keys.

**Exact public row shape:**

```json
{
  "id": "pair-id",
  "language": "rust",
  "label": "consistent",
  "category": null,
  "got": {
    "final_status": "complete",
    "final_verdict": "consistent",
    "verdict": "consistent",
    "category": null,
    "contested": false
  },
  "trial": {
    "snap": {"status": "ok", "verdict": "consistent", "category": null},
    "challenge": {"status": "ok", "cracks": false},
    "prongs": [
      {"status": "ok", "role": "defend", "verdict": "consistent"},
      {"status": "ok", "role": "prove-wrong", "verdict": "consistent"},
      {"status": "ok", "role": "hardest-broken", "verdict": "consistent"}
    ],
    "blindspot": {"status": "ok", "missed_angle_present": false}
  }
}
```

`prongs_escalated` and `synthesis` are omitted when the private trial did not run them. An abstained stage retains only `{"status": "abstain"}`. Allowed stage fields are:

| Stage | Allowed fields |
|---|---|
| `snap` | `status`, `verdict`, `category` |
| `challenge` | `status`, `cracks` |
| `prongs` | list of `status`, `role`, `verdict` |
| `prongs_escalated` | list of `status`, `role`, `verdict` |
| `blindspot` | `status`, `missed_angle_present` |
| `synthesis` | `status`, `verdict`, `category` |

- [ ] **Step 1: Write the fixture builders and redaction tests**

Add these imports and helpers to `tests/test_bench_publication.py`:

```python
import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

from eval.bench import publication


EMPTY_SHA = hashlib.sha256(b"").hexdigest()


def metadata(dataset_path="eval/bench/input.jsonl", dataset_sha=None):
    return {
        "dataset": {"path": dataset_path, "sha256": dataset_sha or "1" * 64},
        "provider": "codex",
        "skill": {"path": "skills/evergreen/SKILL.md", "sha256": "2" * 64},
        "judge": {
            "path": "eval/bench/run_bench.py",
            "sha256": "3" * 64,
            "files": [{"path": "eval/bench/run_bench.py", "sha256": "4" * 64}],
        },
        "git": {
            "commit": "5" * 40,
            "tree": "6" * 40,
            "dirty": False,
            "status_sha256": EMPTY_SHA,
            "diff_sha256": EMPTY_SHA,
            "untracked_sha256": EMPTY_SHA,
        },
        "cli_version": "codex-cli 0.144.1",
        "settings": {
            "provider": "codex",
            "models": {"strong": "gpt-5.6-sol", "cheap": "gpt-5.6-sol"},
            "concurrency": 4,
        },
    }


def private_row(identifier="one", language="rust"):
    ok = lambda value: {"status": "ok", "value": value}
    return {
        "id": identifier,
        "func": "execute",
        "language": language,
        "code": "SECRET_SHAPED_SOURCE = 'do-not-publish'",
        "doc": "free-form documentation must not be copied",
        "label": "consistent",
        "category": None,
        "unexpected_source_field": "must not pass through",
        "got": {
            "final_status": "complete",
            "final_verdict": "consistent",
            "verdict": "consistent",
            "category": None,
            "why": "free-form final explanation",
            "contested": False,
            "unexpected_result_field": "must not pass through",
            "stages": {
                "snap": ok({
                    "id": identifier, "verdict": "consistent", "category": None,
                    "why": "free-form snap explanation", "unexpected": "drop",
                }),
                "challenge": ok({"cracks": False, "why": "free-form challenge"}),
                "prongs": [
                    ok({"role": role, "verdict": "consistent", "why": "free-form prong"})
                    for role in ("defend", "prove-wrong", "hardest-broken")
                ],
                "blindspot": ok({"missed_angle": "free-form candidate"}),
            },
        },
    }


def private_artifact(rows):
    return {
        "schema_version": 1,
        "metadata": metadata(),
        "timing": {"started_at": "2026-07-12T20:00:00Z", "elapsed_seconds": 1.25},
        "rows": rows,
    }
```

- [ ] **Step 2: Add an exact allowlist test**

```python
class ProjectionTests(unittest.TestCase):
    def test_projection_keeps_structured_decisions_and_drops_all_free_text(self):
        projected = publication.project_artifact(private_artifact([private_row()]))
        row = projected["rows"][0]

        self.assertEqual(set(row), {"id", "language", "label", "category", "got", "trial"})
        self.assertEqual(set(row["got"]), {
            "final_status", "final_verdict", "verdict", "category", "contested",
        })
        self.assertEqual(row["trial"]["snap"], {
            "status": "ok", "verdict": "consistent", "category": None,
        })
        self.assertEqual(row["trial"]["challenge"], {"status": "ok", "cracks": False})
        self.assertEqual(
            [item["role"] for item in row["trial"]["prongs"]],
            ["defend", "prove-wrong", "hardest-broken"],
        )
        self.assertEqual(row["trial"]["blindspot"], {
            "status": "ok", "missed_angle_present": True,
        })
        encoded = json.dumps(projected, sort_keys=True)
        for forbidden in (
            "code", "doc", "func", "why", "reason", "missed_angle\"",
            "SECRET_SHAPED_SOURCE", "free-form", "unexpected_source_field",
            "unexpected_result_field",
        ):
            self.assertNotIn(forbidden, encoded)
```

- [ ] **Step 3: Add strict structure and abstention tests**

```python
    def test_projection_preserves_escalation_synthesis_and_abstention_status(self):
        row = private_row()
        row["got"]["final_status"] = "abstain"
        row["got"]["final_verdict"] = None
        row["got"]["verdict"] = None
        row["got"]["stages"]["prongs"][1] = {
            "status": "abstain", "reason": "malformed provider output",
        }
        row["got"]["stages"]["prongs_escalated"] = row["got"]["stages"]["prongs"]
        row["got"]["stages"]["synthesis"] = {
            "status": "abstain", "reason": "timeout /private/path",
        }

        projected = publication.project_row(row)

        self.assertEqual(projected["got"]["final_status"], "abstain")
        self.assertEqual(projected["trial"]["prongs"][1], {"status": "abstain"})
        self.assertEqual(projected["trial"]["synthesis"], {"status": "abstain"})
        self.assertNotIn("/private/path", json.dumps(projected))

    def test_projection_rejects_unknown_stage_and_invalid_types(self):
        row = private_row()
        row["got"]["stages"]["invented"] = {"status": "ok", "value": {}}
        with self.assertRaisesRegex(ValueError, "unknown trial stage"):
            publication.project_row(row)

        row = private_row()
        row["got"]["contested"] = "false"
        with self.assertRaisesRegex(ValueError, "contested"):
            publication.project_row(row)
```

- [ ] **Step 4: Run the focused tests and confirm the intended failure**

Run:

```bash
python3 -m unittest tests.test_bench_publication.ProjectionTests -v
```

Expected: FAIL with an import error because `eval/bench/publication.py` does not exist.

- [ ] **Step 5: Commit the failing test contract**

```bash
git add tests/test_bench_publication.py
git commit -m "test(eval): define public decision artifact contract"
```

### Task 2: Implement deterministic projection and bounded serialization

**Files:**
- Create: `eval/bench/publication.py`
- Modify: `tests/test_bench_publication.py`

**Interfaces:**
- `project_artifact(document: dict) -> dict`
- `project_row(row: dict) -> dict`
- `project_trial(stages: dict) -> dict`
- `canonical_bytes(value: dict) -> bytes`: UTF-8, two-space indentation, sorted keys, one trailing newline, `allow_nan=False`; this must match `artifact.atomic_write_json` byte-for-byte.
- Reuse `artifact.validate_benchmark_row`, `artifact.valid_iso_time`, `artifact.validate_usage`, and `report._validate_metadata` instead of creating a second provenance validator.

- [ ] **Step 1: Add deterministic serialization and bounds tests**

```python
    def test_canonical_bytes_are_stable_and_newline_terminated(self):
        first = publication.canonical_bytes({"z": 1, "a": {"y": 2, "x": 3}})
        second = publication.canonical_bytes({"a": {"x": 3, "y": 2}, "z": 1})
        self.assertEqual(first, second)
        self.assertEqual(
            first,
            b'{\n  "a": {\n    "x": 3,\n    "y": 2\n  },\n  "z": 1\n}\n',
        )

    def test_projection_rejects_legacy_or_malformed_envelopes(self):
        with self.assertRaisesRegex(ValueError, "schema"):
            publication.project_artifact([])
        broken = private_artifact([private_row()])
        broken["timing"]["elapsed_seconds"] = float("nan")
        with self.assertRaisesRegex(ValueError, "timing"):
            publication.project_artifact(broken)
```

- [ ] **Step 2: Implement constants and field validators**

Use these exact constants:

```python
PUBLICATION_SCHEMA_VERSION = 1
MAX_PUBLIC_ARTIFACT_BYTES = 2 * 1024 * 1024
MAX_TOTAL_PUBLIC_BYTES = 8 * 1024 * 1024
MAX_PUBLIC_ROWS = 100_000
MAX_PUBLIC_ARTIFACTS = 5
VERDICTS = {"consistent", "inconsistent"}
STATUSES = {"ok", "abstain"}
STAGES = {"snap", "challenge", "prongs", "prongs_escalated", "blindspot", "synthesis"}
PRONG_ROLES = {"defend", "prove-wrong", "hardest-broken"}
```

Implement small helpers that require exact primitive types (`type(value) is bool` for booleans), validate verdict/category/status enums, and return new dictionaries. Do not mutate or shallow-copy source dictionaries. Reject unknown stage names, malformed stage lists, duplicate prong roles, and fields required by a successful stage that are absent.

- [ ] **Step 3: Implement the stage projection**

The projection logic must follow this structure:

```python
def project_trial(stages):
    if not isinstance(stages, dict):
        raise ValueError("trial stages must be an object")
    unknown = set(stages) - STAGES
    if unknown:
        raise ValueError("unknown trial stage")
    projected = {}
    if "snap" in stages:
        projected["snap"] = _project_value_stage(
            stages["snap"], required=("verdict",), optional=("category",)
        )
    if "challenge" in stages:
        projected["challenge"] = _project_value_stage(
            stages["challenge"], required=("cracks",), optional=()
        )
    for name in ("prongs", "prongs_escalated"):
        if name in stages:
            projected[name] = _project_prongs(stages[name])
    if "blindspot" in stages:
        projected["blindspot"] = _project_blindspot(stages["blindspot"])
    if "synthesis" in stages:
        projected["synthesis"] = _project_value_stage(
            stages["synthesis"], required=("verdict",), optional=("category",)
        )
    return projected
```

`_project_blindspot` converts a successful raw `missed_angle` value to `missed_angle_present: bool`; it never copies the string. `_project_prongs` preserves list order because trial vote order is evidence, while canonical dictionary keys remain sorted.

- [ ] **Step 4: Implement row and envelope projection**

`project_row` must first call `artifact.validate_benchmark_row(row, require_result=True)`, validate `got.final_status`, final verdict/null pairing, predicted category, and `contested`, then construct only the exact row shape declared in Task 1. `project_artifact` must:

1. require `schema_version == 1`;
2. validate metadata through `report._validate_metadata`;
3. validate aware ISO timing and finite non-negative elapsed time;
4. validate optional numeric provider usage;
5. project all rows;
6. reject duplicate IDs;
7. preserve top-level `provider_usage` only when present;
8. reject more than `MAX_PUBLIC_ROWS` rows.

- [ ] **Step 5: Run projection tests**

Run:

```bash
python3 -m unittest tests.test_bench_publication.ProjectionTests -v
```

Expected: all projection tests pass.

- [ ] **Step 6: Commit**

```bash
git add eval/bench/publication.py tests/test_bench_publication.py
git commit -m "feat(eval): project safe public decision artifacts"
```

### Task 3: Add content-addressed export, manifest, and offline verification

**Files:**
- Modify: `eval/bench/publication.py`
- Modify: `tests/test_bench_publication.py`

**Interfaces:**
- `parse_source(value: str) -> tuple[str, Path]`, parsing the first `=` as `SHA256=PATH`.
- `export_publication(source_specs: list[tuple[str, Path]], output_dir: Path, evaluated_release: str, required_languages: list[str], coverage_threshold: float, report_path: Path, repo: Path) -> Path` returning `manifest.json`.
- `verify_publication(manifest_path: Path, repo: Path, report_path: Path) -> list[Path]` returning public artifact paths in manifest order or raising `ValueError`.
- CLI subcommands:

```text
publication.py export --source SHA256=PATH --source SHA256=PATH --output-dir DIR
                      --evaluated-release VERSION --require-language LANGUAGE
                      --coverage-threshold FLOAT --report PATH
publication.py verify --manifest PATH --repo PATH --report PATH
```

**Manifest schema:**

```python
manifest = {
    "schema_version": 1,
    "kind": "evergreen-benchmark-decision-publication",
    "evaluated_release": evaluated_release,
    "projection": {
        "name": "structured-decisions",
        "version": 1,
        "omitted_fields": ["code", "doc", "func", "missed_angle", "reason", "why"],
    },
    "publication": {
        "coverage_threshold": coverage_threshold,
        "required_languages": sorted(required_languages),
    },
    "provenance": {
        "cli_version": shared_metadata["cli_version"],
        "commit": shared_metadata["git"]["commit"],
        "judge_sha256": shared_metadata["judge"]["sha256"],
        "provider": shared_metadata["provider"],
        "settings_sha256": hashlib.sha256(json.dumps(
            shared_metadata["settings"], sort_keys=True, separators=(",", ":")
        ).encode()).hexdigest(),
        "skill_sha256": shared_metadata["skill"]["sha256"],
        "tree": shared_metadata["git"]["tree"],
    },
    "artifacts": artifact_entries,
    "report": {
        "path": repository_path(report_path, repo),
        "sha256": artifact.sha256_file(report_path),
    },
}
```

Each `artifact_entries` item has exactly `bytes`, `dataset`, `language`, `path`, `rows`, `sha256`, and `source`. `dataset` has exactly `path` and `sha256`; `source` has exactly `bytes` and `sha256`. Hashes are 64-character lowercase SHA-256 values computed from the named bytes.

- [ ] **Step 1: Add export and manifest tests**

Use a temporary Git repository and real files. Add tests that assert:

```python
class PublicationTests(unittest.TestCase):
    def test_parse_source_requires_lowercase_sha256_and_regular_path(self):
        digest, path = publication.parse_source("a" * 64 + "=/tmp/source.json")
        self.assertEqual(digest, "a" * 64)
        self.assertEqual(path, Path("/tmp/source.json"))
        for value in ("missing", "xyz=/tmp/source", "A" * 64 + "=/tmp/source"):
            with self.subTest(value=value), self.assertRaisesRegex(ValueError, "source"):
                publication.parse_source(value)

    def test_export_rejects_source_hash_mismatch_before_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "source.json"
            source.write_text(json.dumps(private_artifact([private_row()])))
            output = root / "public"
            with self.assertRaisesRegex(ValueError, "source artifact SHA-256"):
                publication.export_publication(
                    [("0" * 64, source)], output, "0.4.0", ["rust"], 0.99,
                    root / "report.md", root,
                )
            self.assertFalse(output.exists())
```

Also assert deterministic manifest ordering by language then path, exact source byte/hash recording, no absolute source path, atomic output replacement, and refusal of symlink source/output files.

- [ ] **Step 2: Add verifier tamper and join tests**

Create a helper that initializes a temporary Git repository containing the dataset, skill, judge files, public artifact, report, and manifest. Add tests for:

- public artifact byte/hash tampering;
- manifest path traversal and absolute paths;
- missing, extra, duplicate, or undeclared languages;
- duplicate IDs across artifacts;
- changed dataset bytes, label, category, or language;
- historical commit/tree mismatch;
- skill and judge-file hash mismatch at the historical commit;
- forbidden keys or unexpected free-form strings in a public row;
- per-file, aggregate-byte, artifact-count, and row-count bounds;
- report hash mismatch and regenerated-report mismatch;
- a valid package returning the five paths in deterministic order.

Use explicit assertions such as:

```python
    def test_verify_rejects_dataset_join_tampering(self):
        repo, manifest_path, report_path = self.make_publication_fixture()
        dataset = repo / "eval/bench/input.jsonl"
        row = json.loads(dataset.read_text().splitlines()[0])
        row["label"] = "inconsistent"
        dataset.write_text(json.dumps(row) + "\n")
        with self.assertRaisesRegex(ValueError, "dataset SHA-256"):
            publication.verify_publication(manifest_path, repo, report_path)

    def test_verify_rejects_public_free_text_even_when_manifest_is_rehashed(self):
        repo, manifest_path, report_path = self.make_publication_fixture()
        public_path = next((repo / "eval/bench/public/0.4.0").glob("bench-*.json"))
        value = json.loads(public_path.read_text())
        value["rows"][0]["got"]["why"] = "must not become public"
        self.rewrite_artifact_and_manifest(public_path, manifest_path, value)
        with self.assertRaisesRegex(ValueError, "public row fields"):
            publication.verify_publication(manifest_path, repo, report_path)
```

- [ ] **Step 3: Implement bounded export**

Export must perform these actions in order:

1. require exactly five sources for the production package, while unit tests may pass 1–5 when `required_languages` declares the same set;
2. hash each regular non-symlink source with `artifact.sha256_file(source_path, artifact.MAX_ARTIFACT_BYTES)`;
3. compare the hash to its `--source` expectation before creating output;
4. load and validate sources through `report._load_artifacts` so provenance compatibility and global duplicate IDs use the report's existing policy;
5. load each dataset using `runner.load_dataset` and call `artifact.resume_state(source, source["metadata"], dataset_rows)` to prove private rows match the hashed dataset before projection;
6. project and canonicalize each artifact;
7. enforce per-file and aggregate byte limits;
8. require that the immutable final `output_dir` does not already exist;
9. create a sibling temporary directory, write each artifact there with `artifact.atomic_write_json`, and confirm each file exactly equals `canonical_bytes(projected)`;
10. regenerate Markdown with `report.render_markdown` from the staged public artifacts and require exact equality with `report_path`;
11. build and write the canonical manifest inside the staged directory;
12. fsync staged files and directory, then atomically rename the complete staged directory to `output_dir`.

On failure, remove only the executor-created staging directory through a bounded path it owns. Do not create a half-manifest, replace an existing versioned publication, or delete a prior valid publication.

- [ ] **Step 4: Implement offline verification**

Verification must use bounded regular-file reads and:

1. require the exact manifest keys/schema/kind/projection allowlist;
2. reject absolute, escaping, duplicate, and non-regular paths;
3. verify public artifact and report bytes/SHA-256;
4. validate every public artifact with `report._load_artifacts`;
5. recursively enforce the public row/stage allowlists;
6. verify each dataset file SHA-256, unique row ID, language, label, and category join;
7. run Git with argument arrays to confirm the manifest commit exists, its tree matches, and the skill, judge files, and dataset blobs at that commit match the recorded hashes;
8. compare the compatibility provenance and manifest summary;
9. regenerate the report and compare exact bytes;
10. return public paths only after every check passes.

Use `git cat-file -e`, `git rev-parse manifest_commit^{tree}`, and `git show manifest_commit:repository_path` through the existing bounded subprocess helpers, substituting the validated manifest fields as argument-array elements. A shallow checkout missing the commit is a verification failure, not a warning.

- [ ] **Step 5: Implement the CLI**

Use `argparse` subparsers. On success, print only:

```text
exported public benchmark manifest: eval/bench/public/0.4.0/manifest.json
```

or:

```text
verified public benchmark publication: 5 artifacts
```

On `OSError`, `ValueError`, or `json.JSONDecodeError`, print `publication error: ` followed by the bounded exception message to stderr and exit `2`. Never include JSON payloads or environment values.

- [ ] **Step 6: Run focused tests**

Run:

```bash
python3 -m unittest tests.test_bench_publication -v
```

Expected: all publication tests pass with no network or model calls.

- [ ] **Step 7: Commit**

```bash
git add eval/bench/publication.py tests/test_bench_publication.py
git commit -m "feat(eval): verify public benchmark publications"
```

### Task 4: Generate and prove the Evergreen 0.4.0 publication

**Files:**
- Create: `eval/bench/public/0.4.0/manifest.json`
- Create: `eval/bench/public/0.4.0/bench-cascade-java-trial-codex-gpt-5.6-sol.json`
- Create: `eval/bench/public/0.4.0/bench-codocbench-validated-trial-codex-gpt-5.6-sol.json`
- Create: `eval/bench/public/0.4.0/bench-codocbench-ts-validated-trial-codex-gpt-5.6-sol.json`
- Create: `eval/bench/public/0.4.0/bench-codocbench-rust-validated-trial-codex-gpt-5.6-sol.json`
- Create: `eval/bench/public/0.4.0/bench-codocbench-go-validated-trial-codex-gpt-5.6-sol.json`
- Rename: `eval/bench/results-current.md` to `eval/bench/results-0.4.0.md`

**Interfaces:**
- Public artifact envelope remains compatible with `report.py` and `run_bench.py --rescore`.
- Manifest binds the public artifacts to private source hashes, datasets, evaluated release, report, and frozen provenance.

**Required private source identities:**

| Language | Rows | Bytes | SHA-256 |
|---|---:|---:|---|
| Java | 885 | 3,727,667 | `a03f1b33c3f3e13ee60226298dd1dc83d611ba98ad625d86f22157544175a090` |
| Python | 332 | 1,891,435 | `b56b5541ad9e9ad31df3f756e6eb05509241b44651d159396ed0e93ffb566cc9` |
| TypeScript | 284 | 1,509,341 | `16ea13944ce98c9fa62026328903e1babd82a92daf547bd0d45a79b662cd1c94` |
| Rust | 304 | 1,431,430 | `88b499de0f0fcf037d54e00dc9c9d36183e6adb4c731f2441f300623a2929e0d` |
| Go | 299 | 1,444,439 | `b04a027dee961480a1fe3f8505b1b9bfb5d43ffd80b3e6b50861639246ed91ef` |

- [ ] **Step 1: Rename the frozen report without changing its contents**

```bash
git mv eval/bench/results-current.md eval/bench/results-0.4.0.md
```

Expected: `git diff --no-index` against a pre-rename copy would show no content change; the report still says PASS with 2,103/2,104 completed.

- [ ] **Step 2: Confirm every private source against its external archive final**

Run `shasum -a 256` and `stat` on the five live files and the corresponding archive finals. Expected: each live/archive pair matches the table above. Do not print artifact content.

- [ ] **Step 3: Export with exact source hashes**

```bash
python3 eval/bench/publication.py export \
  --source a03f1b33c3f3e13ee60226298dd1dc83d611ba98ad625d86f22157544175a090=~/evergreen-benchmark-run/eval/bench/out/bench-cascade-java-trial-codex-gpt-5.6-sol.json \
  --source b56b5541ad9e9ad31df3f756e6eb05509241b44651d159396ed0e93ffb566cc9=~/evergreen-benchmark-run/eval/bench/out/bench-codocbench-validated-trial-codex-gpt-5.6-sol.json \
  --source 16ea13944ce98c9fa62026328903e1babd82a92daf547bd0d45a79b662cd1c94=~/evergreen-benchmark-run/eval/bench/out/bench-codocbench-ts-validated-trial-codex-gpt-5.6-sol.json \
  --source 88b499de0f0fcf037d54e00dc9c9d36183e6adb4c731f2441f300623a2929e0d=~/evergreen-benchmark-run/eval/bench/out/bench-codocbench-rust-validated-trial-codex-gpt-5.6-sol.json \
  --source b04a027dee961480a1fe3f8505b1b9bfb5d43ffd80b3e6b50861639246ed91ef=~/evergreen-benchmark-run/eval/bench/out/bench-codocbench-go-validated-trial-codex-gpt-5.6-sol.json \
  --output-dir eval/bench/public/0.4.0 \
  --evaluated-release 0.4.0 \
  --require-language Java \
  --require-language Python \
  --require-language typescript \
  --require-language rust \
  --require-language go \
  --coverage-threshold 0.99 \
  --report eval/bench/results-0.4.0.md
```

Expected: `exported public benchmark manifest: eval/bench/public/0.4.0/manifest.json`.

- [ ] **Step 4: Verify size and forbidden-content gates**

Run:

```bash
python3 eval/bench/publication.py verify \
  --manifest eval/bench/public/0.4.0/manifest.json \
  --repo . \
  --report eval/bench/results-0.4.0.md
du -ch eval/bench/public/0.4.0/*.json
```

Expected:

- verifier prints `verified public benchmark publication: 5 artifacts`;
- five artifact files plus `manifest.json` exist;
- artifact total is below 8 MiB and each artifact below 2 MiB;
- expected projection is approximately 1.57 MB total, but the hard limits—not an approximate byte count—are the acceptance criteria.

- [ ] **Step 5: Prove existing report and rescore compatibility**

```bash
TMP_REPORT="$(mktemp)"
python3 eval/bench/report.py \
  eval/bench/public/0.4.0/bench-codocbench-validated-trial-codex-gpt-5.6-sol.json \
  eval/bench/public/0.4.0/bench-cascade-java-trial-codex-gpt-5.6-sol.json \
  eval/bench/public/0.4.0/bench-codocbench-ts-validated-trial-codex-gpt-5.6-sol.json \
  eval/bench/public/0.4.0/bench-codocbench-rust-validated-trial-codex-gpt-5.6-sol.json \
  eval/bench/public/0.4.0/bench-codocbench-go-validated-trial-codex-gpt-5.6-sol.json \
  --require-language Python \
  --require-language Java \
  --require-language typescript \
  --require-language rust \
  --require-language go \
  --coverage-threshold 0.99 \
  --markdown "$TMP_REPORT"
cmp eval/bench/results-0.4.0.md "$TMP_REPORT"
rm -f "$TMP_REPORT"
python3 eval/bench/run_bench.py --rescore \
  eval/bench/public/0.4.0/bench-codocbench-rust-validated-trial-codex-gpt-5.6-sol.json
```

Expected: `cmp` exits `0`; rescoring reports Rust coverage `303/304`, one abstention, TP 13, FP 15, FN 6, TN 269.

- [ ] **Step 6: Commit the immutable 0.4.0 package**

```bash
git add eval/bench/public/0.4.0 eval/bench/results-0.4.0.md
git commit -m "data(eval): publish auditable 0.4.0 decisions"
```

### Task 5: Gate publication in CI and retire obsolete tracked raw outputs

**Files:**
- Modify: `.github/workflows/test.yml`
- Modify: `.gitignore`
- Delete: `eval/bench/out/bench-claude-haiku-4-5-20251001.json`
- Delete: `eval/bench/out/bench-default.json`
- Delete: `eval/bench/out/bench-codocbench-rust-validated-claude-opus-4-8-base.json`
- Delete: `eval/bench/out/bench-codocbench-go-validated-claude-opus-4-8-base.json`
- Delete: `eval/bench/out/bench-codocbench-validated-claude-opus-4-8.json`
- Delete: `eval/bench/out/bench-codocbench-validated-claude-haiku-4-5-20251001.json`
- Delete: `eval/bench/out/bench-codocbench-ts-validated-claude-opus-4-8-base.json`
- Delete: `eval/bench/out/bench-codocbench-validated-claude-opus-4-8-audit-base-prove-refute.json`
- Delete: `eval/bench/out/bench-cascade-claude-haiku-4-5-20251001.json`
- Delete: `eval/bench/out/bench-cascade-claude-opus-4-8.json`
- Delete: `eval/bench/out/bench-cascade-claude-opus-4-8-audit-base-prove-refute.json`
- Delete: `eval/bench/out/cascade.jsonl`
- Preserve: `eval/bench/out/codocbench-derived.jsonl`
- Preserve: `eval/bench/out/codocbench-validated.votes.json`

**Interfaces:**
- CI verifies the committed publication entirely offline.
- Full Git history is available to the verifier in the Python 3.11 matrix job.
- Raw future benchmark output remains ignored.

- [ ] **Step 1: Add a failing repository-package test**

```python
class CommittedPublicationTests(unittest.TestCase):
    def test_committed_0_4_0_publication_verifies(self):
        repo = Path(__file__).parent.parent.resolve()
        paths = publication.verify_publication(
            repo / "eval/bench/public/0.4.0/manifest.json",
            repo,
            repo / "eval/bench/results-0.4.0.md",
        )
        self.assertEqual(len(paths), 5)
```

Run `python3 -m unittest tests.test_bench_publication.CommittedPublicationTests -v` and confirm it fails before the package or full historical provenance is available.

- [ ] **Step 2: Give the verifier full history and add its CI step**

Under the `test` job's `actions/checkout` step in `.github/workflows/test.yml`, add:

```yaml
        with:
          fetch-depth: 0
```

After `Benchmark self-test`, add:

```yaml
      - name: Public benchmark verification
        run: >-
          python3 eval/bench/publication.py verify
          --manifest eval/bench/public/0.4.0/manifest.json
          --repo .
          --report eval/bench/results-0.4.0.md
```

- [ ] **Step 3: Clarify `.gitignore` ownership**

Replace the bare benchmark ignore lines with comments while preserving the existing patterns:

```gitignore
# Raw/local benchmark runs and logs; never publish these files directly.
eval/out/
eval/bench/out/
eval/bench/*.log
# Sanitized, content-addressed publications live in eval/bench/public/ and are tracked.
```

Do not add `!eval/bench/public/`; the existing ignore pattern does not cover it.

- [ ] **Step 4: Remove obsolete tracked raw outputs only**

Use `git rm` on the twelve files listed under **Delete**. Before removal, run:

```bash
git ls-files --error-unmatch eval/bench/out/codocbench-derived.jsonl
git ls-files --error-unmatch eval/bench/out/codocbench-validated.votes.json
```

Expected: both commands exit `0`. If either label-evidence file is absent or a sibling workstream is modifying it, stop this cleanup step and let the root integrator reconcile it; do not delete or recreate label evidence.

- [ ] **Step 5: Run CI-equivalent publication gates**

```bash
python3 -m unittest tests.test_bench_publication -v
python3 -m unittest discover -s tests -p 'test_*.py' -v
python3 eval/bench/run_bench.py --selftest
python3 eval/bench/publication.py verify \
  --manifest eval/bench/public/0.4.0/manifest.json \
  --repo . \
  --report eval/bench/results-0.4.0.md
git diff --check
```

Expected: all tests pass, self-test prints `selftest ok`, publication verification reports five artifacts, and `git diff --check` emits nothing.

- [ ] **Step 6: Commit**

Stage in a separate command from the commit, in accordance with Evergreen's guard:

```bash
git add .github/workflows/test.yml .gitignore tests/test_bench_publication.py
git add -u eval/bench/out
```

Then commit:

```bash
git commit -m "ci(eval): verify public benchmark evidence"
```

### Task 6: Document scope, licensing, and the frozen 0.4.0 baseline

**Files:**
- Modify: `eval/bench/README.md`
- Modify: `eval/README.md`
- Modify: `README.md`
- Do not modify: `AGENTS.md`, `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`, `.claude-plugin/marketplace.json`

**Interfaces:**
- Documentation points to `eval/bench/public/0.4.0/manifest.json` and `eval/bench/results-0.4.0.md`.
- Documentation calls these results the “Evergreen 0.4.0 baseline,” not the current judge after detector changes.
- Documentation distinguishes decision auditability from label-selection reproducibility.

**Ownership precondition:** The root agent executes this task after Tasks 1–5, or explicitly reassigns the three shared documentation files to this workstream. A public-auditability worker must not edit them concurrently with label-validity, detector-quality, or release integration.

- [ ] **Step 1: Replace stale result links and claims**

In `README.md`, `eval/README.md`, and `eval/bench/README.md`:

- replace `results-current.md` links with `results-0.4.0.md`;
- replace “current judge” with “Evergreen 0.4.0 baseline” wherever the statement refers to commit `cb24647f7c62b9704d10c97e615005d924c005f2`;
- retain the exact measured metrics and completion counts;
- do not claim the detector is best-in-class;
- do not claim a changed skill/judge has inherited the 0.4.0 numbers.

- [ ] **Step 2: Add exact verification and rescore commands**

Add this section to `eval/bench/README.md`:

````markdown
## Public decision artifacts

The content-addressed [Evergreen 0.4.0 publication](public/0.4.0/manifest.json) contains every
retained pair's benchmark label, final decision, and structured trial outcomes. It deliberately
omits source code, documentation text, and free-form reviewer prose. Join a decision to its declared
dataset by `metadata.dataset.sha256` plus row `id`.

Verify the five files, historical provenance, dataset joins, and checked-in report without a model
or API call:

```sh
python3 eval/bench/publication.py verify \
  --manifest eval/bench/public/0.4.0/manifest.json \
  --repo . \
  --report eval/bench/results-0.4.0.md
```

Rescore any language directly:

```sh
python3 eval/bench/run_bench.py --rescore \
  eval/bench/public/0.4.0/bench-cascade-java-trial-codex-gpt-5.6-sol.json
```
````

- [ ] **Step 3: Add the licensing and reconstruction boundary**

Add this exact substance, with normal prose wrapping, to `eval/bench/README.md`:

```markdown
### Publication and licensing boundary

CASCADE is attributed to its upstream MIT-licensed repository and frozen source commit above.
CoDocBench's upstream repository did not declare a detectable license when this publication was
prepared. The public decision package therefore does not duplicate source code, docstrings, or
free-form model explanations. This is a publication-scope constraint, not legal advice or a claim
that existing research inputs have been relicensed.

The package supports decision inspection and metric rescoring for the retained evaluated rows. It
does not reconstruct the full candidate-selection process: the TypeScript, Rust, and Go vote logs
do not contain the discarded candidates' source payloads or exact source revisions. Label validity,
selection validity, and decision quality remain separate claims.
```

- [ ] **Step 4: Document chain-of-custody honestly**

Explain that public hashes, dataset joins, historical Git blobs, and report regeneration are independently checkable. Explain separately that `source.sha256` records the private frozen artifact used during export; CI cannot independently inspect an artifact that is intentionally not published. Do not use “fully reproducible” for candidate selection or private-source derivation.

- [ ] **Step 5: Run link, documentation, and full verification**

```bash
test -f eval/bench/results-0.4.0.md
test -f eval/bench/public/0.4.0/manifest.json
! grep -R "results-current.md" README.md eval/README.md eval/bench/README.md
python3 eval/bench/publication.py verify \
  --manifest eval/bench/public/0.4.0/manifest.json \
  --repo . \
  --report eval/bench/results-0.4.0.md
python3 -m unittest discover -s tests -p 'test_*.py' -v
bash tests/action.sh
bash tests/hooks.sh
python3 eval/bench/run_bench.py --selftest
git diff --check
```

Expected: files exist; stale-link grep exits `1`; verifier reports five artifacts; all Python, Action, hook, and benchmark tests pass; diff check is clean.

- [ ] **Step 6: Run Evergreen's freshness ladder over the changed docs**

Check every path, CLI flag, file name, schema field, measured number, and licensing statement against the implementation and manifest. Required verdict: no dead `results-current.md` link, no “current judge” claim attached to a changed judge, and no claim of human validation unless the label-validity workstream supplies it.

- [ ] **Step 7: Commit the documentation**

```bash
git add README.md eval/README.md eval/bench/README.md
git commit -m "docs(eval): expose the auditable 0.4.0 baseline"
```

### Task 7: Root integration with label-validity, detector-quality, and release identity

**Files:**
- Reconcile only; root agent owns shared files and release manifests.
- Potential root-owned modifications: `README.md`, `eval/bench/README.md`, `eval/bench/results-0.4.0.md`, `.claude-plugin/plugin.json`, `.codex-plugin/plugin.json`, `.claude-plugin/marketplace.json`.

**Interfaces:**
- Public auditability remains a frozen 0.4.0 data product even after the source project advances.
- Human-label evidence uses a separate schema/package and never overwrites benchmark model outputs.
- Detector experiments may consume structured trial outcomes but may not mutate the frozen files.

- [ ] **Step 1: Exchange and reconcile sibling constraints**

Confirm these invariants before merging:

- label-validity artifacts name `derived_label`, human labels, adjudication, and model outputs distinctly;
- no sibling plan calls the three-LLM majority a human-validated truth set;
- detector-quality code reads public artifacts without requiring stripped prose;
- the 0.4.0 package remains byte-identical after integration;
- any new source pool or human-labeled set gets its own versioned manifest rather than changing `public/0.4.0`.

- [ ] **Step 2: Resolve README/report wording after detector changes**

If the judge or skill changes, keep the old matrix under `results-0.4.0.md` and call it a historical baseline. Do not create `results-current.md` or claim new quality numbers until a new frozen five-language gate passes.

- [ ] **Step 3: Make one release-identity decision**

After all three workstreams pass, apply Evergreen's release policy once. A backward-compatible public-auditability and judge-quality milestone is a likely `0.5.0` source release, but the root agent must compare the integrated change against the last marketing-version commit and put that judgment on trial. If `0.5.0` is sustained, update the Claude manifest, Codex manifest, marketplace version/description, and any current-source README claim together. External marketplace/release publication remains unverified unless directly performed with authority.

- [ ] **Step 4: Run the complete integration gate**

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
bash tests/action.sh
bash tests/hooks.sh
python3 eval/bench/run_bench.py --selftest
python3 eval/bench/publication.py verify \
  --manifest eval/bench/public/0.4.0/manifest.json \
  --repo . \
  --report eval/bench/results-0.4.0.md
python3 bin/evergreen doctor --repo .
git diff --check
git status --short
```

Expected: all automated gates pass; doctor reports no owned-state drift; diff check is silent; `git status` contains only intentional integrated files before the root commit.

- [ ] **Step 5: Review repository-size outcome**

```bash
git ls-files eval/bench/public/0.4.0 | wc -l
git ls-files eval/bench/out | sort
du -ch eval/bench/public/0.4.0/*.json
git count-objects -vH
```

Expected: six public files including the manifest; no obsolete `bench-*.json` files remain tracked under `eval/bench/out`; the two label-evidence files remain unless the label-validity workstream deliberately moved them; public artifacts stay under their declared limits.

- [ ] **Step 6: Commit root-owned integration separately from publication data**

Stage the exact root-owned files in one tool call, inspect `git diff --cached`, then commit in a separate call:

```bash
git commit -m "release: integrate evergreen evidence remediation"
```

Do not push or publish a marketplace release unless the user explicitly requests that external mutation.

---

## Self-Review Checklist

- [ ] Every implementation file and generated artifact has an exact path.
- [ ] Every public field is allowlisted; source and free-form fields are explicitly forbidden.
- [ ] Export verifies exact private hashes before writing; verify is fully offline and fail-closed.
- [ ] Existing report and rescore interfaces remain compatible without changing their code.
- [ ] The manifest distinguishes independently verifiable public evidence from private chain-of-custody hashes.
- [ ] CoDocBench license uncertainty and incomplete candidate reconstruction are explicit release constraints.
- [ ] Label evidence files are preserved and sibling-owned.
- [ ] The old matrix is tied to evaluated release 0.4.0 and cannot silently become a changed judge's result.
- [ ] CI has full Git history for historical provenance checks.
- [ ] No paid run, model call, network call, plugin-manifest edit, push, or publication is required by this workstream.
- [ ] No placeholder remains in the plan or in committed/generated files.
