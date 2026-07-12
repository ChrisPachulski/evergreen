#!/usr/bin/env python3
"""Compatibility facade and CLI for the Evergreen benchmark."""

try:
    from .metrics import report, rows_from_transcript, score, split_metrics
    from .runner import (
        MAX_DATASET_BYTES, MAX_RESCORE_BYTES, accumulated_usage, artifact_rows,
        bounded_results, eval_concurrency, load_dataset, load_rescore, main,
        provider_usage, selftest,
    )
    from .trial import (
        MAX_MODEL_STDERR_BYTES, MAX_MODEL_STDOUT_BYTES, MAX_PAIR_TEXT_BYTES, PRONGS,
        UNTRUSTED_DATA_INSTRUCTION, UNTRUSTED_PAIR_PREFIX, UNTRUSTED_TRIAL_PREFIX,
        _pair_envelope, blindspot_call, bounded_cli_run, challenge_call, claude_json,
        judge, prong_call, run_prongs, snap_call, synthesis_call,
    )
except ImportError:  # Direct script execution.
    from metrics import report, rows_from_transcript, score, split_metrics
    from runner import (
        MAX_DATASET_BYTES, MAX_RESCORE_BYTES, accumulated_usage, artifact_rows,
        bounded_results, eval_concurrency, load_dataset, load_rescore, main,
        provider_usage, selftest,
    )
    from trial import (
        MAX_MODEL_STDERR_BYTES, MAX_MODEL_STDOUT_BYTES, MAX_PAIR_TEXT_BYTES, PRONGS,
        UNTRUSTED_DATA_INSTRUCTION, UNTRUSTED_PAIR_PREFIX, UNTRUSTED_TRIAL_PREFIX,
        _pair_envelope, blindspot_call, bounded_cli_run, challenge_call, claude_json,
        judge, prong_call, run_prongs, snap_call, synthesis_call,
    )


if __name__ == "__main__":
    raise SystemExit(main())
