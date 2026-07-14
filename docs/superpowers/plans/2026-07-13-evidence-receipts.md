# Evidence-Backed Completion Receipts Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a deterministic, read-only `evergreen receipt` command and synchronized Claude/Codex rules that prevent unsupported project, benchmark, ship, cleanup, and release-status claims.

**Architecture:** A focused `evergreen.receipt` module derives repository state from bounded, non-mutating local Git argv calls and optionally parses one bounded Evergreen public benchmark manifest into a declaration-only identity. `bin/evergreen` owns argument parsing and rendering. Canonical product, full-skill, digest, and agent surfaces share exact receipt rules guarded by integration tests.

**Tech Stack:** Python 3.10+ standard library, Git CLI, `unittest`, Bash integration checks.

## Global Constraints

- No dependency additions, network calls, provider calls, repository writes, Git mutations, pushes, tags, releases, or deployments.
- Every Git subprocess uses an argv list, no shell, a hardened environment/configuration, one
  five-second deadline, streaming reads capped at one MiB, and process-group termination/reaping.
- Every Git call sets `GIT_NO_LAZY_FETCH=1`; captured-HEAD manifest verification separates the tree
  lookup from a bounded local `cat-file` blob read so unavailable promised objects fail operationally.
- Receipt collection is macOS/Linux-only and fails before POSIX operations elsewhere. Status uses
  one pinned non-split index, temporary synthetic Git metadata outside the repository, and literal
  Git paths, so it never loads repository configuration.
  Effective external clean/process filters, tracked submodules, split indexes, and hidden-index
  flags fail closed; file mode, symlink, and rename visibility are explicitly enabled.
- Missing origin/upstream are data, not errors; missing HEAD is an error.
- Local Git state never proves an external release.
- A benchmark manifest produces `evidence_state: declared_publication`, never a fresh-execution, reverified, or quality-PASS claim.
- Stage and commit in separate tool calls.

---

### Task 1: Repository and benchmark receipt core

**Files:**
- Create: `evergreen/receipt.py`
- Create: `tests/test_receipt.py`

**Interfaces:**
- Produces: `ReceiptError(ValueError)`.
- Produces: `build_receipt(repo: Path, benchmark_manifest: Path | None = None) -> dict`.
- Produces JSON-ready values matching the approved schema; it does not render or print.

- [ ] **Step 1: Write the failing repository-state tests**

Create `tests/test_receipt.py` with a `ReceiptTests` fixture that initializes a temporary repository,
commits one file, creates a bare origin, pushes `main`, and sets upstream. Add tests equivalent to:

```python
def test_clean_synchronized_receipt_is_deterministic(self):
    first = build_receipt(self.repo)
    second = build_receipt(self.repo)
    self.assertEqual(first, second)
    self.assertEqual(first["schema_version"], 1)
    self.assertEqual(first["repository"]["branch"], "main")
    self.assertEqual(first["repository"]["head"], self.git("rev-parse", "HEAD"))
    self.assertEqual(first["repository"]["upstream"], "origin/main")
    self.assertEqual((first["repository"]["ahead"], first["repository"]["behind"]), (0, 0))
    self.assertEqual(
        {key: first["repository"][key] for key in ("staged", "unstaged", "untracked")},
        {"staged": 0, "unstaged": 0, "untracked": 0},
    )
    self.assertTrue(first["repository"]["clean"])
    self.assertEqual(first["release"], {"external_state": "unverified", "local_tags": []})
    self.assertIsNone(first["benchmark"])

def test_counts_staged_unstaged_and_untracked_without_counting_ignored(self):
    (self.repo / ".gitignore").write_text("ignored\n")
    (self.repo / "staged").write_text("staged\n")
    self.git("add", ".gitignore", "staged")
    (self.repo / "tracked").write_text("changed\n")
    (self.repo / "untracked").write_text("new\n")
    (self.repo / "ignored").write_text("ignored\n")
    receipt = build_receipt(self.repo)
    self.assertEqual((receipt["repository"]["staged"], receipt["repository"]["unstaged"], receipt["repository"]["untracked"]), (2, 1, 1))
    self.assertFalse(receipt["repository"]["clean"])
```

Also add explicit tests for detached HEAD, no origin, no upstream, ahead/behind, sorted tags pointing
at HEAD, a path inside the worktree, non-repository input, missing HEAD, credential redaction for
HTTP and SCP-like remotes, timeout/output-limit errors through a stub Git executable seam, and
byte-for-byte snapshots of HEAD, index, refs, and worktree before/after receipt creation. Cover
canonical project names derived from HTTP and SCP-like origins in linked-worktree-style directories,
plus the repository-root directory fallback when origin is missing or unusable.

