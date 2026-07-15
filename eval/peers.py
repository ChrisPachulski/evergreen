"""Frozen, label-blind protocol for same-corpus peer comparisons."""

from __future__ import annotations

import hashlib
import hmac
import json
import math
from pathlib import Path
import re


LANGUAGES = ("go", "java", "python", "rust", "typescript")
DECISIONS = ("abstain", "consistent", "inconsistent")
MAX_MANIFEST_BYTES = 1024 * 1024
MAX_ROWS = 100_000
MAX_TEXT_BYTES = 1024 * 1024
MAX_TOTAL_TEXT_BYTES = 64 * 1024 * 1024
MAX_REASON_CHARS = 1024
HEX = frozenset("0123456789abcdef")


class PeerError(ValueError):
    """The frozen peer manifest or a peer transcript is inadmissible."""


def canonical_bytes(value):
    return json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
        allow_nan=False,
    ).encode("ascii")


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PeerError("duplicate JSON key")
        result[key] = value
    return result


def _constant(value):
    raise PeerError(f"non-finite JSON number: {value}")


def _load(payload):
    try:
        return json.loads(payload, object_pairs_hook=_object, parse_constant=_constant)
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise PeerError("invalid peer JSON") from error


def _exact(value, keys, label):
    if not isinstance(value, dict) or set(value) != set(keys):
        raise PeerError(f"{label} fields are invalid")


def _text(value, label, maximum=4096, empty=False):
    if not isinstance(value, str) or len(value) > maximum or (not empty and not value):
        raise PeerError(f"{label} is invalid")
    return value


def _hex(value, label, lengths=(64,)):
    if (not isinstance(value, str) or len(value) not in lengths or
            any(character not in HEX for character in value)):
        raise PeerError(f"{label} is invalid")


def _strict_json(value, depth=0):
    if depth > 16:
        raise PeerError("peer JSON nesting is too deep")
    if isinstance(value, dict):
        if any(not isinstance(key, str) for key in value):
            raise PeerError("peer JSON key is invalid")
        for item in value.values():
            _strict_json(item, depth + 1)
    elif isinstance(value, list):
        for item in value:
            _strict_json(item, depth + 1)
    elif isinstance(value, float):
        if not math.isfinite(value):
            raise PeerError("peer JSON number is not finite")
    elif value is not None and not isinstance(value, (str, int, bool)):
        raise PeerError("peer JSON type is invalid")


def load_manifest(path):
    path = Path(path)
    try:
        from eval.bench.artifact import read_bytes
        payload = read_bytes(path, MAX_MANIFEST_BYTES, label="peer manifest")
    except (OSError, ValueError) as error:
        raise PeerError("peer manifest is unavailable") from error
    return load_manifest_bytes(payload)


