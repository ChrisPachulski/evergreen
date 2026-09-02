# The eval — prove it or drop it, applied to evergreen itself

A skill that demands citations owes you numbers. This harness measures the ruleset against a
fixture repo with **seeded, known drift**: 10 planted lies (at least two per ladder rung), 8
true-claim decoys that must *not* be flagged, and 2 exempt docs (an ADR and a CHANGELOG) that
must be left alone.

## Layout

- `fixture/` — a small Python CLI (`shipit`) whose README and docs lie in specific, catalogued
  ways. The drift is the exam; don't fix it. (The repo's own reflex skips it via
  `.evergreen-ignore`.)
- `manifest.tsv` — the answer key: every seeded case with its kind (`drift | decoy | exempt`),
  doc file, matchable token, and ladder rung.
- `prompt.md` — the task given to the judged model (appended to the shipped SKILL body, so the
  eval always measures the ruleset as it currently is).
- `run.sh` — one measured run: `claude -p` winnows the fixture read-only, output is scored.
- `score.py` — grades a run twice over: the fixture exam (recall over seeded drift, decoy false
  positives, exempt-doc violations, precision, and any unseeded flags for manual audit) and the
  frozen hard goals. Exit 0 only when both pass; 2 on any failure; 1 when nothing in the
  transcript parses. `--allow-misses N` widens the recall bar (default 0, strict).
  `--selftest` grades built-in pass/fail transcripts — one failing control per gate — and needs
  no run at all.

## The ledger the scorer reads

Goals 1, 2, 3 and 5 are counts over the run's own claim ledger, so the run has to emit one. The
trailing `jsonl` block carries it; prose lines are ignored.

```jsonl
{"type":"claims","n":19,"command":"grep -rnE '`[^`]+`' README.md docs/"}
{"type":"verdict","status":"certified","file":"README.md","line":12,"claim":"<doc phrase>","code":"shipit/cli.py:31"}
{"type":"verdict","status":"drift","file":"README.md","line":8,"claim":"<doc phrase>","why":"gone; shipit/cli.py:1"}
{"type":"verdict","status":"unverified","file":"docs/usage.md","line":22,"claim":"<doc phrase>","why":"behavior-asserted"}
{"type":"left_alone","file":"CHANGELOG.md","reason":"history, frozen in time"}
{"type":"judge","status":"ok","provider":"codex","docs":19,"certified":17,"drift":0,"unverified":2}
```

A legacy `{"type":"flag",...}` row still counts as a `drift` verdict, so an older transcript
still scores on the fixture exam — but with no claims row it fails goals 1, 2 and 5, which is
the intended reading of "not done". A run that states the census in prose
(`N = 19 documented claims ...` with the command on a nearby line) is also accepted.

## Which hard goals are enforced

| goal | status | what the check actually proves |
|------|--------|--------------------------------|
| 1 — enumerate the whole claim set | enforced | a single census states `N` and a countable command. The command is not re-run. |
| 2 — a verdict row per claim | enforced | verdict rows == `N`, counted exactly. |
| 3 — a code `path:line` on every certified row | enforced | the citation is present and is not a doc path. It is never opened. |
| 4 — every drift proven gone by grep | **proxied** | a drift row scores only when its claim quotes the planted token from `manifest.tsv`. Re-running each grep needs the fixture tree at the run's commit, which the scorer does not have. |
| 5 — nothing silently dropped | enforced | certified + drift + unverified == `N`, and no fourth status is allowed. |
| 6 — exempt docs stay out of findings | enforced | no exempt path on a certified or drift row. This now fails the run rather than only printing. |
| second provider | enforced | exactly one `judge` row with status `ok` and `docs` ≥ 1, summed from the judge entry point run with `--repo . --doc <doc> --json` over every doc in scope. An absent row, an abstain, or a row that judged no doc fails the run. The judge's drift count is reported, not gated: this fixture seeds drift on purpose, so a judge that finds some is doing its job; zero judge drift is the release gate's rule, for real docs. |

## Run it

```sh
bash eval/run.sh                      # default model
EVAL_MODEL=claude-haiku-4-5-20251001 bash eval/run.sh   # or pin one
```

The harness prints its own numbers on each run.

## How it compares

The per-pair benchmark ([`bench/`](bench/)) reduces each language to one confusion matrix. The
Evergreen 0.4.0 five-language run reached at least 99% coverage per language from one frozen
implementation and provider identity, but canonical IDs leaked label proxies to both the judge and
the pre-fix label screen. Its metrics are therefore contaminated and unverified, not detector-quality
evidence. [`bench/results-0.4.0.md`](bench/results-0.4.0.md) and the checked-in
[public decision package](bench/public/0.4.0/manifest.json) remain replayable historical records.