- [ ] **Step 2: Run the focused tests and verify RED**

Run:

```sh
python3 -m unittest tests.test_receipt
```

Expected: import failure because `evergreen.receipt` does not exist.

- [ ] **Step 3: Implement the minimal bounded Git reader**

Create `evergreen/receipt.py` with these constants and public interface:

```python
GIT_TIMEOUT_SECONDS = 5
MAX_GIT_OUTPUT_BYTES = 1_048_576
MAX_MANIFEST_BYTES = 1_048_576
PUBLICATION_KIND = "evergreen-benchmark-decision-publication"
RECEIPT_ATTEMPTS = 2

class ReceiptError(ValueError):
    pass

class ReceiptOperationalError(ReceiptError):
    pass

def build_receipt(repo: Path, benchmark_manifest: Path | None = None) -> dict:
    ...
```

Implement `_git` with `subprocess.Popen`, an argv list beginning with the resolved Git executable,
`--no-optional-locks`, `--no-replace-objects`, hardened `-c` overrides, and `-C`. Use a minimal
environment, read incrementally to at most one MiB plus one byte under one total deadline, and
terminate/reap the process group on timeout or overflow before UTF-8 decoding. Allow only the
origin and symbolic-branch lookups to return “missing” without raising. Redact URL userinfo and the
user part of SCP-like remotes; fail closed for helpers and unsupported schemes.

Pin the current non-split index through an inherited read-only descriptor, then parse `git status
--porcelain=v2 -z --untracked-files=all` with that index and temporary synthetic Git metadata
outside the repository, literal pathspec handling, and an explicit rename policy. Read symbolic
branch, upstream, and ahead/behind identity separately through bounded Git commands.
Count one staged entry when
the X status is not `.`, one unstaged entry when Y is not `.`, and one untracked entry for each `?`
record. Correctly consume the second NUL path for `2` rename/copy records. Use branch headers for
HEAD, upstream, and `+ahead -behind`; use `symbolic-ref` to distinguish detached HEAD from a legal
branch named `(detached)`. Refuse tracked submodules, split indexes, assume-unchanged/skip-worktree entries, and
effective clean/process filters (including config includes), and force deterministic rename limits
plus file-mode and symlink visibility. Query tags against the captured commit and return only after two complete
snapshots match. When a benchmark manifest is supplied, bracket those snapshots with two identity
reads and require them to match too, retrying once before a bounded operational failure.

- [ ] **Step 4: Add benchmark-manifest tests and verify RED**

Add a valid five-language fixture using the exact public-manifest shape and regular in-repository
artifact, dataset, and report files. Assert:

```python
self.assertEqual(receipt["benchmark"], {
    "artifact_count": 5,
    "evaluated_release": "0.4.0",
    "evidence_state": "declared_publication",
    "judge_sha256": "e" * 64,
    "languages": ["Java", "Python", "go", "rust", "typescript"],
    "manifest": "bench/manifest.json",
    "protocol": "unverified",
    "provenance_commit": "a" * 40,
    "provider": "codex",
    "report": "bench/report.md",
    "resolver": "unverified",
})
```

Add table-driven rejection tests for malformed UTF-8/JSON, oversized manifest, absolute and `..`
paths, outside-root paths, symlinks at the file or parent level, non-regular files, wrong kind,
wrong schema, missing release/provider/report/commit, non-hex or short commit, duplicate languages,
duplicate artifact languages, mismatched required/artifact language sets, and unsafe artifact or
dataset paths.

Run the focused tests. Expected: the new benchmark cases fail because `_benchmark_identity` is
missing or incomplete.

- [ ] **Step 5: Implement strict declaration-only benchmark identity**

Implement descriptor-relative no-follow traversal to reject symlinks in every repository-relative
component, require a regular file, and enforce the byte ceiling from the same opened descriptor
used for the manifest read. Implement `_normalized_path` with `PurePosixPath`, rejecting
absolute, empty, `.`, `..`, backslash, repeated-slash, and non-canonical paths.

Require the normalized manifest path and exact bytes to match the captured HEAD blob. An untracked,
staged-only, or dirty working-tree manifest is not a declared publication.

