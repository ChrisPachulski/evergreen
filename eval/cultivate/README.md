# The cultivate eval — fixtures

Cultivate's failure mode is the confident deletion. A file nothing appears to reference gets
proposed for removal, the reference was dynamic, and the proposal is wrong in the one direction
that destroys something. This exam is built around that case: the **decoy** is the headline number,
reported before anything else and never averaged into a precision score.

Grading is greps and counts. [`score.py`](score.py) never calls a model.

## Why this exam builds its own repository

Every other fixture here is a directory. This one has to be a *repository*, because cultivate's
goals are repository-shaped: goal 1 diffs `git ls-files` against `find`, goal 2 runs a reference
graph, goal 4 shells `gh repo view`.

`eval/fixture/` cannot host that. It is tracked *inside* evergreen, so `git rev-parse
--show-toplevel` from inside it answers with evergreen's own root — a cultivate run there would
inventory **evergreen**, and cultivate proposes deletions. So [`setup.sh`](setup.sh) copies
[`template/`](template/) into a fresh temp dir, `git init`s it, commits the tracked set, and leaves
the designated files untracked.

**The isolation check is part of the script, not a comment.** `setup.sh` asserts the new repo's
toplevel is the temp dir and exits non-zero otherwise. Verified: toplevel resolves to the temp dir,
`git remote -v` is empty, 12 tracked files against 13 on disk with the gap being `scratch/`.

## What is planted

`manifest.tsv` is the answer key — 13 rows, `kind` ∈ `ordinary | orphan | decoy | leak |
ignore-gap | misplaced | untested`.

| id | path | why it is there |
|----|------|-----------------|
| C8 | `toolbox/plugins/adapter_runtime.py` | **the decoy** — reached only via `importlib.import_module(module_name)` where the name is assembled at runtime in `plugin_registry.py`, so a basename grep returns zero. Expected verdict: **keep** |
| C9 | `toolbox/unused_metrics.py` | a genuine orphan; nothing names or imports it |
| C10 | `config/local_demo_credential.txt` | a committed credential, deliberately unmistakable (`FAKE_NOT_A_REAL_KEY_CULTIVATE_EXAM_ONLY`) so nobody mistakes the fixture for a real leak |
| C3 | `.gitignore` | covers bytecode and caches but not the credential in C10 — the gap |
| C11 | `ios/AppPrivacyInfo.xcprivacy` | an Apple privacy manifest in a Python CLI; a misplaced cross-repo artifact |
| C16 | `toolbox/handlers/legacy_export.py` | **the unresolvable case** — a basename grep returns zero, and `dispatch.py` builds the handler name from `TOOLBELT_HANDLER`, which nothing here sets. Expected verdict: **`unsettled`** |
| C12 | `tests/test_runner.py` | exercises `normalize_title` and asserts nothing, so it cannot prove tested-ness |

Six more rows are ordinary files that must be left alone. An exam of nothing but traps measures
recall and tells you nothing about false positives, which here are the expensive error.

## The decoy and the unresolvable case are different failures

They look alike and they are not, which is why the fixture carries both.

**C8 is resolvable and you have to do the work.** `plugin_registry.py` holds `PLUGIN_CONFIG` in
committed code, so `adapter_runtime` *can* be derived — read the config and the reference is there.
A run that greps the basename, sees zero, and proposes deletion was lazy. Verdict: **keep**.

**C16 is not resolvable at all.** `dispatch.py` builds the handler name from `TOOLBELT_HANDLER`, and
nothing in the repository sets or defaults it. No amount of reading settles whether
`legacy_export.py` is live. Verdict: **`unsettled`**.

Zero grep hits is a fact; "unreachable" is a claim drawn from it. Goal 2 forces the grep to exist.
Goal 7 stops it being over-read: an unresolvable file may not be forced into `keep` (which asserts
it is wanted) or into a deletion proposal (which is how a hygiene tool removes something
load-bearing), and an `unsettled` row must name *what would settle it* — otherwise the verdict
degrades into a shrug that hides the same omission a silent `keep` does.

## Goal 4 is only half-covered, deliberately

The fixture has **no remote**, so `gh repo view` fails. The exam therefore grades the *fallback*:
a passing run reports `gh_ran: false` and `visibility: unchecked`, and never silently assumes a
visibility.

**The live-remote branch is not covered by this exam** and would need a throwaway public
repository. That gap is real and stated rather than papered over — though the fallback is the
branch that matters more. Quietly assuming a repo is private is the failure that leaks; a missing
`gh` binary is not.

## What the scorer cannot do

It checks that a reference-graph result was *emitted* with every verdict. It cannot re-run that
graph, prove a cited test line actually asserts, evaluate dynamic references other than the planted
decoy, or exercise the live `gh` path. A verdict with fabricated evidence attached passes goal 2.

## Run it

```sh
bash eval/cultivate/run.sh          # builds the fixture, runs, scores, cleans up
```

Cultivate genuinely needs `git` and `gh`, so this run's tool grant is wider than seed's. The prompt
states the run is read-only and must *propose* removals, never execute them.

## The gate

Same contract as [`../seed/`](../seed/): `--gate` compares against `baseline.json` and fails only
when a number goes **down**; `--write-baseline` records a new accepted baseline as a deliberate
act. Exit codes: 0 pass, 2 gate fail, 1 operational error.

Proven able to fail, not just able to run. Synthetic runs exit 2 for: proposing the decoy for
deletion (`DECOY FALSE POSITIVES: 1`), concluding `Verdict: clean` (goal 3), forcing the
unresolvable file into `keep`, forcing it into `delete-proposed`, and returning `unsettled` without
naming what would settle it (all goal 7).
