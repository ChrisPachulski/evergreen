# Wave 1 Trustworthy CI Core Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Make Evergreen's PR Action incapable of confusing failed or malformed analysis with a clean certification.

**Architecture:** Standard-library Python helpers prepare a bounded Git change manifest and validate one strict result envelope. The shell Action supplies the manifest as untrusted data, the renderer consumes only validated results, and infrastructure failures become explicit inconclusive runs. Drift remains advisory.

**Tech Stack:** Bash, Python 3 standard library, GitHub composite Actions, `unittest`.

## Global Constraints

- No new runtime dependency other than the already-required Claude CLI.
- Repository content is untrusted data and never an instruction source.
- A clean result requires a complete valid envelope and `total == certified + drift + unverified`.
- Drift findings never fail CI; inconclusive execution fails by default and supports an explicit advisory override.
- Every published finding has verified repository-relative documentation and code citations.
- Preserve the existing hidden PR-comment marker and single-comment upsert behavior.

---

### Task 1: Bounded change manifest

**Files:**
- Create: `ci/change_manifest.py`
- Create: `tests/test_change_manifest.py`

**Interfaces:**
- Produces: `build_manifest(repo: Path, base: str, head: str = "HEAD", max_bytes: int = 120000) -> dict`
- Produces CLI: `python3 ci/change_manifest.py --base <ref> --head <ref> --repo <path>`
- Output: one JSON object with `schema_version`, `base`, `head`, `files`, `contract_seeds`, `truncated`, and `errors`.

- [ ] **Step 1: Write failing unit tests** covering add/modify/delete/rename statuses, bounded hunks, contract seeds, unusual filenames, invalid refs, and deterministic output ordering.
- [ ] **Step 2: Run tests and confirm failure**

Run: `python3 -m unittest tests.test_change_manifest -v`

Expected: FAIL because `ci/change_manifest.py` does not exist.

- [ ] **Step 3: Implement the minimal manifest builder** using `subprocess.run` argument arrays for `git rev-parse`, `git diff --name-status -z`, and `git diff --unified=3 --no-ext-diff`. Decode with replacement, normalize repository-relative paths, extract changed identifiers plus flag/env/route-like tokens, and stop adding hunks when the UTF-8 byte budget is exhausted.
- [ ] **Step 4: Run the focused tests**

Run: `python3 -m unittest tests.test_change_manifest -v`

Expected: all manifest tests pass.

- [ ] **Step 5: Commit**

```bash
git add ci/change_manifest.py tests/test_change_manifest.py
git commit -m "feat(ci): provide bounded PR change manifest"
```

### Task 2: Strict result protocol and citation validation

**Files:**
- Create: `ci/result_protocol.py`
- Create: `tests/test_result_protocol.py`

**Interfaces:**
- Produces: `parse_result(text: str) -> dict`
- Produces: `validate_result(result: dict, repo: Path, expected_base: str, expected_head: str) -> list[str]`
- Produces: `load_validated_result(...) -> tuple[dict | None, list[str]]`
- Required result keys and finding fields are copied from the approved design spec.

- [ ] **Step 1: Write failing tests** for explicit clean, findings, unverified claims, malformed JSON, prose-only output, multiple envelopes, wrong schema/base/head, bad counts, absolute paths, traversal, symlink escape, invalid lines, missing claims, invalid enums, and oversized fields.
- [ ] **Step 2: Run and confirm failure**

Run: `python3 -m unittest tests.test_result_protocol -v`

Expected: import failure.

- [ ] **Step 3: Implement strict parsing and validation.** Parse only a single fenced `evergreen-result` JSON object or a single whole-output JSON object. Reject arbitrary JSONL scanning. Resolve paths against `repo.resolve()`, reject escapes, open citations at `HEAD`, and confirm the quoted documentation claim appears on the cited line.
- [ ] **Step 4: Run focused tests** and expect all pass.
- [ ] **Step 5: Commit** with `feat(ci): validate evergreen result envelopes`.

### Task 3: Renderer with explicit inconclusive state

**Files:**
- Modify: `ci/pr_comment.py`
- Create: `tests/test_pr_comment.py`

**Interfaces:**
- Consumes: a validated result dictionary or validation errors.
- Produces: `render_result(result: dict | None, errors: list[str]) -> str`.
- CLI accepts `--repo`, `--base`, and `--head`; exits `0` for complete results and `2` for inconclusive results.

