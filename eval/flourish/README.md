# The flourish eval — fixtures

Flourish's failure mode is the beautiful gutting: a rewrite that passes face and voice while
quietly deleting the only copy of true reference material. These fixtures are accurate-but-ugly
READMEs with that trap (and its cousins) planted; `ground-truth.json` is the answer key a scorer
greps against — no model needed to grade.

## Fixtures

- `fixtures/monolith/` — a Python CLI (`logloom`) behind an architecture-first wall of prose.
  Traps: a config-gotchas table whose content lives nowhere else (gut it and you lose data), and
  a `--parallel` flag the code parses but refuses (claim it and you fabricated a feature).
- `fixtures/voiced/` — a Node cron wrapper (`hushcron`), structurally ugly but opening with a
  real hook line. Trap: flattening — replacing the hook with the definitional formula is a
  failed flourish even when the structure lands textbook.

## The fixture contract (`ground-truth.json`, `schema_version: 1`)

- `readme` — fixture-relative path of the doc to flourish.
- `sections[]` — every `##`/`###` heading, verbatim, in source order. `only_home: true` marks
  sole-copy reference material a rewrite must rehome, never delete (hard goal 3).
- `claims[]` — the claim inventory. `backed: true` rows cite real `file:line` evidence.
  `backed: false` rows are TEMPTATIONS: things the source README correctly does *not* claim and
  a rewrite must not invent; their evidence points at the line proving non-implementation.
  A temptation's `text` states the forbidden capability plainly; the human annotation lives in
  `note` (never in `text`, so matching keys on the capability, not on annotation words); and
  `keywords` carries the capability's stems (e.g. `["parallel", "thread", "concurren"]`) — two
  or more landing in a single result sentence/bullet that isn't in the source fires the truth
  gate, so natural phrasings of the temptation are caught too.
- `voice` — whether the source already has a hook (`hook_line`, verbatim). If it does, a rewrite
  that drops it flattened the doc — the don't-flatten guard in `commands/flourish.md`.
- `traps[]` — kinds `only-home-section | near-backable-claim | voiced-source`; each `detail`
  names what a failing flourish does there.

Both source READMEs are ACCURATE. The ugliness is the exam; don't fix it in place — the repo's
reflex skips `eval/flourish/fixtures/*` (its own `.evergreen-ignore` line, same as
`eval/fixture/*`). Grading is hard-goals mechanics (`skills/evergreen/hard-goals/flourish.md`):
heading-set diff vs the ledger, evidence greps, and a five-check face gate (hero and tagline
mandatory per goal 4, ≥4 of 5 overall). The hook check is advisory voice reporting, never
grading — plus body
conservation: a demoted or kept section must carry >= 70% of the source section's unique body
tokens (sections under 10 unique body tokens keep plain heading-match semantics).

## Which hard goals are enforced

`score.py` grades the written result; `--report <path>` additionally grades the run's report and
turns on goals 5, 6, 9, and the mandatory second-provider judge. Without `--report` those four
print `NOT ENFORCED` and leave the exit code alone. In the golden matrix (`tests/readiness.sh`), a
golden with a sibling `<name>.report.md` is graded with them on — `good.report.md` and
`flattened.report.md` exist for exactly that, each carrying a passing `judge:` line.

| goal | status | what the check actually proves |
|------|--------|--------------------------------|
| 1 — conserve every source section | enforced | every ground-truth heading is present, in a `<details>`, in a linked in-fixture file, or on the cuts declaration — and the home carries >= 70% of the source body's unique tokens. |
| 2 — name every cut | enforced | via `--cuts`: a row needs a reason of >= 10 characters that is not "trimmed for length" or a synonym. |
| 3 — never delete sole-copy material | enforced | `only_home` sections accept only a kept section or a `<details>`; a declared cut cannot save them. |
| 4 — keep the face | enforced | hero and tagline are both mandatory, inside a wider 4-of-5 overlay (badges, prose opener, earned visual). |
| 5 — certified / cut / markered counts | enforced with `--report` | the three counts are stated and sum to the stated claim total. Arithmetic only — the scorer cannot tell whether the counts are honest. |
| 6 — show the rewrite | enforced with `--report` | the report holds a ```diff fence (or the full result inlined) **and** a line naming a ledger. Presence only. |
| 7 — quick start within 400 words | enforced | the goal's own awk count, in Python. A result with no Install/Quick start/Getting started/Usage heading is exempt as a pure explainer. |
| 8 — open page <= 2,500 words | enforced | the goal's own snippet: `<details>` blocks stripped, then a word count. |
| 9 — run the repository's own checks | enforced with `--report` | a re-runnable command line and a verdict line after it, or the stated "declares no verification entry point" escape. **Presence only**: a report that prints a `GREEN` it never earned passes here and fails the hard goal in the world. |
| 10 — one vertical mermaid diagram | enforced | the goal's four greps, exactly: a ```mermaid fence, a `graph`/`flowchart` TD/TB/BT declaration, zero LR/RL declarations or `direction LR|RL` lines, zero box-drawing characters. |
| second provider | enforced with `--report` | the report's `judge: <provider> <ok\|abstain> — docs K, certified N, drift D, unverified U` line (or the short `judge: abstain — <reason>` escape) names status `ok` and drift 0. A missing line, an abstain, or drift > 0 fails. |
| voice | advisory | reported (kept / flattened / absent) and never gates, so `flattened.md` still exits 0. |

The `good` and `flattened` goldens carry a vertical mermaid diagram where their ASCII pipeline art
used to be, because goal 10 admits no ASCII alternative.
