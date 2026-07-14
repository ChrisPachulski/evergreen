"""Strict local launcher shared by the pinned upstream peer adapters."""

from __future__ import annotations

import hashlib
import math
import os
from pathlib import Path
import selectors
import signal
import subprocess
import time

from eval import peers


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_MANIFEST = ROOT / "eval" / "peers-v1.json"
MAX_INPUT_BYTES = 512 * 1024 * 1024
MAX_OUTPUT_BYTES = 16 * 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024


def _peer(peer_id, manifest_path=DEFAULT_MANIFEST):
    manifest = peers.load_manifest(manifest_path)
    for item in manifest["peers"]:
        if item["id"] == peer_id:
            return item
    raise peers.PeerError("peer is absent from the frozen manifest")


def _validate_request_bytes(payload, applicable_languages):
    if not isinstance(payload, bytes) or len(payload) > MAX_INPUT_BYTES:
        raise peers.PeerError("peer input bytes are invalid")
    document = peers._load(payload)
    expected = {"schema_version", "kind", "id_set_sha256", "input_sha256", "rows"}
    if not isinstance(document, dict) or set(document) != expected:
        raise peers.PeerError("peer input fields are invalid")
    if type(document["schema_version"]) is not int or document["schema_version"] != 1:
        raise peers.PeerError("peer input schema is invalid")
    if document["kind"] != "evergreen-peer-input":
        raise peers.PeerError("peer input kind is invalid")
    peers._hex(document["id_set_sha256"], "peer input ID-set hash")
    peers._hex(document["input_sha256"], "peer input hash")
    rows = document["rows"]
    if not isinstance(rows, list) or not rows or len(rows) > peers.MAX_ROWS:
        raise peers.PeerError("peer input rows are invalid")
    seen = set()
    total = 0
    for row in rows:
        if not isinstance(row, dict) or set(row) != {
                "opaque_id", "language", "code", "documentation"}:
            raise peers.PeerError("peer input row fields are invalid")
        peers._hex(row["opaque_id"], "opaque peer row ID")
        if row["opaque_id"] in seen:
            raise peers.PeerError("opaque peer row ID is duplicated")
        seen.add(row["opaque_id"])
        language = row["language"]
        if language not in peers.LANGUAGES:
            raise peers.PeerError("peer input language is invalid")
        if language not in applicable_languages:
            raise peers.PeerError(f"peer is not applicable to {language}")
        code = peers._text(row["code"], "peer input code", peers.MAX_TEXT_BYTES)
        documentation = peers._text(
            row["documentation"], "peer input documentation", peers.MAX_TEXT_BYTES,
        )
        total += len(code.encode("utf-8")) + len(documentation.encode("utf-8"))
        if total > peers.MAX_TOTAL_TEXT_BYTES:
            raise peers.PeerError("peer input text is too large")
    body = {key: value for key, value in document.items() if key != "input_sha256"}
    actual = hashlib.sha256(peers.canonical_bytes(body)).hexdigest()
    if actual != document["input_sha256"]:
        raise peers.PeerError("peer input hash does not match input")
    return document


def _terminate(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    process.wait()


def _run_bounded(command, payload, timeout):
    if (not isinstance(timeout, (int, float)) or isinstance(timeout, bool) or
            not math.isfinite(timeout) or timeout <= 0):
        raise peers.PeerError("peer timeout is invalid")
    environment = {
        "PATH": os.environ.get("PATH", "/usr/bin:/bin"),
        "HOME": os.environ.get("HOME", "/nonexistent"),
        "LANG": "C",
        "LC_ALL": "C",
        "npm_config_audit": "false",
        "npm_config_fund": "false",
        "npm_config_offline": "true",
    }
    try:
        process = subprocess.Popen(
            command, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            env=environment, start_new_session=True,
        )
    except OSError as error:
        raise peers.PeerError("peer process could not start") from error
    streams = selectors.DefaultSelector()
    output = bytearray()
    errors = bytearray()
    sent = 0
    assert process.stdin is not None and process.stdout is not None and process.stderr is not None
    for stream in (process.stdin, process.stdout, process.stderr):
        os.set_blocking(stream.fileno(), False)
    streams.register(process.stdin, selectors.EVENT_WRITE, "stdin")
    streams.register(process.stdout, selectors.EVENT_READ, "stdout")
    streams.register(process.stderr, selectors.EVENT_READ, "stderr")
    deadline = time.monotonic() + timeout
    try:
        while streams.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise peers.PeerError("peer process timed out")
            events = streams.select(min(remaining, 0.25))
            if not events and process.poll() is not None:
                events = [(key, selectors.EVENT_READ) for key in streams.get_map().values()
                          if key.data != "stdin"]
            for key, _ in events:
                stream = key.fileobj
                if key.data == "stdin":
                    try:
                        count = os.write(stream.fileno(), payload[sent:sent + 64 * 1024])
                    except BrokenPipeError:
                        count = 0
                        sent = len(payload)
                    sent += count
                    if sent >= len(payload):
                        streams.unregister(stream)
                        stream.close()
                    continue
                try:
                    chunk = os.read(stream.fileno(), 64 * 1024)
                except BlockingIOError:
                    continue
                if not chunk:
                    streams.unregister(stream)
                    stream.close()
                    continue
                target = output if key.data == "stdout" else errors
                maximum = MAX_OUTPUT_BYTES if key.data == "stdout" else MAX_STDERR_BYTES
                target.extend(chunk)
                if len(target) > maximum:
                    raise peers.PeerError(f"peer {key.data} is too large")
        return_code = process.wait()
        if return_code != 0:
            raise peers.PeerError("peer process failed")
        return bytes(output)
    finally:
        streams.close()
        _terminate(process)


def run_adapter(*, peer_id, payload, checkout, runner, applicable_languages,
                runner_arguments=(), timeout=300, manifest_path=DEFAULT_MANIFEST):
    request = _validate_request_bytes(payload, frozenset(applicable_languages))
    item = _peer(peer_id, manifest_path)
    checkout = Path(checkout)
    peers.verify_git_source(item["source"], checkout)
    command = ["node", str(runner), str(checkout), *map(str, runner_arguments)]
    raw_output = _run_bounded(command, peers.canonical_bytes(request), timeout)
    output = peers._load(raw_output)
    peers.validate_output(output, request)
    return peers.canonical_bytes(output)
