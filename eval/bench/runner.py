"""Dataset validation, bounded scheduling, resume, and artifact orchestration."""

from collections import deque
from datetime import datetime, timezone
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
import stat
import sys
import time

try:
    from . import metrics as metrics_module, trial as trial_module
    from .artifact import (
        artifact_document, artifact_metadata, atomic_write_json, load_json, merge_usage,
        read_bytes, resume_state, validate_benchmark_row, validate_input_hashes, validate_usage,
    )
    from .java_context import PROTOCOLS as CONTEXT_PROTOCOLS, validate_context
    from .metrics import report, rows_from_transcript
    from .trial import (
        _validated_pair_data as validate_pair, judge, set_skill_body,
    )
except ImportError:  # Direct script execution.
    import metrics as metrics_module
    import trial as trial_module
    from artifact import (
        artifact_document, artifact_metadata, atomic_write_json, load_json, merge_usage,
        read_bytes, resume_state, validate_benchmark_row, validate_input_hashes, validate_usage,
    )
    from java_context import PROTOCOLS as CONTEXT_PROTOCOLS, validate_context
    from metrics import report, rows_from_transcript
    from trial import (
        _validated_pair_data as validate_pair, judge, set_skill_body,
    )

HERE = Path(__file__).parent
SKILL = HERE.parent.parent / "skills" / "evergreen" / "SKILL.md"
MAX_CONCURRENCY = 32
MAX_RESUME_BYTES = 64 * 1024 * 1024
MAX_PROVIDER_USAGE_BYTES = 64 * 1024
MAX_DATASET_BYTES = 64 * 1024 * 1024
MAX_DATASET_ROWS = 100_000
MAX_SKILL_BYTES = 1024 * 1024
MAX_RESCORE_BYTES = 64 * 1024 * 1024
MAX_RESCORE_ROWS = 100_000
MAX_IDS_BYTES = 1024 * 1024


def selftest():
    metrics_module.selftest()
    trial_module.selftest()
    print("selftest ok")
    return 0


def artifact_rows(value):
    """Read versioned artifacts while retaining legacy transcript compatibility."""
    if isinstance(value, list):
        return value
    if isinstance(value, dict) and isinstance(value.get("rows"), list):
        return value["rows"]
    raise ValueError("benchmark artifact must be a transcript list or contain a rows list")


def eval_concurrency(environment=os.environ):
    raw = environment.get("EVAL_CONCURRENCY", "1")
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError("EVAL_CONCURRENCY must be an integer from 1 to 32") from None
    if not 1 <= value <= MAX_CONCURRENCY:
        raise ValueError("EVAL_CONCURRENCY must be an integer from 1 to 32")
    return value


def eval_provider(environment=os.environ):
    provider = environment.get("EVAL_PROVIDER", "claude")
    if provider not in ("claude", "codex"):
        raise ValueError("EVAL_PROVIDER must be claude or codex")
    return provider


def eval_resolver(environment=os.environ):
    resolver = environment.get("EVAL_RESOLVER", "v1")
    if resolver not in ("v1", "v2", "v3"):
        raise ValueError("EVAL_RESOLVER must be v1, v2, or v3")
    return resolver


def eval_max_provider_attempts(environment=os.environ):
    """Parse the optional hard provider-attempt ceiling frozen_run.py freezes for v3 runs.

    Absent or empty means no ceiling (today's unlimited v1/v2 behavior); a present value must
    be a positive integer. Whether a ceiling is REQUIRED is a resolver-specific policy decision
    left to require_provider_attempt_ceiling, not this parser.
    """
    raw = environment.get("EVAL_MAX_PROVIDER_ATTEMPTS")
    if raw is None or raw == "":
        return None
    try:
        value = int(raw)
    except (TypeError, ValueError):
        raise ValueError("EVAL_MAX_PROVIDER_ATTEMPTS must be a positive integer") from None
    if value <= 0:
        raise ValueError("EVAL_MAX_PROVIDER_ATTEMPTS must be a positive integer")
    return value