def load_manifest_bytes(payload):
    if not isinstance(payload, bytes) or len(payload) > MAX_MANIFEST_BYTES:
        raise PeerError("peer manifest bytes are invalid")
    document = _load(payload)
    _exact(
        document, {"schema_version", "kind", "languages", "peers", "exclusions"},
        "manifest",
    )
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise PeerError("peer manifest schema is invalid")
    if document["kind"] != "evergreen-peer-manifest":
        raise PeerError("peer manifest kind is invalid")
    if document["languages"] != list(LANGUAGES):
        raise PeerError("peer manifest languages are invalid")
    items = document["peers"]
    if not isinstance(items, list) or not items or len(items) > 64:
        raise PeerError("peer manifest entries are invalid")
    seen = set()
    for item in items:
        _exact(
            item,
            {"id", "source", "config", "config_sha256", "applicability"},
            "peer",
        )
        identifier = _text(item["id"], "peer ID", 128)
        if identifier in seen:
            raise PeerError("peer identity is invalid")
        seen.add(identifier)
        source = item["source"]
        _exact(
            source,
            {
                "kind", "url", "commit", "tree", "license", "license_path",
                "license_sha256", "lock_path", "lock_sha256",
            },
            "peer source",
        )
        if source["kind"] not in ("git", "protocol"):
            raise PeerError("peer source kind is invalid")
        _text(source["url"], "peer source URL", 2048)
        _text(source["license"], "peer license", 128)
        _hex(source["license_sha256"], "peer license hash")
        _hex(source["lock_sha256"], "peer lock hash")
        if source["kind"] == "git":
            if not source["url"].startswith("https://"):
                raise PeerError("peer Git URL is invalid")
            _hex(source["commit"], "peer source commit", (40, 64))
            _hex(source["tree"], "peer source tree", (40, 64))
            _text(source["license_path"], "peer license path", 256)
            _text(source["lock_path"], "peer lock path", 256)
        else:
            if not source["url"].startswith("evergreen://"):
                raise PeerError("peer protocol URL is invalid")
            _text(source["commit"], "peer protocol revision", 128)
            _hex(source["tree"], "peer protocol hash")
            if source["license_path"] != "" or source["lock_path"] != "":
                raise PeerError("peer protocol file paths are invalid")
        _strict_json(item["config"])
        _hex(item["config_sha256"], "peer config hash")
        if hashlib.sha256(canonical_bytes(item["config"])).hexdigest() != item["config_sha256"]:
            raise PeerError("peer config hash does not match config")
        if source["kind"] == "protocol" and source["tree"] != item["config_sha256"]:
            raise PeerError("peer protocol hash does not match config")
        applicability = item["applicability"]
        if not isinstance(applicability, dict) or set(applicability) != set(LANGUAGES):
            raise PeerError("peer applicability is incomplete")
        for language in LANGUAGES:
            state = applicability[language]
            _exact(state, {"state", "reason"}, "peer applicability")
            if state["state"] not in ("applicable", "not-applicable"):
                raise PeerError("peer applicability state is invalid")
            _text(state["reason"], "peer applicability reason", MAX_REASON_CHARS, empty=True)
            if (state["state"] == "applicable") != (state["reason"] == ""):
                raise PeerError("peer applicability reason is invalid")
    if "direct-baseline" not in seen:
        raise PeerError("direct baseline is required")
    baseline = next(item for item in items if item["id"] == "direct-baseline")
    if any(
            state["state"] != "applicable" for state in baseline["applicability"].values()
    ):
        raise PeerError("direct baseline must apply to every language")
    exclusions = document["exclusions"]
    if not isinstance(exclusions, list) or len(exclusions) > 64:
        raise PeerError("peer exclusions are invalid")
    excluded = set()
    for item in exclusions:
        _exact(
            item, {"id", "source_url", "source_commit", "reason_code", "detail"},
            "peer exclusion",
        )
        identifier = _text(item["id"], "excluded peer ID", 128)
        if identifier in seen or identifier in excluded:
            raise PeerError("excluded peer identity is invalid")
        excluded.add(identifier)
        url = _text(item["source_url"], "excluded peer source URL", 2048)
        if not url.startswith("https://"):
            raise PeerError("excluded peer source URL is invalid")
        _hex(item["source_commit"], "excluded peer source commit", (40, 64))
        _text(item["reason_code"], "peer exclusion reason", 128)
        _text(item["detail"], "peer exclusion detail", MAX_REASON_CHARS)
    return document


def verify_git_source(source, checkout):
    """Verify a pre-fetched peer checkout without network access or package execution."""
    from evergreen import receipt

    if not isinstance(source, dict) or source.get("kind") != "git":
        raise PeerError("peer source is not a Git checkout")
    root = Path(checkout)
    try:
        commit = receipt._git(root, "rev-parse", "--verify", "HEAD^{commit}").strip()
        tree = receipt._git(root, "rev-parse", "--verify", "HEAD^{tree}").strip()
        status = receipt._git(
            root, "status", "--porcelain=v1", "--untracked-files=all",
        )
        license_bytes = receipt._read_repo_file(
            root, source["license_path"], max_bytes=16 * 1024 * 1024,
        )
        lock_bytes = receipt._read_repo_file(
            root, source["lock_path"], max_bytes=16 * 1024 * 1024,
        )
    except (KeyError, OSError, receipt.ReceiptError) as error:
        raise PeerError("peer Git source could not be verified") from error
    if status:
        raise PeerError("peer Git source must be clean")
    if commit != source["commit"] or tree != source["tree"]:
        raise PeerError("peer Git source identity does not match manifest")
    if hashlib.sha256(license_bytes).hexdigest() != source["license_sha256"]:
        raise PeerError("peer license hash does not match manifest")
    if hashlib.sha256(lock_bytes).hexdigest() != source["lock_sha256"]:
        raise PeerError("peer lock hash does not match manifest")
    return True


