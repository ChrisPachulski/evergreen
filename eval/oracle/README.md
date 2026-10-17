# Executable-oracle research

This directory contains the frozen synthetic v1 source-pack contract and pinned candidate source
catalogs. It does not contain a completed five-language corpus, authorized holdout, v2
certification system, or detector-quality result. The package under `eval/bench/public/0.4.0` is a
replayable historical execution record whose metrics are contaminated by canonical-ID label
proxies; the catalogs do not update or supersede it.

## Frozen v1 contract

`schema-v1.json`, `oracle.py`, `build.py`, `split.py`, and `sources/provenance.json` retain the v1
contract. No candidate is admitted to its public source pack, so its checked-in provenance reports
zero sources and zero claims and readiness fails closed. The v1 floor remains 20 source-project
groups and 250 seed claims per language across Python, Java, TypeScript, Rust, and Go. Its missing
owner-only custody package is not inferred from public hashes or plans.

The existing `eval.oracle.build validate-provenance` CLI owns that contract, but this salvage does
not authorize executing it. The static freeze and separate execution authorization in the active
plan come first.

## Pinned source candidates

Candidate recipes and catalogs live under `sources/<language>/`. Python and Go name 20 pinned
projects each, Rust names 20 candidates and includes an earlier exact-span prototype, and Java and
TypeScript contain smaller catalogs. These are real source candidates, not admitted corpus rows or
evidence of detector performance.

## Measurement-first boundary

Before any pilot or detector measurement runs, freeze the exact candidate revision, source
revisions, selected files and assertions, mutation and evaluator bytes, command, environment,
bounds, and output destination. Review those immutable inputs first, then obtain explicit execution
authorization. The first result is a development measurement, not a holdout or certification.

The abandoned v2 custody, runtime, ledger, attestation, category, and A-grade framework was removed
because it preceded the evidence it was meant to protect. Those mechanisms remain deferred until a
measured failure and concrete threat model justify the smallest necessary control.