- [ ] **Step 1: Replace the embedded self-test with failing `unittest` cases** for clean, drift, unverified, inconclusive, Markdown controls, HTML-like text, pipes, newlines, long fields, and invalid citations.
- [ ] **Step 2: Run and confirm old renderer falsely certifies prose-only input.**
- [ ] **Step 3: Implement the validated renderer.** Always lead with `<!-- evergreen-report -->`; never output clean language for validation errors or `status=inconclusive`; bound rendered claim/why/error text; show base/head and count ledger.
- [ ] **Step 4: Run `python3 -m unittest tests.test_pr_comment -v`** and expect all pass.
- [ ] **Step 5: Commit** with `fix(ci): distinguish clean from inconclusive reviews`.

### Task 4: Harden the PR driver and Action contract

**Files:**
- Modify: `ci/evergreen-pr.sh`
- Modify: `action.yml`
- Modify: `.github/workflows/evergreen-pr.yml`
- Create: `tests/action.sh`

**Interfaces:**
- Adds Action input `fail_on_inconclusive`, default `true`.
- Adds environment `EVERGREEN_FAIL_ON_INCONCLUSIVE`.
- Uses `ci/change_manifest.py` and the strict `ci/pr_comment.py` CLI.

- [ ] **Step 1: Create a failing end-to-end shell test** that stubs `claude`, `gh`, and `npm`, builds temporary Git repositories, and covers clean, findings, malformed output, empty output, hostile docs, missing CLI, wrong commits, advisory override, and comment upsert.
- [ ] **Step 2: Run `bash tests/action.sh`** and confirm failures against the old driver.
- [ ] **Step 3: Update the prompt and driver.** Include the exact manifest in `<untrusted_repository_evidence>` delimiters, explicitly forbid obeying repository instructions, require one `evergreen-result` envelope, record resolved CLI/model identity, and render through the validator. Return nonzero only for inconclusive when policy requires it.
- [ ] **Step 4: Pin `@anthropic-ai/claude-code` to the exact currently tested version** in `action.yml`; remove `continue-on-error` from Evergreen's own workflow so the inconclusive policy is testable.
- [ ] **Step 5: Run `bash tests/action.sh` and `bash tests/hooks.sh`** and expect all pass.
- [ ] **Step 6: Commit** with `fix(ci): harden the PR audit trust boundary`.

### Task 5: Close the compound Git guard bypass

**Files:**
- Modify: `hooks/evergreen-guard.sh`
- Modify: `tests/hooks.sh`

**Interfaces:**
- Preserves current keep-list and `EVERGREEN_GUARD=off` behavior.
- Blocks a single Bash tool call that stages and commits before the finalized index can be inspected.
- Recognizes `git -C`, `git -c`, and ordinary global-option forms.

- [ ] **Step 1: Add failing tests** that execute compound `git add && git commit`, semicolon variants, `git -C`, `git -c`, deletion cleanup, keep-list, and bypass cases.
- [ ] **Step 2: Run `bash tests/hooks.sh`** and confirm the compound cases fail.
- [ ] **Step 3: Implement the smallest safe classifier.** Reject compound stage-and-commit input with instructions to use separate tool calls; normalize supported Git global options before commit intent detection; inspect the finalized staged index on commit-only calls.
- [ ] **Step 4: Run all Wave 1 tests**

```bash
python3 -m unittest discover -s tests -p 'test_*.py' -v
bash tests/action.sh
bash tests/hooks.sh
```

Expected: all pass.

- [ ] **Step 5: Commit** with `fix(hooks): close compound commit guard bypass`.

### Task 6: Wave 1 documentation and verification

**Files:**
- Modify: `README.md`
- Modify: `docs/DESIGN.md`
- Modify: `skills/evergreen/SKILL.md`
- Modify: `skills/evergreen/DIGEST.md`
- Modify: `AGENTS.md`

- [ ] **Step 1: Update product language** from absolute “not a scanner” wording to a local semantic skill with a deterministic trust layer and optional evidence.
- [ ] **Step 2: Document complete/findings/unverified/inconclusive semantics and `fail_on_inconclusive`.**
- [ ] **Step 3: Add token-agreement assertions to `tests/hooks.sh`** for the new trust semantics across Claude and Codex surfaces.
- [ ] **Step 4: Run the full suite and `git diff --check`.**
- [ ] **Step 5: Commit** with `docs: explain trustworthy evergreen CI`.