def _private_rows(rows):
    if not isinstance(rows, list) or not rows or len(rows) > MAX_ROWS:
        raise PeerError("private peer rows are invalid")
    seen = set()
    total = 0
    normalized = []
    for row in rows:
        if not isinstance(row, dict):
            raise PeerError("private peer row is invalid")
        for key in ("id", "language", "code", "documentation", "label"):
            if key not in row:
                raise PeerError("private peer row is incomplete")
        identifier = _text(row["id"], "private row ID", 4096)
        if identifier in seen:
            raise PeerError("private row ID is duplicated")
        seen.add(identifier)
        if row["language"] not in LANGUAGES:
            raise PeerError("private row language is invalid")
        if row["label"] not in ("consistent", "inconsistent"):
            raise PeerError("private oracle label is invalid")
        code = _text(row["code"], "private row code", MAX_TEXT_BYTES)
        documentation = _text(
            row["documentation"], "private row documentation", MAX_TEXT_BYTES,
        )
        total += len(code.encode()) + len(documentation.encode())
        if total > MAX_TOTAL_TEXT_BYTES:
            raise PeerError("private peer input is too large")
        normalized.append(row)
    return sorted(normalized, key=lambda row: row["id"].encode())


def benchmark_private_rows(rows):
    """Project benchmark/oracle rows to the only private fields the trusted scorer needs."""
    if not isinstance(rows, list):
        raise PeerError("benchmark peer rows are invalid")
    projected = []
    for row in rows:
        if not isinstance(row, dict):
            raise PeerError("benchmark peer row is invalid")
        documentation = row.get("documentation", row.get("doc"))
        projected.append({
            "id": row.get("id"),
            "language": row.get("language"),
            "code": row.get("code"),
            "documentation": documentation,
            "label": row.get("label"),
        })
    return _private_rows(projected)