Parse only schema version `1` and `PUBLICATION_KIND`. Validate the artifact languages exactly match
`publication.required_languages`, every language is a non-empty string and unique, provenance
commit is 40 or 64 lowercase hex characters, judge identity is a lowercase SHA-256, optional
resolver/protocol values are non-empty text, and every referenced artifact, dataset, and report is
a safe regular file. Return only the approved identity fields and `declared_publication` state;
missing resolver/protocol values are explicitly `unverified`.

- [ ] **Step 6: Run focused and neighboring tests**

Run:

```sh
python3 -m unittest tests.test_receipt tests.test_cli tests.test_bench_publication
```

Expected: all pass.

- [ ] **Step 7: Commit the core separately**

Run `git add evergreen/receipt.py tests/test_receipt.py`, inspect `git diff --cached --check` and
the staged diff, then run a separate `git commit -m "feat: add evidence-backed repository receipts"`.

---

### Task 2: Receipt CLI and exact rendering

**Files:**
- Modify: `bin/evergreen`
- Modify: `tests/test_cli.py`

**Interfaces:**
- Consumes: `build_receipt(Path, Path | None) -> dict` and `ReceiptError` from Task 1.
- Produces: `evergreen receipt [--repo PATH] [--benchmark-manifest PATH] [--json]`.
- Produces: `print_receipt(payload: dict) -> None`.

- [ ] **Step 1: Write failing CLI tests**

Extend `test_help_and_usage_exit_codes` to require `receipt` in root help and all three receipt
flags in subcommand help. Add a Git fixture test that runs:

```python
result = self.run_cli("receipt", "--repo", str(git_repo), "--json")
self.assertEqual(result.returncode, 0, result.stderr)
payload = json.loads(result.stdout)
self.assertEqual(payload["repository"]["root"], str(git_repo.resolve()))
self.assertEqual(result.stdout, json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True) + "\n")
```

Add human-output assertions for every repository/release field and benchmark-none state. Add invalid
repository and manifest cases that exit `2`, emit one terminal-safe error line, and do not print a
receipt. Add an import-isolation test proving `receipt` does not import the POSIX host mutation
stack. Add a copied-fresh-plugin test proving receipt creates no bytecode or other files.

- [ ] **Step 2: Run CLI tests and verify RED**

Run `python3 -m unittest tests.test_cli`.

Expected: failures because the parser lacks `receipt`.

- [ ] **Step 3: Implement parser, runner, and renderer**

Add the parser exactly:

```python
receipt = commands.add_parser("receipt", help="emit evidence-backed repository state")
receipt.add_argument("--repo", default=".", help="path inside a Git repository")
receipt.add_argument("--benchmark-manifest", help="Evergreen public benchmark manifest")
receipt.add_argument("--json", action="store_true", help="emit one JSON object")
receipt.set_defaults(run=run_receipt)
```

`run_receipt` lazily imports the receipt module and constructs expanded `Path` values. Invalid or
unsafe repository/evidence input returns `2`; `ReceiptOperationalError` from a bounded Git or
concurrent-state failure returns `1`. Both paths emit one terminal-safe bounded error line. Success
prints compact sorted JSON under `--json` and otherwise calls `print_receipt`.

Human output must use these headings and fields in schema order:

```text
Repository receipt:
- root: ...
- name: ...
- origin: none|...
- branch: detached|...
- HEAD: ...
- upstream: none|...
- ahead/behind: unknown|N/N
- changes: staged=N unstaged=N untracked=N
- clean: true|false
Release evidence:
- local tags at HEAD: none|comma-separated tags
- external state: unverified
Benchmark evidence:
- none
```

When benchmark evidence exists, render every benchmark identity field and explicitly label its
state `declared_publication`.

- [ ] **Step 4: Run CLI and receipt tests**

Run `python3 -m unittest tests.test_cli tests.test_receipt`.

Expected: all pass.

- [ ] **Step 5: Commit the CLI separately**

Run `git add bin/evergreen tests/test_cli.py`, inspect the staged diff, then separately commit with
`git commit -m "feat: expose deterministic completion receipts"`.

---

### Task 3: Synchronized Claude, Codex, product, and test contract

**Files:**
- Modify: `AGENTS.md`
- Modify: `skills/evergreen/SKILL.md`
- Modify: `skills/evergreen/DIGEST.md`
- Modify: `README.md`
- Modify: `docs/DESIGN.md`
- Modify: `tests/hooks.sh`

**Interfaces:**
- Consumes: shipped `./bin/evergreen receipt --repo .` command from Task 2.
- Produces: one exact cross-host operational evidence contract.

- [ ] **Step 1: Add failing host-alignment and documented-command tests**

