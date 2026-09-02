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

The fixture's six pre-existing symbols (`load_config`, `sort_releases`, `format_row`,
`build_parser`, `main`, and the `DEFAULT_TIMEOUT` constant till lists) are carried as `unscored`
rows. They were never designed as exam cases, so
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

All eight goals in [`../../skills/evergreen/hard-goals/seed.md`](../../skills/evergreen/hard-goals/seed.md),
plus classification against the manifest.

| goal | status | what the check actually proves |
|------|--------|--------------------------------|
| 1 — complete, surface-shaped inventory | enforced | one inventory row with `source_files == scanned_files`, `candidates` matching the row count and the manifest; every candidate row carries symbol, kind, a declaration `path:line`, and a rank; no `truncated` warning; every `outside inventory` warning quoted a second time in the report. |
| 2 — walk the ranking, stop only when cold | enforced | ranks are 1..R in emission order with no gap, the inspected rows form a prefix, the remainder is `deferred`, and the walk stops at `N` or after 10 consecutive cold verdicts. |
| 3 — a code `file:line` on every claim | enforced | the citation is present. It is never opened, so it proves presence, not truth. |
| 4 — winnow the seeded output | enforced | one winnow row with `certified` == claim-ledger rows, `drift` == 0, `unverified` == 0. |
| 5 — zero unmarked speculation | enforced | every declarative unit matches a ledger row by normalized substring or sits on a `seed:gap` line. A paraphrase sharing no tokens with its row walks through. |
| 6 — purely additive | **proxied** | proposal paths that already exist under `eval/fixture/` fail. The judged run is read-only, so there is no real diff to read `-` lines from. |
| 7 — the owner sets the write count | enforced | the worthy list and its recommendation appear before any doc content; `documented + worthy + not-worthy + deferred == N`; `written <= chosen <= worthy`; no owner answer means `written == 0`; a pre-diff `documented` candidate may not be written. The owner's count comes from an `owner` row or the `--approve N` invocation argument. |
| 8 — useful, small, repeat-visible | enforced, one part proxied | every written doc needs a `reader-use` claim citing code outside its declaration line, and stays within 60 lines — the scorer bounds each doc, stricter than the goal's aggregate `A ≤ 60 × written`. Gap markers and touched doc paths each stay within `written`, and every marker sits on a written doc. The post-diff grep is **proxied** by a scan of the proposed body outside `seed:gap` lines, since a read-only run writes no file to grep. |
| second provider (inventory) | enforced on every run | one `judge` row with stage `inventory`, status `ok`, drift 0: till's surface judged on a second provider before triage. |
| second provider (docs) | enforced whenever a doc was written | one `judge` row with stage `docs`, status `ok`, drift 0; absent is allowed only when `written == 0`. |

### The rows the goal gates read

Goals 1, 2, 4 and 7 are counts over rows the run emits, so the trailing `jsonl` block carries the rows
the goal gates read:

```jsonl
{"type":"inventory","source_files":4,"scanned_files":4,"candidates":10,"warnings":[]}
{"type":"candidate","symbol":"with_backoff","kind":"function","code_ref":"helpers.py:12","rank":1,"verdict":"worthy","reader_use":"..."}
{"type":"recommendation","worthy":["with_backoff","resolve_setting"],"recommend":2}
{"type":"owner","chosen":2}
{"type":"claim","doc":"<path>","claim":"<sentence>","code_ref":"cli.py:44","kind":"reader-use"}
{"type":"winnow","certified":4,"drift":0,"unverified":0}
{"type":"doc","path":"<path>","symbol":"with_backoff","lines":12,"body":"<full content>"}
{"type":"judge","status":"ok","provider":"codex","docs":2,"certified":2,"drift":0,"unverified":0}
```

`--approve N` supplies the owner's chosen count when
the invocation carries it instead of the transcript.

`score.py --selftest` grades built-in pass/fail transcripts with one failing control per gate, so
a gate that stopped discriminating is caught without a model call. The honest ceiling stands: the
ledger gate proves a citation is *present*, never that it is *true*.

Over-population and under-population are reported as separate counts. They are not equally bad and
averaging them would hide the one that matters.

## Run it

```sh
bash eval/seed/run.sh                                    # default model
EVAL_MODEL=claude-haiku-4-5-20251001 bash eval/seed/run.sh
```

The runner pre-executes `till`, embeds its JSON as the pass-B provider result, and saves it as
`till.json` in the sandbox root for the judge's `--inventory`, so the judged run
needs only `Read,Grep,Glob` plus the judge entry point — no other shell, and one less
nondeterministic step. It runs from the
repository root with `eval/fixture` as a scope path, not from inside the fixture: `till` resolves
the repository root and refuses a fixture-relative root.

### What this exam does *not* measure

The grant is read-only plus exactly one shell command, the judge entry point, because the judge is
mandatory on every run: the inventory row is judged before triage and any written doc after it (one
codex call per inventory batch and per doc), and a host without codex fails the two second-provider gates rather than passing
quietly. The eight hard goals are about triage, ledger discipline, and coverage; the judge gates sit
beside them, and a green exam proves the judge *ran*, not that it is right. The judge's own
correctness is proven separately and more cheaply, by pointing
`bin/evergreen judge --repo PATH --doc PATH` at a doc with a known planted lie and confirming it
comes back `drift` (exit 0 with a verdict, 3 when it abstains or is unavailable, 2 on bad input);
that is a better test of the judge than any seed
run, because seed writes its claims *from* the code and rarely produces a false one to catch.

## The gate

`--gate` compares against `baseline.json` and fails **only when a number goes down**. Absolute
thresholds on a single stochastic run flake, and a gate that flakes is a gate you switch off within
a week. `--write-baseline` records a new accepted baseline — a deliberate act, never automatic. No
baseline yet means `--gate` reports that and exits 0.

This catches a ruleset regression: you edit `SKILL.md` or `commands/seed.md`, recall falls off a
cliff, the exam says so. It does not resolve a five-point drift, and one run is one sample.
