# Benchmark results

Scored deterministically from committed run transcripts (`out/*.json`) with no manual credit —
`python3 run_bench.py --rescore out/<file>.json` re-derives every number here without API calls.

**Protocol.** Real doc-drift is rare (~8–10% of documented functions in wild corpora), so
balanced 50/50 sets overstate precision by the prevalence gap — CASCADE itself drops 0.88 → 0.39
precision moving balanced → 10/90 ([arXiv:2604.19400](https://arxiv.org/abs/2604.19400)). We
therefore report **precision · recall · F1 at a natural 10/90 split as the headline**, taking
medians over 1000 resamples of the consistent class (CASCADE's method, mirrored so numbers line
up), with the balanced split and flag-rate as secondary lenses.

**Baseline regime.** The peer is **DocPrism**
([arXiv:2511.00215](https://arxiv.org/abs/2511.00215)): 0.62 precision at a 15% flag rate across
Python/TypeScript/C++/Java, zero fine-tuning, LLM-proves-each-finding — the regime evergreen
lives in. Fine-tuned single-language SOTA (CCISolver 89.54 F1, CARL-CCI ~0.88–0.94,
[arXiv:2506.20558](https://arxiv.org/abs/2506.20558)) is trained on cleaned single-language data
and is **out of scope** — a prompt ruleset doesn't play in that regime, and we don't invite the
comparison. Off-the-shelf zero-shot GPT-4 (~0.50 accuracy, high recall / low precision) is the
floor the discipline is supposed to beat.

## 0 · Verification-funnel ablation (what actually raises precision)

evergreen's weakness at natural distribution is precision (over-flagging), not recall. We built a
staged funnel to attack false positives — a **calibrated base bar** (flag only when you can quote
both the doc claim and the contradicting code token, else certify), then three verify stages that
fire only on survivors: **refute** (argue the doc is consistent; drop the flag if that defense
holds), **prove** (synthesize a test of the doc's claim and run it — [`prove.py`](prove.py)), and a
three-pronged **audit** (alternative-reading / falsification / strongest-objection, majority
rules). One full-funnel Opus run per dataset; every stage's verdict is committed, so `--ablate`
reads out each cumulative depth with no re-run:

| depth | CoDocBench (Python) P / R / F1 | CASCADE (Java) P / R / F1 |
|---|---|---|
| single-call base (pre-funnel, §2/§1 below) | 0.54 / 0.78 / 0.64 | 0.30 / 0.33 / 0.32 |
| **base (calibrated bar)** | **0.78 / 0.78 / 0.78** | 0.36 / 0.07 / 0.12 |
| + refute | 0.83 / 0.56 / 0.67 | 0.40 / 0.06 / 0.10 |
| + prove | 0.83 / 0.56 / 0.67 | 0.40 / 0.06 / 0.10 |
| + refute + prove + audit | 0.80 / 0.44 / 0.57 | 0.40 / 0.06 / 0.10 |

**What the ablation settled, honestly:**

- **The calibrated bar is the whole win — and it's one cheap call per pair.** On Python it lifts
  precision 0.54 → 0.78 at flat recall, **clearing DocPrism's 0.62**. That is the entire
  improvement worth shipping.
- **The expensive stages don't pay.** refute/prove/audit trade recall away faster than they add
  precision — F1 falls 0.78 → 0.57 on Python and is flat-to-down on Java. The multi-call funnel
  is not worth its cost on either set.
- **The same bar regresses Java.** "Quote both sides" is too strict for Javadoc's subtler drift:
  CASCADE recall collapses 0.33 → 0.07 (F1 0.32 → 0.12). There is **no single config that wins
  both languages** — the bar is a Python win and a Java loss. That is a prompt-calibration finding
  to iterate on cheaply, not a result to brute-force with more full passes.
- **prove barely fires:** it was reached by 10/332 CoDocBench pairs (7 fail, 3 skip) and 12/885
  CASCADE pairs — so language coverage, while now complete (Python + Java both execute; see the
  executor note in Caveats), cannot move the aggregate. The winning config (base-only) executes
  nothing.

Bottom line: **ship the calibrated bar for Python-ish prose; keep the old, looser bar where recall
matters (Java/Javadoc); drop refute/prove/audit as default.** No further full-funnel pass is needed
— these numbers are final via `--ablate`.

## 1 · CASCADE head-to-head (885 wild Java pairs, execution-validated labels)

The first genuinely comparable number: same data, same splits, same metrics as a published
tool. [CASCADE's released dataset](https://github.com/TobiasKiecker/CASCADE) (MIT), converted by
`cascade_to_jsonl.py`; labels come from developers' own Javadoc-fix commits. The release
contains 70 inconsistent / 815 consistent (the paper says 71/814 — a release-artifact
discrepancy we report rather than hide). One run per judge, 2026-07-02, Claude Code CLI 2.1.197.

**Natural 10/90 split** (70 inconsistent + 630 resampled consistent, medians over 1000 resamples;
CASCADE Table 2's imbalanced protocol):

| tool | precision | recall | F1 | specificity | flag-rate |
|---|---|---|---|---|---|
| evergreen · Opus 4.8 | 0.30 | 0.33 | **0.32** | 0.92 | 0.11 |
| evergreen · Haiku 4.5 | 0.22 | **0.49** | 0.30 | 0.81 | 0.22 |
| Cascade (full, their tool) | **0.39** | 0.21 | 0.28 | **0.96** | — |
| their LLM baselines (best per metric) ¹ | 0.06–0.28 | 0.10–0.81 | 0.11–0.28 | — | — |

**Balanced 50/50 split** (70+70, medians over 1000 resamples):

| tool | precision | recall | F1 | specificity |
|---|---|---|---|---|
| evergreen · Opus 4.8 | 0.79 | 0.33 | **0.46** | 0.91 |
| evergreen · Haiku 4.5 | 0.72 | **0.49** | 0.58 ² | 0.81 |
| Cascade (full) | **0.88** | 0.21 | 0.35 | **0.97** |

Reading it honestly: evergreen posts the highest F1 on the 10/90 table with either judge (Opus
0.32, Haiku 0.30, Cascade-full 0.28, best LLM baseline 0.28), but Cascade keeps the precision
crown (0.39 vs 0.30) — its per-finding test execution buys fewer false alarms at the cost of
recall. Opus is the better-calibrated judge (0.92 specificity, 0.11 flag-rate — the same regime
as DocPrism's 15%); Haiku trades precision for recall. Cascade generates and executes unit
tests per finding; evergreen is a prompt ruleset. Domain-transfer caveat: CASCADE is
Java/Javadoc; evergreen's ruleset was written against Python/prose examples.

¹ Table 2 of arXiv:2604.19400, rows LLM-S/LLM-A/Voting/DocChecker/C4RLLaMA at the 10/90 split.
² Haiku's balanced F1 exceeds Opus's because balanced splits reward its high recall and forgive
its false positives — exactly the distortion the natural split exists to correct.

## 2 · CoDocBench-derived wild Python set (n=332, label-validated)

No downloadable labeled multi-language *doc*-drift corpus exists, so we mined one from
[CoDocBench](https://github.com/kunpai/codocbench) (arXiv:2502.00519): 400 candidates derived
from real coupled code+docstring changes in top PyPI projects — `(old docstring, new code)` =
lagging-doc positive candidate, `(new docstring, new code)` = control from disjoint rows —
then **every label validated by a three-LLM majority vote** (Fable 5, Opus 4.8, Sonnet 5;
two-thirds keep rule, CCIBench's method; neutral prompt, not evergreen's ruleset).

**The validation itself is a finding: 78% of heuristic positives were rejected** (9/40 kept vs
323/360 controls) — the "a doc changed alongside code, so the old doc must be inconsistent"
heuristic is mostly noise, worse than CCISolver's measured 45.67% on JITDATA. Kept set: 332
pairs, natural prevalence 2.7%. Inter-annotator agreement: Fleiss' kappa **0.660**, pairwise
Cohen's kappa 0.633–0.688 — *below* the >0.8 target and reported anyway. Annotator setup,
honestly: three LLMs, no human pass, and all three are Claude-family — correlated errors are
possible, and the judge below is same-family (circularity caveat). Votes are committed
(`out/codocbench-validated.votes.json`) for audit.

Scored at the protocol's 10/90 (9 inconsistent + 81 resampled consistent, medians over 1000
resamples) and balanced splits:

| judge | split | precision | recall | F1 | specificity | flag-rate |
|---|---|---|---|---|---|---|
| Opus 4.8 | **natural 10/90** | 0.54 | 0.78 | **0.64** | 0.93 | 0.14 |
| Opus 4.8 | balanced 50/50 | 0.88 | 0.78 | 0.82 | 0.89 | 0.44 |
| Haiku 4.5 | **natural 10/90** | 0.23 | **1.00** | 0.37 | 0.62 | 0.44 |
| Haiku 4.5 | balanced 50/50 | 0.75 | 1.00 | 0.86 | 0.67 | 0.67 |

The Opus row is the closest thing to a DocPrism comparison this side of their dataset
releasing: wild Python, natural split, and a 0.14 flag-rate against their 0.15 — **0.54
precision vs their published 0.62**. Slightly below, honestly reported (different data, so
context rather than a head-to-head; DocPrism also spans four languages). Haiku catches every
validated lagging-doc pair but flags 38% of validated-consistent wild Python (124/323) — the
same trigger-happy pattern as its fixture false positive and its CASCADE precision.
Small-positive-n caveat: 9 positives make recall coarse (each Opus miss is 11 points; it
missed 2).

## 3 · Sanity fixture (n=12 core, author-written — NOT a comparable result)

`dataset.jsonl`: 14 hand-labeled pairs (12 core + 2 under-promise). Author-written and balanced
by construction, so it proves the harness and catches regressions; it compares to nothing.

| judge | split | precision | recall | F1 | specificity | flag-rate |
|---|---|---|---|---|---|---|
| Opus 4.8 | **natural 10/90** ¹ | 1.00 | 1.00 | 1.00 | 1.00 | 0.10 |
| Opus 4.8 | balanced 50/50 | 1.00 | 1.00 | 1.00 | 1.00 | 0.50 |
| Haiku 4.5 | **natural 10/90** ¹ | **0.40** | 1.00 | 0.57 | 0.83 | 0.25 |
| Haiku 4.5 | balanced 50/50 | 0.86 | 1.00 | 0.92 | 0.83 | 0.58 |

¹ Only 6 consistent pairs exist, so the 10/90 split bootstraps them **with** replacement to 54.
Haiku's one false positive is the whole story: harmless at 50/50 (0.86), it collapses precision
to 0.40 at natural prevalence. That is why balanced numbers must not be headlines.

Footnote for the record: this fixture's Opus row was previously reported as a headline
"1.00 precision / 1.00 recall." **Balanced sanity fixture, n=12, author-written — not a
comparable result.** Zero false positives across 6 consistent pairs bounds the fixture FPR; it
does not claim 1.00 on a natural corpus — sections 1–2 above show what the same harness does on
wild data.

## Under-promise — the deliberate asymmetry (n=2, fixture only)

**Both models flagged 0/2 — as designed.** `greet_extra` (an undocumented optional param) and
`read_config_expand` (undocumented `~` expansion) are cases where the code does *more* than the
doc says. DocPrism labels these `inconsistent`; evergreen holds "code is truth, the doc is the
claim," so undocumented extra behavior is informational, not drift. The scorer reports this
separately from recall rather than dragging it down.

## Honest caveats

- **One run per judge per dataset.** Re-run with `EVAL_MODEL=` to see the spread. All numbers
  re-derivable from committed transcripts via `--rescore`, no API spend.
- **Executor coverage (prove stage).** [`prove.py`](prove.py) runs a synthesized test in one
  generic runner + a per-language table; 14 languages execute where their toolchain is installed
  (bash, c, cpp, go, java, javascript, lua, perl, python, r, ruby, rust, swift, typescript).
  Both dataset languages — Python and Java — execute. Java needed a local JDK; before it was
  installed the 9 CASCADE pairs that reached prove logged `skip:no-executor` and fell through to
  audit (visible in the committed transcript). This does not change any number: prove is reached
  by ≤12 pairs per set and the ablation (§0) shows the stage doesn't move the aggregate — the
  winning config (base-only) executes nothing. A language whose interpreter can't separate a
  parse error from an assertion failure is deliberately left as audit-fallback, never given an
  unsafe row that could manufacture drift.
- **CASCADE is Java; the CoDocBench-derived set is Python.** Neither is multi-language;
  evergreen's README/prose territory is broader than both. Under-promise the generality.
- **CoDocBench-derived labels are LLM-validated, not human-validated**, kappa 0.660 < 0.8, and
  annotators/judges share a model family. A human-validated few-hundred subset is the next rung.
- **DocPrism's own set is still unrunnable** (`anonymous.4open.science/r/DocPrism-5746` returns
  `{"error":"not_connected"}`, re-checked 2026-07-02); its 0.62 is context from the paper, not a
  same-data head-to-head. The harness reads its schema: `run_bench.py --dataset <path>` the day
  it releases.
- **All dataset links verified live 2026-07-02**: CASCADE repo, CoDocBench repo + Zenodo DOI,
  CodeFuse-CommitEval, JITDATA (do not use raw — 45.67% positive-label noise), and all arXiv
  IDs cited here.
