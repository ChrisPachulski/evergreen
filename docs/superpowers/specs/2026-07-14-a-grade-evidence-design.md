# Archived proposal: evidence-earned A certification

> **Status — superseded on 2026-07-14.** This document records a design exploration, not an
> implemented system, approved benchmark, release gate, or instruction to execute its commands.
> Evergreen must measure the current detector on immutable declared inputs before deciding whether
> any certification layer or numerical grade threshold is justified.

## Why this proposal was stopped

The proposal tried to design the complete custody, split, runtime, ledger, attestation, peer, and
grading system before establishing how the detector would perform on the proposed corpus. That
inverted the evidence order: thousands of lines were being built before the result they would
certify was known. The required external attestation authority and Linux OCI/seccomp execution
boundary had also not been established.

No five-language v2 corpus was admitted, no holdout was authorized, no v2 benchmark was completed,
and none of the proposed per-language or per-claim-class A thresholds was earned. Schemas and plans
were never evidence of those events.

## What remains valuable

The central executable-oracle idea remains a promising way to reduce dependence on subjective
labels. An authentic project can supply a mechanically checkable relation:

1. the pristine project compiles and its original selected test passes;
2. a semantic no-op preserves the same result;
3. one bounded production mutation still compiles and makes that same assertion fail.

The experimental Python pilot is designed to exercise that narrow relation for one integer-return
pattern. It does not establish corpus capacity, detector quality, external validity, safety for
untrusted code, or a grade. The pinned source catalogs remain candidate inputs; they are not
admitted examples.

## Direction recorded at archival

Work proceeds measurement first:

1. Define a small development protocol around authentic in-project assertions and a finite set of
   mutation patterns.
2. Before executing it, freeze the exact candidate commit/tree, source commits/trees, selected
   paths and assertions, mutation implementation, evaluator bytes, expected output schema, and
   result destination.
3. Run only after a static review confirms those identities are immutable and the execution scope
   is safe for the chosen trusted repositories.
4. Measure the current detector without an A threshold or best-in-class claim.
5. Use the result to decide whether detector improvement, corpus redesign, or a later minimal
   integrity layer is warranted.

Holdout secrecy is not claimed until a real custodian and authorization boundary exists. Until
then, development measurements must be called development measurements.

## Claim boundary

- The immutable `0.4.0` public benchmark remains the only published evaluated-release evidence.
- Resolver v2 and the executable-oracle pilot have no published quality result.
- No A or best-in-class claim follows from this proposal.
- Human review can test external validity, but neither human nor model identity substitutes for
  reproducible evidence.
- A future certification design must be proportional to demonstrated measurement needs and reuse
  the existing grade, benchmark, receipt, and publication code before adding another framework.

## Proposed future resume criteria

Certification work may resume only after a real development corpus exists, the detector has been
measured on it, the intended claim is written precisely, and a concrete threat model identifies an
integrity failure that existing repository machinery cannot cover. Any future thresholds must be
declared before the evaluation they grade.