def require_provider_attempt_ceiling(resolver, ceiling):
    """Resolver v3 must never launch unbounded: enforce the ceiling before any row runs."""
    if resolver == "v3" and ceiling is None:
        raise ValueError(
            "resolver v3 requires EVAL_MAX_PROVIDER_ATTEMPTS to be a positive integer"
        )


def eval_policy_settings(environment=os.environ):
    resolver = eval_resolver(environment)
    context_protocol = environment.get("EVAL_CONTEXT_PROTOCOL", "none")
    if context_protocol not in ("none", *CONTEXT_PROTOCOLS):
        raise ValueError("EVAL_CONTEXT_PROTOCOL is invalid")
    manifest = environment.get("EVAL_SPLIT_MANIFEST_SHA256") or None
    split = environment.get("EVAL_SPLIT") or None
    selection_receipt = environment.get("EVAL_SELECTION_RECEIPT_SHA256") or None
    if (manifest is None) != (split is None):
        raise ValueError("frozen split provenance must include both manifest hash and split")
    if resolver == "v2" and (manifest is None or split is None):
        raise ValueError("resolver v2 requires frozen split provenance")
    if manifest is not None and not re.fullmatch(r"[0-9a-f]{64}", manifest):
        raise ValueError("frozen split provenance hash is invalid")
    if split is not None and split not in ("dev", "holdout"):
        raise ValueError("frozen split provenance split is invalid")
    if (selection_receipt is not None and
            not re.fullmatch(r"[0-9a-f]{64}", selection_receipt)):
        raise ValueError("frozen selection receipt hash is invalid")
    return {
        "resolver": resolver, "context_protocol": context_protocol,
        "split_manifest_sha256": manifest, "split": split,
        "selection_receipt_sha256": selection_receipt,
    }


def require_frozen_run(environment=os.environ):
    try:
        descriptor = int(environment["EVAL_FROZEN_FD"])
        expected = environment["EVAL_FROZEN_TOKEN_SHA256"]
        os.set_blocking(descriptor, False)
        token = os.read(descriptor, 33)
    except (KeyError, OSError, TypeError, ValueError):
        raise ValueError(
            "paid benchmark runs must use eval/bench/frozen_run.py for durable checkpoints"
        ) from None
    finally:
        if "descriptor" in locals():
            try:
                os.close(descriptor)
            except OSError:
                pass
    if (len(token) != 32 or len(expected) != 64 or
            not hmac.compare_digest(hashlib.sha256(token).hexdigest(), expected)):
        raise ValueError(
            "paid benchmark runs must use eval/bench/frozen_run.py for durable checkpoints"
        )


def build_provider_attempt_budget(ceiling, prior_provider_attempts=0):
    """Instantiate the per-run CallBudget from a hard provider-attempt ceiling, net of attempts
    already spent by resumed rows (see artifact.resume_state's "prior_provider_attempts").

    A None ceiling means unlimited: no budget object, which is exactly today's v1/v2 behavior
    with the ceiling unset. Whoever supplies a positive ceiling is responsible for validating it
    is positive and for rejecting prior_provider_attempts that already exceed it (resume_state
    does the latter); this function only nets the two together.
    """
    if ceiling is None:
        return None
    return trial_module.CallBudget(max(ceiling - prior_provider_attempts, 0))


