# GOAL: comparable benchmark numbers (kill the vanity 1.00)

**For:** a future Fable 5 session picking this up cold.
**Status:** ✅ COMPLETED — the frozen 0.4.0 publication shipped gate-cleared five-language
numbers; see [`eval/bench/results-0.4.0.md`](eval/bench/results-0.4.0.md). Kept as the original
working plan; every figure below is a pre-gate planning estimate, not a current claim.
**Owner:** unassigned. **Written:** 2026-07-02.

## The problem

Evergreen currently reports **1.00 precision / 1.00 recall** on `eval/bench/` (n=12 core,
hand-authored, balanced Python pairs). That number is a **vanity metric** and must not be the
headline. Two independent, quantified reasons from the literature:

- **Balanced-set inflation.** Real doc-drift is rare (~8% of functions in CASCADE's natural corpus).
  Measuring on a 50/50 set overstates precision by exactly the prevalence gap. CASCADE shows this
  *within one tool*: precision **0.88 (balanced) → 0.39 (10/90 natural)** with recall/specificity
  unchanged ([arXiv:2604.19400](https://arxiv.org/abs/2604.19400)). The JIT vuln-prediction
  literature shows the same collapse at scale: average PR-AUC **0.805 → 0.016** moving balanced→natural
  ([arXiv:2507.10729](https://arxiv.org/pdf/2507.10729)).
- **Author-written, tiny, unambiguous.** 14 pairs we wrote ourselves to be clearly right/wrong is an
  exam we set for ourselves. Nobody else reports on it, so it compares to nothing.

**The bar to clear:** a number on a **wild-mined, label-validated corpus**, reported at **natural
(imbalanced) class distribution**, next to a **published baseline**, using a **protocol that matches
a real paper's** so it's apples-to-apples.

## The target to be comparable to (pick the RIGHT baseline)

- **Correct peer:** DocPrism — **0.62 precision @ 15% flag rate**, across Python/TS/C++/Java, **no
  fine-tuning** ([arXiv:2511.00215](https://arxiv.org/abs/2511.00215)). This is the multi-language,
  zero/few-shot, LLM-prove-each-finding regime evergreen lives in. **Beat or match 0.62 at natural
  distribution and the claim is real.**
- **NOT our target:** fine-tuned SOTA at **F1 0.88–0.94** (CCISolver 89.54%, CARL-CCI). That's a
  fine-tuned, single-language, trained-on-cleaned-data regime a prompt skill doesn't play in. State
  this explicitly and out of scope — don't invite the comparison, don't pretend to lose it.
- Off-the-shelf zero-shot GPT-4 sits at ~0.50 accuracy (high recall, low precision) — the floor
  evergreen's discipline is supposed to beat.

## Datasets that are DOWNLOADABLE TODAY (all verified HTTP 200 on 2026-07-02)

Do **not** wait on DocPrism's dataset — its 4open.science artifact is expired (`repository_expired`).
Use these instead:

| dataset | link | size / lang | labels | use for |
|---|---|---|---|---|
| **CASCADE** ⭐ | [github.com/TobiasKiecker/CASCADE](https://github.com/TobiasKiecker/CASCADE) (MIT) | 885 Java pairs (71 inconsistent + 814 consistent) | **execution-validated** developer Javadoc-fix commits; ships each inconsistent pair's *corrected* version | the head-to-head — real labels, published protocol to match |
| **CoDocBench** ⭐ | [github.com/kunpai/codocbench](https://github.com/kunpai/codocbench) · [Zenodo 10.5281/zenodo.14251622](https://doi.org/10.5281/zenodo.14251622) | 4,573 Python coupled code+docstring change commits (top-200 projects) | **none** — all positive coupled changes | mine a *natural, multi-file* Python set; derive drift pairs (see Phase 2) |
| CodeFuse-CommitEval | [github.com/codefuse-ai/CodeFuse-CommitEval](https://github.com/codefuse-ai/CodeFuse-CommitEval) · [figshare](https://figshare.com/s/21fe4ec9cb960b52bffe) | 52,127 balanced pairs, 50 repos | LLM-mutation + 2-fold Claude validation (98.3% correct) | adjacent (commit-msg↔code), a construction template — not core |
| JITDATA | [github.com/panthap2/deep-jit-inconsistency-detection](https://github.com/panthap2/deep-jit-inconsistency-detection) (Google Drive, live) | 40,688 Java comment/code pairs | heuristic commit-mined — **~46% of positives mislabeled** | **DO NOT use raw.** Only via the cleaned derivative below, or with your own re-validation |

⚠️ **The label-noise trap is real and quantified:** CCISolver hand-annotated 600 JITDATA positives
and found **45.67% mislabeled** ([arXiv:2506.20558](https://arxiv.org/abs/2506.20558)). Any corpus
built by "a comment changed when code changed → inconsistent" inherits ~half-noise positives. Never
trust heuristic labels without validation.

## The plan (in priority order)

### Phase 0 — Fix the *protocol* first (cheap, no new data, do this today)
Change `eval/bench/run_bench.py` + `RESULTS.md` to:
1. Report at **both** a balanced split **and** a natural ~10/90 split, taking **medians over ≥1000
   resamples** of the consistent class (CASCADE's method — mirror it so numbers line up).
2. Report **precision · recall · F1** at natural distribution as the headline, with
   precision-at-flag-rate as *one* secondary lens (the research refuted 0-3 that flag-rate is the
   universal metric — don't make it the sole headline).
3. Add a one-paragraph "baseline regime" note: DocPrism 0.62 is the peer; fine-tuned F1 0.88–0.94 is
   out of scope and why.
4. **Demote the 1.00/1.00** to a footnote labeled "balanced sanity fixture, n=12, author-written —
   not a comparable result."

### Phase 1 — Run the head-to-head on CASCADE (highest value)
1. `git clone` CASCADE; unzip `PaperEvaluation/dataset.zip`. Schema: Java method + Javadoc, label
   consistent/inconsistent, plus the corrected version for inconsistent pairs.
2. Extend `run_bench.py` with a CASCADE adapter (map its fields → the existing
   `{code, doc, label}` schema; `--dataset` already exists as the seam).
3. Reproduce **CASCADE's Table 2 protocol**: balanced 71/71 **and** imbalanced 71/639 (exactly
   10/90), medians over 1000 resamples. Report evergreen's precision/recall/specificity next to
   CASCADE's own (0.88→0.39 precision, ~0.21 recall, ~0.97 specificity).
4. This yields the first genuinely comparable number: same data, same splits, same metrics, a
   published tool to sit beside. Java/Javadoc is a domain-transfer caveat — state it.

### Phase 2 — Build a natural, evergreen-native set (fills the real gap: multi-language *doc* drift)
CASCADE is Java+execution; evergreen's territory is broader (READMEs, docstrings, any language).
No downloadable labeled corpus covers that — so mine one:
1. Start from **CoDocBench** (4,573 real Python coupled changes). It has no drift labels, so
   **derive** them: for each coupled change, `(old docstring, new code)` = a natural *inconsistent*
   pair (the doc that lagged), `(new docstring, new code)` = a *consistent* control. This produces
   wild, not-author-written pairs.
2. Or replicate CoDocBench's recipe directly (PyDriller commit iteration + Tree-sitter/AST detection
   of functions whose doc **and** code changed in one commit) to surface **doc-only-fix-after-code**
   commits = genuine natural drift, then its fix as the corrected version.
3. **Validate labels** (mandatory — this is what makes it credible):
   - Pre-filter with **three-LLM majority vote** (e.g. Fable 5 + two others, two-thirds keep rule) —
     CCIBench's method.
   - Then **human-verify a few-hundred subset**; report **Cohen's Kappa** (target >0.8; CCISolver hit
     0.95 on a 300-case set). Document your actual annotator setup honestly.
   - Minimum credible n: **a few hundred validated pairs** at natural prevalence.

### Phase 3 — Publish honestly
A minimal-but-credible 2026 result = **provenance + n + both splits + baseline + caveats**:
- Where the pairs came from (wild-mined, which projects, which recipe).
- n, and the natural class balance.
- Precision/recall/F1 at natural distribution vs DocPrism 0.62.
- Explicit small-n, single-language, and domain caveats. Under-promise the generality.

## Definition of done

- [x] `eval/bench/` reports at natural distribution with resampling; 1.00/1.00 demoted to a labeled fixture footnote. (2026-07-02)
- [x] At least one number on **CASCADE** (real, execution-labeled, downloadable) reproducing its balanced+10/90 protocol. (Opus 4.8: F1 0.32 at 10/90 vs Cascade 0.28; Haiku 0.30. Released set is 70/815 vs paper's 71/814 — noted.)
- [x] A natural evergreen-native set of **≥200 label-validated pairs** (Kappa reported), scored at natural distribution. (n=332 from CoDocBench, Fleiss' kappa 0.660 — below the 0.8 target, reported honestly with the LLM-only-annotator caveat. Opus: 0.54 precision @ 0.14 flag-rate, F1 0.64 at 10/90.)
- [x] `RESULTS.md` states the DocPrism-0.62 baseline as the peer and the fine-tuned band as out of scope.
- [x] Every dataset link in the writeup re-verified live at publish time. (All HTTP 200 on 2026-07-02; DocPrism's 4open.science artifact confirmed still dead — `not_connected`.)

## Open questions (resolve while executing)

1. No downloadable, naturally-mined, **multi-language code↔documentation** (not comment) drift set
   *with* labels exists — Phase 2 must build it. Confirm nothing newer shipped before investing.
2. Smallest n where evergreen's natural-distribution precision is statistically distinguishable from
   0.62 — and whether a solo author can hand-validate that many at Kappa >0.8.
3. Will DocPrism (124 pandas/Requests) and CASCADE's *official* set become downloadable
   post-acceptance, enabling an identical head-to-head? If DocPrism's releases, running its exact
   124 pairs is the cleanest possible comparison — worth a periodic re-check of the 4open.science /
   arXiv links.

## Sources

CASCADE [arXiv:2604.19400](https://arxiv.org/abs/2604.19400) · DocPrism [arXiv:2511.00215](https://arxiv.org/abs/2511.00215) · CCISolver/CCIBench + JITDATA-noise [arXiv:2506.20558](https://arxiv.org/abs/2506.20558) · CoDocBench [arXiv:2502.00519](https://arxiv.org/pdf/2502.00519) · JIT-VP imbalance collapse [arXiv:2507.10729](https://arxiv.org/pdf/2507.10729) · CodeFuse-CommitEval [arXiv:2511.19875](https://arxiv.org/pdf/2511.19875) · JITDATA [Panthaplackel et al. AAAI 2021](https://github.com/panthap2/deep-jit-inconsistency-detection) · DocChecker JIT mirror [github.com/FSoft-AI4Code/DocChecker](https://github.com/FSoft-AI4Code/DocChecker)
