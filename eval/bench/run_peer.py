#!/usr/bin/env python3
"""Execute frozen peer adapters without exposing oracle labels to the peer."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import sys
import time

try:
    from .. import peers as peer_protocol
    from .artifact import artifact_metadata, atomic_write_json, load_json
    from .java_context import PROTOCOLS as CONTEXT_PROTOCOLS
    from .runner import bounded_results, load_dataset, require_frozen_run
    from .trial import UNTRUSTED_DATA_INSTRUCTION, model_json
except ImportError:  # Direct script execution.
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from eval import peers as peer_protocol
    from artifact import artifact_metadata, atomic_write_json, load_json
    from java_context import PROTOCOLS as CONTEXT_PROTOCOLS
    from runner import bounded_results, load_dataset, require_frozen_run
    from trial import UNTRUSTED_DATA_INSTRUCTION, model_json


HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
MAX_SETTINGS_BYTES = 16 * 1024
MAX_RUN_BYTES = 64 * 1024 * 1024
SETTING_KEYS = {
    "provider", "model", "peer_id", "peer_manifest_sha256", "peer_config_sha256",
    "peer_source_sha256", "concurrency", "resolver", "context_protocol",
    "split_manifest_sha256", "split", "selection_receipt_sha256",
}


def validate_settings(settings):
    if not isinstance(settings, dict) or set(settings) != SETTING_KEYS:
        raise ValueError("peer settings are invalid")
    if settings["provider"] not in ("claude", "codex"):
        raise ValueError("peer provider is invalid")
    for key in ("model", "peer_id"):
        if not isinstance(settings[key], str) or not settings[key] or len(settings[key]) > 128:
            raise ValueError(f"peer {key} is invalid")
    for key in ("peer_manifest_sha256", "peer_config_sha256", "peer_source_sha256"):
        value = settings[key]
        if (not isinstance(value, str) or len(value) != 64 or
                any(character not in "0123456789abcdef" for character in value)):
            raise ValueError(f"peer {key} is invalid")
    if type(settings["concurrency"]) is not int or not 1 <= settings["concurrency"] <= 32:
        raise ValueError("peer concurrency is invalid")
    if settings["resolver"] not in ("v1", "v2"):
        raise ValueError("peer resolver is invalid")
    if settings["context_protocol"] not in ("none", *CONTEXT_PROTOCOLS):
        raise ValueError("peer context protocol is invalid")
    manifest_hash = settings["split_manifest_sha256"]
    split = settings["split"]
    if (manifest_hash is None) != (split is None):
        raise ValueError("peer split provenance is incomplete")
    if manifest_hash is not None:
        if (not isinstance(manifest_hash, str) or len(manifest_hash) != 64 or
                any(character not in "0123456789abcdef" for character in manifest_hash)):
            raise ValueError("peer split manifest hash is invalid")
        if split not in ("dev", "holdout"):
            raise ValueError("peer split is invalid")
    receipt_hash = settings["selection_receipt_sha256"]
    if (receipt_hash is not None and
            (not isinstance(receipt_hash, str) or len(receipt_hash) != 64 or
             any(character not in "0123456789abcdef" for character in receipt_hash))):
        raise ValueError("peer selection receipt hash is invalid")
    return settings


def load_settings(payload):
    if not isinstance(payload, str) or len(payload.encode()) > MAX_SETTINGS_BYTES:
        raise ValueError("peer settings payload is invalid")
    try:
        value = json.loads(
            payload, object_pairs_hook=peer_protocol._object,
            parse_constant=peer_protocol._constant,
        )
    except (json.JSONDecodeError, peer_protocol.PeerError) as error:
        raise ValueError("peer settings payload is invalid") from error
    return validate_settings(value)


def bound_peer(settings, manifest_path=REPO / "eval" / "peers-v1.json"):
    """Re-derive the frozen peer identity inside the child process."""
    manifest = peer_protocol.load_manifest(manifest_path)
    if peer_protocol._manifest_sha256(manifest) != settings["peer_manifest_sha256"]:
        raise ValueError("peer manifest does not match frozen settings")
    peer = next(
        (item for item in manifest["peers"] if item["id"] == settings["peer_id"]), None,
    )
    if peer is None:
        raise ValueError("peer is absent from frozen manifest")
    if peer["config_sha256"] != settings["peer_config_sha256"]:
        raise ValueError("peer config does not match frozen settings")
    if peer_protocol._source_sha256(peer["source"]) != settings["peer_source_sha256"]:
        raise ValueError("peer source does not match frozen settings")
    return manifest, peer


def direct_decision(item, model, provider, *, call=model_json):
    """Make exactly one direct classification call for one already-opaque row."""
    public = {
        "opaque_id": item["opaque_id"],
        "language": item["language"],
        "code": item["code"],
        "documentation": item["documentation"],
    }
    prompt = (
        "Judge whether the documentation is consistent with the supplied code. "
        "Code doing more than documented is consistent. Use only the supplied data.\n\n"
        + UNTRUSTED_DATA_INSTRUCTION + "\nUNTRUSTED_PEER_INPUT_JSON="
        + json.dumps(public, sort_keys=True, separators=(",", ":"))
        + "\n\nReply with exactly one JSON object: "
        '{"opaque_id":"<copy exactly>","decision":"consistent"|"inconsistent"}'
    )
    result = call(prompt, model, provider)
    expected = {"opaque_id", "decision"}
    value = result.get("value") if isinstance(result, dict) and result.get("status") == "ok" else None
    if (not isinstance(value, dict) or set(value) != expected or
            value.get("opaque_id") != item["opaque_id"] or
            value.get("decision") not in ("consistent", "inconsistent")):
        decision = "abstain"
    else:
        decision = value["decision"]
    return {"opaque_id": item["opaque_id"], "decision": decision}


def run_direct(
    request, model, provider, previous=None, *, decide=direct_decision, checkpoint=None,
    concurrency=1,
):
    previous = dict(previous or {})
    expected = {item["opaque_id"] for item in request["rows"]}
    if not set(previous) <= expected or any(value not in (
            "abstain", "consistent", "inconsistent") for value in previous.values()):
        raise ValueError("peer resume state is invalid")
    if type(concurrency) is not int or not 1 <= concurrency <= 32:
        raise ValueError("peer concurrency is invalid")
    todo = [item for item in request["rows"] if item["opaque_id"] not in previous]
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=concurrency) as pool:
        for item, result in bounded_results(
                pool, lambda value: decide(value, model, provider), todo, concurrency):
            if (not isinstance(result, dict) or set(result) != {"opaque_id", "decision"} or
                    result["opaque_id"] != item["opaque_id"] or
                    result["decision"] not in ("abstain", "consistent", "inconsistent")):
                raise ValueError("peer adapter returned an invalid decision")
            previous[item["opaque_id"]] = result["decision"]
            if checkpoint is not None:
                checkpoint(dict(previous))
    return {
        "schema_version": 1,
        "kind": "evergreen-peer-decisions",
        "input_sha256": request["input_sha256"],
        "rows": [
            {"opaque_id": item["opaque_id"], "decision": previous[item["opaque_id"]]}
            for item in request["rows"]
        ],
    }


def run_local(request, peer_id, checkout, previous=None, *, execute=None, checkpoint=None):
    """Execute one pinned local adapter over the exact opaque request."""
    expected = {item["opaque_id"] for item in request["rows"]}
    previous = dict(previous or {})
    if not set(previous) <= expected or any(value not in peer_protocol.DECISIONS
                                             for value in previous.values()):
        raise ValueError("peer resume state is invalid")
    if set(previous) == expected:
        output = {
            "schema_version": 1,
            "kind": "evergreen-peer-decisions",
            "input_sha256": request["input_sha256"],
            "rows": [
                {"opaque_id": item["opaque_id"], "decision": previous[item["opaque_id"]]}
                for item in request["rows"]
            ],
        }
        peer_protocol.validate_output(output, request)
        return output
    if execute is None:
        if peer_id != "drift-guardian":
            raise ValueError("local peer has no frozen adapter")
        from eval.peer_adapters import drift_guardian

        def execute(payload, root):
            return drift_guardian.run_bytes(payload, checkout=root)
    raw = execute(peer_protocol.canonical_bytes(request), Path(checkout))
    if not isinstance(raw, bytes):
        raise ValueError("local peer adapter output is invalid")
    output = peer_protocol._load(raw)
    peer_protocol.validate_output(output, request)
    if checkpoint is not None:
        checkpoint({item["opaque_id"]: item["decision"] for item in output["rows"]})
    return output


def _peer_key(environment=os.environ):
    descriptor = None
    try:
        descriptor = int(environment["EVAL_PEER_KEY_FD"])
        key = os.read(descriptor, 33)
    except (KeyError, OSError, TypeError, ValueError):
        raise ValueError("peer opaque-ID key is unavailable") from None
    finally:
        if descriptor is not None:
            try:
                os.close(descriptor)
            except OSError:
                pass
    if len(key) != 32:
        raise ValueError("peer opaque-ID key must contain exactly 32 bytes")
    return key


def main(argv=None, environment=os.environ):
    argv = list(sys.argv[1:] if argv is None else argv)
    require_frozen_run(environment)
    try:
        dataset = Path(argv[argv.index("--dataset") + 1])
    except (ValueError, IndexError):
        raise ValueError("peer runner requires --dataset") from None
    settings = load_settings(environment.get("EVAL_PEER_SETTINGS_JSON"))
    _manifest, peer = bound_peer(settings)
    key = _peer_key(environment)
    _payload, benchmark_rows = load_dataset(dataset)
    private_rows = peer_protocol.benchmark_private_rows(benchmark_rows)
    request = peer_protocol.freeze_request(private_rows, key)
    metadata = artifact_metadata(dataset, REPO, settings)
    output = HERE / "out" / peer_protocol.artifact_filename(
        dataset, settings["provider"], settings["model"], settings["peer_id"],
    )
    output.parent.mkdir(exist_ok=True)
    started_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    prior_elapsed = 0.0
    previous = {}
    if output.exists():
        document = load_json(output, MAX_RUN_BYTES)
        previous = peer_protocol.validate_run_document(document, metadata, request)
        started_at = document["timing"]["started_at"]
        prior_elapsed = document["timing"]["elapsed_seconds"]
    start = time.monotonic()

    def checkpoint(decisions):
        decided_rows = [
            {"opaque_id": item["opaque_id"], "decision": decisions[item["opaque_id"]]}
            for item in request["rows"] if item["opaque_id"] in decisions
        ]
        document = peer_protocol.run_document(
            metadata, request, decided_rows, started_at=started_at,
            elapsed_seconds=round(prior_elapsed + time.monotonic() - start, 3),
        )
        atomic_write_json(output, document)

    if peer["source"]["kind"] == "protocol":
        if settings["peer_id"] != "direct-baseline" or environment.get("EVAL_PEER_CHECKOUT"):
            raise ValueError("protocol peer execution is invalid")
        result = run_direct(
            request, settings["model"], settings["provider"], previous,
            checkpoint=checkpoint, concurrency=settings["concurrency"],
        )
    else:
        checkout = environment.get("EVAL_PEER_CHECKOUT")
        if not checkout:
            raise ValueError("local peer checkout is unavailable")
        result = run_local(
            request, settings["peer_id"], checkout, previous, checkpoint=checkpoint,
        )
    checkpoint({item["opaque_id"]: item["decision"] for item in result["rows"]})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