def artifact_filename(dataset, strong_model, provider, resolver="v1"):
    parts = (Path(dataset).stem, provider, strong_model)
    if any(not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", part) for part in parts):
        raise ValueError("dataset, provider, and model names must be safe filename components")
    if resolver not in ("v1", "v2", "v3"):
        raise ValueError("resolver must be v1, v2, or v3")
    suffix = "" if resolver == "v1" else f"-resolver-{resolver}"
    return f"bench-{parts[0]}-trial-{parts[1]}-{parts[2]}{suffix}.json"


def eval_settings(provider, strong, cheap, workers, policy, max_provider_attempts=None):
    """Assemble the frozen settings dict from launch policy.

    A v3 provider-attempt ceiling is folded in only when the resolver is v3, so v1/v2 settings
    — and therefore their metadata and artifact filenames — stay byte-identical to runs that
    predate the ceiling ever existing.
    """
    settings = {"provider": provider, "models": {"strong": strong, "cheap": cheap},
                "concurrency": workers}
    settings.update(policy)
    if policy["resolver"] == "v3":
        settings["max_provider_attempts"] = max_provider_attempts
    return settings


def checkpoint_interval(resolver):
    """v3 checkpoints (and, via write_artifact, synchronously mirrors) every completed row so a
    crash never loses more than one row's worth of provider-attempt spend; v1/v2 keep the
    coarser interval that predates the ceiling and its per-row accounting."""
    return 1 if resolver == "v3" else 25


def validate_dataset_policy(rows, policy):
    protocol = policy["context_protocol"]
    if protocol == "none":
        if any("context" in row for row in rows):
            raise ValueError("dataset context is present but not declared")
        return
    if any(row.get("language", "python").casefold() != "java" or "context" not in row
           for row in rows):
        raise ValueError("Java context protocol requires context on every row")
    for row in rows:
        validate_context(row["context"], protocol)


def require_single_language(rows):
    languages = {row.get("language", "python") for row in rows}
    if len(languages) != 1:
        raise ValueError("a frozen benchmark lane must contain exactly one language")
    return next(iter(languages))


def provider_usage(environment=os.environ):
    raw = environment.get("EVAL_PROVIDER_USAGE_JSON")
    if raw is None:
        return None
    if len(raw.encode()) > MAX_PROVIDER_USAGE_BYTES:
        raise ValueError("EVAL_PROVIDER_USAGE_JSON is too large")
    value = json.loads(raw)
    if (not isinstance(value, dict) or set(value) != {"semantics", "usage"} or
            value["semantics"] != "incremental" or not isinstance(value["usage"], dict)):
        raise ValueError("EVAL_PROVIDER_USAGE_JSON must declare incremental semantics and usage")
    validate_usage(value["usage"])
    return value["usage"]


def accumulated_usage(previous, incremental, evaluated_rows):
    return merge_usage(previous, incremental) if evaluated_rows else merge_usage(previous, None)


def mirror_frozen_checkpoint(path, metadata, environment=os.environ):
    archive = environment.get("EVAL_FROZEN_ARCHIVE_DIR")
    archive_token = environment.get("EVAL_FROZEN_ARCHIVE_TOKEN")
    if not archive or not Path(archive).is_absolute() or not archive_token:
        raise ValueError("frozen benchmark archive must be an absolute path")
    try:
        status = os.lstat(archive)
        actual = f"{status.st_dev}:{status.st_ino}:{status.st_mode}"
        archive_is_directory = stat.S_ISDIR(status.st_mode)
    except OSError:
        actual = None
        archive_is_directory = False
    if actual != archive_token or not archive_is_directory:
        raise ValueError("frozen benchmark archive was replaced")
    try:
        from .frozen_run import archive_checkpoint
    except ImportError:
        from frozen_run import archive_checkpoint
    return archive_checkpoint(Path(path), Path(archive), metadata)


def validate_runtime_metadata(
    expected, dataset, repo, settings, metadata_fn=artifact_metadata
):
    if metadata_fn(dataset, repo, settings) != expected:
        raise ValueError("benchmark runtime provenance changed before checkpoint write")


def load_dataset(path):
    payload = read_bytes(path, MAX_DATASET_BYTES, label="dataset")
    rows = [json.loads(line) for line in payload.splitlines() if line.strip()]
    if len(rows) > MAX_DATASET_ROWS:
        raise ValueError("dataset has too many rows")
    for row in rows:
        validate_benchmark_row(row, require_result=False)
        if any(not isinstance(row.get(key), str) for key in ("func", "code", "doc")):
            raise ValueError("dataset func, code, and doc must be strings")
        validate_pair(row)
    if len({row["id"] for row in rows}) != len(rows):
        raise ValueError("dataset contains duplicate pair ids")
    return payload, rows


def load_rescore(path):
    document = load_json(path, MAX_RESCORE_BYTES)
    legacy = isinstance(document, list)
    rows = artifact_rows(document)
    if len(rows) > MAX_RESCORE_ROWS:
        raise ValueError("rescore artifact has too many rows")
    for row in rows:
        validate_benchmark_row(row, require_result=not legacy)
        if legacy and row.get("got") is not None and not isinstance(row["got"], dict):
            raise ValueError("legacy benchmark row result must be an object or null")
    return rows


def subset_rows(rows, ids_path):
    """Restrict rescoring to an explicit id list, e.g. a locked holdout split.

    Every listed id must exist in the artifact: a silently ignored id would let a
    mistyped or stale split file masquerade as a scored one."""
    text = read_bytes(ids_path, MAX_IDS_BYTES, label="id list").decode()
    wanted = {line.strip() for line in text.splitlines() if line.strip()}
    if not wanted:
        raise ValueError("id list is empty")
    missing = sorted(wanted - {row.get("id") for row in rows})
    if missing:
        raise ValueError(
            f"{len(missing)} listed ids missing from artifact, first: {missing[0]}")
    return [row for row in rows if row["id"] in wanted]


def bounded_results(executor, function, items, max_in_flight):
    """Yield ordered results while keeping at most max_in_flight submitted futures."""
    if max_in_flight < 1:
        raise ValueError("max_in_flight must be positive")
    iterator = iter(items)
    pending = deque()
    for _ in range(max_in_flight):
        try:
            item = next(iterator)
        except StopIteration:
            break
        pending.append((item, executor.submit(function, item)))
    while pending:
        item, future = pending.popleft()
        yield item, future.result()
        try:
            next_item = next(iterator)
        except StopIteration:
            continue
        pending.append((next_item, executor.submit(function, next_item)))


def main():
    if "--selftest" in sys.argv:
        return selftest()
    if "--rescore" in sys.argv:
        path = Path(sys.argv[sys.argv.index("--rescore") + 1])
        rows = load_rescore(path)
        label = f", rescored from {path.name}"
        if "--ids" in sys.argv:
            ids_path = Path(sys.argv[sys.argv.index("--ids") + 1])
            rows = subset_rows(rows, ids_path)
            label += f" restricted to {len(rows)} ids from {ids_path.name}"
        return report(rows_from_transcript(rows), label)
    require_frozen_run()
    ds = Path(sys.argv[sys.argv.index("--dataset") + 1]) if "--dataset" in sys.argv \
        else Path(os.environ.get("EVAL_DATASET", HERE / "dataset.jsonl"))
    # Frozen v1 uses strong for snap/escalated prongs/synthesis and cheap for challenge, initial
    # prongs, and blind-spot. V2 adds evidence-auditor/cleared-bar plurality semantics and moves
    # blind-spot to strong; its initial prongs remain cheap unless the challenge cracks the snap.
    # Fable is banned from every role in this project.
    provider = eval_provider()
    policy = eval_policy_settings()
    max_provider_attempts = eval_max_provider_attempts()
    require_provider_attempt_ceiling(policy["resolver"], max_provider_attempts)
    default_strong = "claude-opus-4-8" if provider == "claude" else "gpt-5.6-sol"
    default_cheap = "claude-sonnet-5" if provider == "claude" else "gpt-5.6-sol"
    strong = os.environ.get("EVAL_MODEL_STRONG", default_strong)
    cheap = os.environ.get("EVAL_MODEL_CHEAP", default_cheap)
    for role, m in (("strong", strong), ("cheap", cheap)):
        assert "fable" not in m.lower(), f"Fable is banned from this project ({role}={m})"
    dataset_payload, pairs = load_dataset(ds)
    require_single_language(pairs)
    skill_payload = read_bytes(SKILL, MAX_SKILL_BYTES, label="skill input")
    set_skill_body(skill_payload.decode())
    models = {"strong": strong, "cheap": cheap, "provider": provider,
              "resolver": policy["resolver"]}
    workers = eval_concurrency()
    settings = eval_settings(provider, strong, cheap, workers, policy, max_provider_attempts)
    validate_dataset_policy(pairs, policy)
    metadata = artifact_metadata(ds, HERE.parent.parent, settings)
    if hashlib.sha256(dataset_payload).hexdigest() != metadata["dataset"]["sha256"]:
        raise ValueError("dataset changed while provenance was captured")
    if hashlib.sha256(skill_payload).hexdigest() != metadata["skill"]["sha256"]:
        raise ValueError("skill changed while provenance was captured")
    new_started_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    start = time.monotonic()
    out_dir = HERE / "out"; out_dir.mkdir(exist_ok=True)
    out_path = out_dir / artifact_filename(ds, strong, provider, policy["resolver"])
    if out_path.exists():
        state = resume_state(
            load_json(out_path, MAX_RESUME_BYTES), metadata, dataset_rows=pairs,
            provider_attempt_ceiling=max_provider_attempts,
        )
    else:
        state = {
            "rows": [], "started_at": new_started_at, "elapsed_seconds": 0,
            "provider_usage": None, "prior_provider_attempts": 0,
        }
    # A v1/v2 ceiling is always None (unlimited, exactly today's behavior). A v3 ceiling is a
    # positive int frozen into settings/metadata above, so a resumed artifact whose metadata
    # carries a DIFFERENT ceiling never reaches this line — resume_state's metadata-equality
    # check (via the settings match built into `metadata`) already rejected it as incompatible
    # provenance; a SAME ceiling instead nets resumed rows' got.execution attempts out of the
    # budget below.
    models["budget"] = build_provider_attempt_budget(
        max_provider_attempts, state["prior_provider_attempts"]
    )
    done = {item["id"]: item for item in state["rows"]}
    started_at = state["started_at"]
    prior_elapsed = state["elapsed_seconds"]
    todo = [p for p in pairs if p["id"] not in done]  # resumable: crash loses nothing scored
    current_usage = provider_usage() if todo else None
    evaluated_rows = 0

    def write_artifact(rows):
        validate_input_hashes(metadata, ds, SKILL)
        validate_runtime_metadata(metadata, ds, HERE.parent.parent, settings)
        document = artifact_document(
            rows, metadata, started_at=started_at,
            elapsed_seconds=round(prior_elapsed + time.monotonic() - start, 3),
            provider_usage=accumulated_usage(
                state["provider_usage"], current_usage, evaluated_rows
            ),
        )
        atomic_write_json(out_path, document)
        mirror_frozen_checkpoint(out_path, metadata)

    # Each write_artifact call is itself a synchronous atomic write plus a synchronous archive
    # mirror, so setting the interval to 1 for v3 satisfies "checkpoint every row" directly.
    interval = checkpoint_interval(policy["resolver"])
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=workers) as pool:
        for p, v in bounded_results(pool, lambda pair: judge(pair, models), todo, workers):
            evaluated_rows += 1
            done[p["id"]] = {**p, "got": v}
            verdict = v["final_verdict"]
            status = v["final_status"]
            if status == "abstain":
                path = "abstain"
            elif v.get("semantic_status") == "unverified":
                path = "unverified"
            else:
                path = "contested" if v.get("contested") else "clear"
            # Binary scoring folds unverified to a consistent-side prediction; the progress
            # mark must reflect what the row will score as, not print a third state.
            effective = "consistent" if path == "unverified" else verdict
            mark = "-" if status == "abstain" else \
                ("✓" if (effective == "inconsistent") == (p["label"] == "inconsistent") else "✗")
            display_verdict = "unverified" if path == "unverified" else (verdict or "abstain")
            print(f"  {mark} [{len(done)}/{len(pairs)}] {p['id']:40} label={p['label']:12} "
                  f"verdict={display_verdict:12} [{path}]", flush=True)
            if len(done) % interval == 0:
                write_artifact([done[pair["id"]] for pair in pairs if pair["id"] in done])
    transcript = [done[p["id"]] for p in pairs]
    write_artifact(transcript)
    report(rows_from_transcript(transcript), f", trial, strong={strong} cheap={cheap}")
