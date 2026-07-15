# Executable-oracle research

This directory contains the frozen synthetic v1 source-pack contract, pinned candidate source
catalogs, and one experimental Python in-project mutation pilot. It does not contain a completed
five-language corpus, authorized holdout, v2 certification system, or detector-quality result.
Current published quality evidence lives under `eval/bench/public/0.4.0`; the pilot and catalogs do
not update or supersede it.

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

## Python pilot

`python_pilot.py` is a standard-library Python experiment, with Git as an external executable, for
one narrow pattern: a selected unittest asserts an integer returned by one production function. It
is designed to check pristine, semantic-no-op, and mutant variants twice and record the original
assertion and source byte spans. It awards no grade and is not called by the product or benchmark
runner.

The current prototype expects the selected test to be named `ValueTests.test_value`. That limitation
must be removed or frozen explicitly before using it on a development corpus. The report contains
only identities derived from the exact Git tree and bound source/assertion bytes; it accepts no
caller-supplied corpus, derivation, or grade identity.

Its offline dependency scan currently supports flat local Python modules, not general package-root
discovery. Broader package support belongs only in response to a selected frozen fixture.

The pilot executes project Python as the current user. Its bounded environment improves
reproducibility but is not a security sandbox; use only explicitly trusted repositories. The
integration test is preserved in `tests/test_oracle_python_pilot.py`, but salvage did not execute
it.

## Measurement-first boundary

Before any pilot or detector measurement runs, freeze the exact candidate revision, source
revisions, selected files and assertions, mutation and evaluator bytes, command, environment,
bounds, and output destination. Review those immutable inputs first, then obtain explicit execution
authorization. The first result is a development measurement, not a holdout or certification.

The abandoned v2 custody, runtime, ledger, attestation, category, and A-grade framework was removed
because it preceded the evidence it was meant to protect. Those mechanisms remain deferred until a
measured failure and concrete threat model justify the smallest necessary control.
