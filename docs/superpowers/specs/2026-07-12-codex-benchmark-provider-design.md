# Codex Benchmark Provider Design

## Goal

Run Evergreen's existing five-language trial benchmark through Codex after the Claude Max
interruption, without changing the trial, scoring, datasets, or publication threshold.

## Design

The benchmark selects a provider with `EVAL_PROVIDER`, defaulting to `claude` for compatibility.
One model-call boundary builds and parses either the current Claude command or a non-interactive,
ephemeral, read-only Codex command. All trial stages continue to call that boundary, so their
prompts and decision logic remain identical.

Provenance records the provider, provider-specific CLI version, models, and concurrency. Artifact
filenames include the provider. Exact metadata equality continues to reject resume attempts made
with a different provider, model, judge implementation, repository state, or dataset. Interrupted
Claude artifacts remain quarantined and are never resumed as Codex work.

Codex receives a strict JSON Schema wrapper for the common response envelope and the existing
prompt's stricter stage-specific requirements remain enforced by Evergreen. Provider failures,
malformed output, timeouts, tool events, and output-limit violations remain explicit abstentions.

## Models and execution

The Codex run uses `gpt-5.6-sol` for both trial roles. Runs start fresh across Python, Java,
TypeScript, Rust, and Go with `EVAL_CONCURRENCY=4`. Language lanes may be serialized when local
scratch-space pressure requires it; row-level concurrency and all artifact settings remain fixed.

## Publication gate

No partial results are published. `results-current.md` and README metrics are generated only when
all five language artifacts pass the predeclared per-language coverage threshold of `0.99`.
Published prose names Codex, its CLI version, exact model identifiers, commit, and tree.

## Verification

Tests first prove command isolation, provider validation, Codex response parsing, provider-specific
provenance, filenames, and mixed-provider resume rejection. Then the full local test suite and
benchmark self-test must pass before the smoke test and full run.
