# The seed eval — fixtures

Seed's failure mode is the confident write: a doc that restates a signature, or documents something
the prose already covered under another name, and reads like diligence either way. Over-population
is the failure this command exists to avoid, so it is the one the exam scores loudest.

The fixture is [`../fixture/`](../fixture/) — the same seeded `shipit` CLI the winnow eval uses,
extended with five public functions and no new prose. `manifest.tsv` is the answer key; grading is
greps and counts, and [`score.py`](score.py) never calls a model.

## The planted set

Five graded rows, one per verdict seed can reach:

| id | symbol | expected | the trap |
|----|--------|----------|----------|
| S1 | `with_backoff` | `worthy` | retries **only** `ConnectionError`/`TimeoutError` and re-raises the rest, with exponential backoff — none of it in any doc, none of it in the signature |
| S2 | `resolve_setting` | `worthy` | precedence is cli > env > file > default, and the sources disagree about emptiness: an empty **env** value is skipped, an empty **file** value wins |
| S3 | `get_release_name` | `not-worthy` | returns `release["name"]` with a `""` default. A doc here says nothing the declaration doesn't |
| S4 | `ordered_releases` | `not-worthy` | forwards to `sort_releases` unchanged |
| S5 | `render_release` | `documented` | the alias trap — a fixed-string grep for the symbol finds nothing, but `README.md:17-18` ("Output defaults to table") already describes exactly what it does |

The fixture's five pre-existing symbols (`load_config`, `sort_releases`, `format_row`,
`build_parser`, `main`) are carried as `unscored` rows. They were never designed as exam cases, so
inventing labels for them would add noise to the answer key — but they still count toward the
coverage identity, because a run that silently drops half the inventory has failed goal 2 whatever
it says about the other half.

## The shared-fixture rule (read before adding a row)

One fixture, two answer keys. `../manifest.tsv` marks 10 doc sentences as `drift` — planted lies
the winnow exam **requires** to be flagged — and 8 as `decoy`, true by construction.

**A seed row may cite a `decoy` sentence or unclaimed true prose. Never a `drift` row.** S5's
anchor is decoy `C7`. The live landmine is `docs/usage.md:3` ("Row rendering lives in `utils.py`"),
which is planted lie `D2`: `utils.py` does not exist. A seed row resting on it would assert that a
symbol is documented by a sentence the other exam is grading as a lie, and both exams would be
wrong at once.

The same care applies to symbol names. `with_backoff` was originally `retry_call` with
`attempts=3`, which collided with decoys `C1` (`--retries`) and `C6` ("3 attempts") — a run could
defensibly have called it `documented`, making the row contested. The hard goals must be binary,
and a noisy golden label is worse than no label.

## What it grades

The eight goals in [`../../skills/evergreen/hard-goals/seed.md`](../../skills/evergreen/hard-goals/seed.md),
plus classification against the manifest. `score.py`'s docstring states what each gate can and
cannot catch — read it before trusting a number. The honest ceiling: the ledger gate proves a
citation is *present*, never that it is *true*, and the speculation gate matches normalized
substrings, so a paraphrase sharing no tokens with its ledger row walks straight through.

Over-population and under-population are reported as separate counts. They are not equally bad and
averaging them would hide the one that matters.

## Run it

```sh
bash eval/seed/run.sh                                    # default model
EVAL_MODEL=claude-haiku-4-5-20251001 bash eval/seed/run.sh
```

The runner pre-executes `till` and embeds its JSON as the pass-B provider result, so the judged run
needs only `Read,Grep,Glob` — no shell, and one less nondeterministic step. It runs from the
repository root with `eval/fixture` as a scope path, not from inside the fixture: `till` resolves
the repository root and refuses a fixture-relative root.

### What this exam does *not* measure

A read-only grant cannot shell out, so seed's pass-E certification takes its documented fallback
and **the external judge is never exercised** by a default run. That is deliberate — the eight hard
goals are about triage, ledger discipline, and coverage, none of which involve the judge — but it
means a green exam says nothing about whether cross-provider certification works.

```sh
SEED_EXAM_JUDGE=external bash eval/seed/run.sh   # widens the grant to the judge entry point
```

widens the tool grant to exactly the judge entry point and costs one codex call. The judge's own
correctness is proven separately and more cheaply, by pointing `judge_json` at a doc with a known
planted lie and confirming it comes back `drift`; that is a better test of the judge than any seed
run, because seed writes its claims *from* the code and rarely produces a false one to catch.

## The gate

`--gate` compares against `baseline.json` and fails **only when a number goes down**. Absolute
thresholds on a single stochastic run flake, and a gate that flakes is a gate you switch off within
a week. `--write-baseline` records a new accepted baseline — a deliberate act, never automatic. No
baseline yet means `--gate` reports that and exits 0.

This catches a ruleset regression: you edit `SKILL.md` or `commands/seed.md`, recall falls off a
cliff, the exam says so. It does not resolve a five-point drift, and one run is one sample.