In `tests/hooks.sh`, add one exact-token loop over README, DESIGN, full skill, digest, and AGENTS for:

```text
Before reporting pushed, merged, clean, complete, released, lost, erased, or not run, obtain fresh evidence.
Never reverse an earlier project, mutation, benchmark, or release-status claim without new evidence.
Treat pushed to a source branch, tagged, GitHub Release published, marketplace published, and deployed as separate states.
Empty cleanup output means nothing was removed.
Stage and commit in separate tool calls.
```

Add `./bin/evergreen receipt --repo .` to the documented shipped-command loop. Execute the command
with `--json` and assert repository, release, and benchmark top-level keys plus
`release.external_state == "unverified"`.

- [ ] **Step 2: Run hooks and verify RED**

Run `bash tests/hooks.sh`.

Expected: failures for missing receipt wording and README command.

- [ ] **Step 3: Update canonical instruction and product surfaces**

Add a compact “Evidence-backed completion receipts” subsection to all five surfaces. Preserve the
exact shared sentences from Step 1, then add the detailed rules from the approved design:

- target lock before external mutation;
- benchmark identity includes release, resolver/judge, provider, languages, commit, and state;
- user contradiction requires receipt/artifact inspection before agreement or defense;
- external release remains unverified without authority;
- empty cleanup output proves no removal;
- staging and commit are separate calls.

Document human and JSON receipt examples in README and the read-only/no-network architecture in
DESIGN. Do not claim the command verifies external publication or fresh provider execution.

- [ ] **Step 4: Run documentation and CLI gates**

Run:

```sh
bash tests/hooks.sh
python3 -m unittest tests.test_cli tests.test_receipt
```

Expected: all pass.

- [ ] **Step 5: Commit policy/docs separately**

Run `git add AGENTS.md skills/evergreen/SKILL.md skills/evergreen/DIGEST.md README.md docs/DESIGN.md tests/hooks.sh`, inspect the staged diff, then separately commit with
`git commit -m "docs: require evidence-backed completion claims"`.

---

### Task 4: Integration, adversarial review, and ship receipt

**Files:**
- Modify only files required by verified review findings.

**Interfaces:**
- Consumes all prior tasks.
- Produces one reviewed, fully verified branch with a clean final receipt.

- [ ] **Step 1: Run focused quality checks**

Run `python3 -m compileall -q evergreen bin/evergreen`, `git diff --check`, and parse every tracked
JSON file with Python. Expected: exit `0` and no output except intentional test summaries.

- [ ] **Step 2: Dispatch independent reviewers in parallel**

Assign three read-only reviewers:

1. security/non-mutation/path validation;
2. CLI/schema/cross-platform correctness;
3. Claude/Codex documentation and status-semantics accuracy.

Require Critical/Important/Minor findings with file and line evidence. Fix every sustained finding
test-first and repeat the affected review until no finding remains.

- [ ] **Step 3: Run the complete local ship matrix**

Run independently:

```sh
python3 -m unittest discover -s tests -p 'test_*.py'
bash tests/hooks.sh
bash tests/action.sh
python3 eval/bench/run_bench.py --selftest
python3 eval/bench/publication.py verify \
  --manifest eval/bench/public/0.4.0/manifest.json \
  --repo . \
  --report eval/bench/results-0.4.0.md
```

Regenerate the frozen v1 report to a temporary path and compare it byte-for-byte with
`eval/bench/results-0.4.0.md`. Expected: every command passes and no tracked file changes.

- [ ] **Step 4: Generate the pre-push receipt**

Run:

```sh
./bin/evergreen receipt --repo . \
  --benchmark-manifest eval/bench/public/0.4.0/manifest.json \
  --json
```

Expected: the feature branch HEAD, intended origin, zero staged/unstaged/untracked changes,
external release `unverified`, and the exact declared 0.4.0 Codex five-language identity.

- [ ] **Step 5: Integrate, push, and monitor only with existing authority**

Merge the isolated feature branch into `main`, push `main`, and monitor the exact pushed SHA until
Ubuntu Python 3.10 compatibility, Ubuntu Python 3.11, and macOS Python 3.11 complete successfully.
Do not create a tag, GitHub Release, marketplace publication, or deployment.

- [ ] **Step 6: Generate and report the final receipt**

Run the receipt again on `main`, compare local HEAD, `origin/main`, and remote `main`, confirm clean
state, and report four separate facts:

- source branch integration;
- CI status;
- frozen benchmark declaration and fresh offline verification;
- external release state `unverified`.

Only then assign final grades.