def artifact_filename(dataset, provider, model, peer_id):
    parts = (Path(dataset).stem, provider, model, peer_id)
    if any(not isinstance(part, str) or not re.fullmatch(
            r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", part) for part in parts):
        raise PeerError("peer artifact name is invalid")
    return f"peer-{parts[0]}-{provider}-{model}-{peer_id}.json"


def id_set_sha256(rows):
    ordered = _private_rows(rows)
    digest = hashlib.sha256()
    for row in ordered:
        value = row["id"].encode()
        digest.update(len(value).to_bytes(8, "big"))
        digest.update(value)
    return digest.hexdigest()


def _opaque_id(secret, identifier):
    if not isinstance(secret, bytes) or len(secret) < 32:
        raise PeerError("peer run secret is invalid")
    return hmac.new(secret, identifier.encode(), hashlib.sha256).hexdigest()


def freeze_request(rows, secret):
    ordered = _private_rows(rows)
    public_rows = [{
        "opaque_id": _opaque_id(secret, row["id"]),
        "language": row["language"],
        "code": row["code"],
        "documentation": row["documentation"],
    } for row in ordered]
    body = {
        "schema_version": 1,
        "kind": "evergreen-peer-input",
        "id_set_sha256": id_set_sha256(ordered),
        "rows": public_rows,
    }
    body["input_sha256"] = hashlib.sha256(canonical_bytes(body)).hexdigest()
    return body


def validate_output(output, request):
    _exact(request, {"schema_version", "kind", "id_set_sha256", "input_sha256", "rows"},
           "peer request")
    if not isinstance(output, dict):
        raise PeerError("peer output is invalid")
    _exact(output, {"schema_version", "kind", "input_sha256", "rows"}, "peer output")
    if type(output["schema_version"]) is not int or output["schema_version"] != 1:
        raise PeerError("peer output schema is invalid")
    if output["kind"] != "evergreen-peer-decisions":
        raise PeerError("peer output kind is invalid")
    if output["input_sha256"] != request["input_sha256"]:
        raise PeerError("peer output is bound to different input")
    rows = output["rows"]
    if not isinstance(rows, list) or len(rows) > MAX_ROWS:
        raise PeerError("peer output rows are invalid")
    expected = {item["opaque_id"] for item in request["rows"]}
    decisions = {}
    for item in rows:
        _exact(item, {"opaque_id", "decision"}, "peer decision")
        identifier = item["opaque_id"]
        _hex(identifier, "opaque peer row ID")
        if identifier in decisions or item["decision"] not in DECISIONS:
            raise PeerError("peer decision is invalid")
        decisions[identifier] = item["decision"]
    if set(decisions) != expected:
        raise PeerError("peer output does not contain the exact input ID set")
    return tuple(sorted(decisions.items()))


def run_document(
    metadata, request, rows, *, started_at, elapsed_seconds, provider_usage=None,
):
    """Build a resumable peer checkpoint that contains no oracle labels or private IDs."""
    document = {
        "schema_version": 1,
        "kind": "evergreen-peer-run",
        "metadata": metadata,
        "request": {
            "id_set_sha256": request["id_set_sha256"],
            "input_sha256": request["input_sha256"],
        },
        "rows": rows,
        "timing": {
            "started_at": started_at,
            "elapsed_seconds": elapsed_seconds,
        },
    }
    if provider_usage is not None:
        document["provider_usage"] = provider_usage
    validate_run_document(document, metadata, request)
    return document


def validate_run_document(document, expected_metadata, request):
    from eval.bench.artifact import valid_iso_time, validate_usage

    required = {"schema_version", "kind", "metadata", "request", "rows", "timing"}
    if isinstance(document, dict) and "provider_usage" in document:
        required.add("provider_usage")
    _exact(document, required, "peer run")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise PeerError("peer run schema is invalid")
    if document["kind"] != "evergreen-peer-run" or document["metadata"] != expected_metadata:
        raise PeerError("peer run provenance is invalid")
    _exact(document["request"], {"id_set_sha256", "input_sha256"}, "peer run request")
    if document["request"] != {
            "id_set_sha256": request["id_set_sha256"],
            "input_sha256": request["input_sha256"]}:
        raise PeerError("peer run request identity is invalid")
    timing = document["timing"]
    _exact(timing, {"started_at", "elapsed_seconds"}, "peer run timing")
    elapsed = timing["elapsed_seconds"]
    if (not valid_iso_time(timing["started_at"]) or type(elapsed) not in (int, float) or
            not math.isfinite(elapsed) or elapsed < 0):
        raise PeerError("peer run timing is invalid")
    if "provider_usage" in document:
        try:
            validate_usage(document["provider_usage"])
        except ValueError as error:
            raise PeerError("peer run provider usage is invalid") from error
    expected_ids = {item["opaque_id"] for item in request["rows"]}
    rows = document["rows"]
    if not isinstance(rows, list) or len(rows) > len(expected_ids):
        raise PeerError("peer run rows are invalid")
    decisions = {}
    for item in rows:
        _exact(item, {"opaque_id", "decision"}, "peer run decision")
        identifier = item["opaque_id"]
        if identifier not in expected_ids or identifier in decisions or item["decision"] not in DECISIONS:
            raise PeerError("peer run decision is invalid")
        decisions[identifier] = item["decision"]
    return decisions


def _language_score(rows):
    tp = fp = fn = tn = abstained = 0
    for label, decision in rows:
        if decision == "abstain":
            abstained += 1
        elif label == "inconsistent" and decision == "inconsistent":
            tp += 1
        elif label == "consistent" and decision == "inconsistent":
            fp += 1
        elif label == "inconsistent":
            fn += 1
        else:
            tn += 1
    completed = tp + fp + fn + tn
    precision = tp / (tp + fp) if tp + fp else 0.0
    recall = tp / (tp + fn) if tp + fn else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    specificity = tn / (tn + fp) if tn + fp else 0.0
    return {
        "attempted": completed + abstained,
        "completed": completed,
        "abstained": abstained,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "precision": precision, "recall": recall, "f1": f1,
        "specificity": specificity,
    }


def _manifest_sha256(manifest):
    return hashlib.sha256(canonical_bytes(manifest)).hexdigest()


def _source_sha256(source):
    return hashlib.sha256(canonical_bytes(source)).hexdigest()


def score_output(
    *, manifest, peer_id, subject_commit, private_rows, secret, request, output,
):
    _text(peer_id, "peer ID", 128)
    _hex(subject_commit, "subject commit", (40, 64))
    try:
        peer = next(item for item in manifest["peers"] if item["id"] == peer_id)
    except (KeyError, StopIteration, TypeError):
        raise PeerError("peer is absent from frozen manifest") from None
    ordered = _private_rows(private_rows)
    languages = {row["language"] for row in ordered}
    applicable = {
        language for language, state in peer["applicability"].items()
        if state["state"] == "applicable"
    }
    if not languages or not languages <= applicable:
        raise PeerError("peer input contains a non-applicable language")
    expected_request = freeze_request(ordered, secret)
    if request != expected_request:
        raise PeerError("peer request does not match private holdout rows")
    decisions = dict(validate_output(output, request))
    by_language = {language: [] for language in LANGUAGES}
    for row in ordered:
        opaque = _opaque_id(secret, row["id"])
        by_language[row["language"]].append((row["label"], decisions[opaque]))
    return {
        "schema_version": 1,
        "kind": "evergreen-peer-result",
        "peer_id": peer_id,
        "subject_commit": subject_commit,
        "peer_manifest_sha256": _manifest_sha256(manifest),
        "peer_config_sha256": peer["config_sha256"],
        "peer_source_sha256": _source_sha256(peer["source"]),
        "id_set_sha256": request["id_set_sha256"],
        "input_sha256": request["input_sha256"],
        "output_sha256": hashlib.sha256(canonical_bytes(output)).hexdigest(),
        "languages": {
            language: _language_score(by_language[language]) for language in sorted(languages)
        },
    }


def comparison_complete(manifest, bundles, subject_commit, canonical_private_rows):
    try:
        _hex(subject_commit, "subject commit", (40, 64))
        expected = {item["id"]: item for item in manifest["peers"]}
        canonical = benchmark_private_rows(canonical_private_rows)
        projected = {}
        for peer_id, peer in expected.items():
            applicable = {
                language for language, state in peer["applicability"].items()
                if state["state"] == "applicable"
            }
            projected[peer_id] = [
                row for row in canonical if row["language"] in applicable
            ]
            if not projected[peer_id]:
                return False
        if not isinstance(bundles, list):
            return False
        observed = {}
        for bundle in bundles:
            if not isinstance(bundle, dict) or set(bundle) != {
                    "private_rows", "secret", "request", "output", "result"}:
                return False
            result = bundle["result"]
            peer_id = result.get("peer_id") if isinstance(result, dict) else None
            if peer_id in observed or peer_id not in expected:
                return False
            recomputed = score_output(
                manifest=manifest, peer_id=peer_id, subject_commit=subject_commit,
                private_rows=bundle["private_rows"], secret=bundle["secret"],
                request=bundle["request"], output=bundle["output"],
            )
            if result != recomputed:
                return False
            bundle_rows = benchmark_private_rows(bundle["private_rows"])
            if bundle_rows != projected[peer_id]:
                return False
            if result["id_set_sha256"] != id_set_sha256(projected[peer_id]):
                return False
            required_languages = {
                language for language, state in expected[peer_id]["applicability"].items()
                if state["state"] == "applicable"
            }
            if set(result["languages"]) != required_languages:
                return False
            if any(metrics["attempted"] <= 0 for metrics in result["languages"].values()):
                return False
            observed[peer_id] = result
        return set(observed) == set(expected)
    except (AttributeError, KeyError, PeerError, TypeError):
        return False
