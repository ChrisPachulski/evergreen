#!/usr/bin/env python3
"""Fail-closed launcher with durable, content-addressed benchmark checkpoints."""

import argparse
import fcntl
import hashlib
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time

try:
    from .. import peers as peer_protocol
    from . import bind_subset
    from .artifact import (
        MAX_ARTIFACT_BYTES, artifact_metadata, git_identity, read_bytes, resume_state,
    )
    from .runner import artifact_filename, load_dataset, require_single_language
    from .java_context import (
        PROTOCOL as JAVA_CONTEXT_PROTOCOL, PROTOCOLS as JAVA_CONTEXT_PROTOCOLS, validate_context,
    )
    from .split_manifest import (
        MAX_DATASET_BYTES, MAX_MANIFEST_BYTES, load_split_bindings_bytes,
    )
except ImportError:
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))
    from eval import peers as peer_protocol
    import bind_subset
    from artifact import (
        MAX_ARTIFACT_BYTES, artifact_metadata, git_identity, read_bytes, resume_state,
    )
    from runner import artifact_filename, load_dataset, require_single_language
    from java_context import (
        PROTOCOL as JAVA_CONTEXT_PROTOCOL, PROTOCOLS as JAVA_CONTEXT_PROTOCOLS, validate_context,
    )
    from split_manifest import MAX_DATASET_BYTES, MAX_MANIFEST_BYTES, load_split_bindings_bytes


HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
ARCHIVE_NAME = re.compile(r"\.rows-(\d+)\.([0-9a-f]{64})\.json$")


class LiveArtifactIncompatible(ValueError):
    pass


def head_bound_bytes(path, maximum, label, commit="HEAD"):
    """Read a tracked repository file only when its bytes exactly match HEAD."""
    repo = REPO.resolve()
    try:
        resolved = Path(path).resolve(strict=True)
        relative = resolved.relative_to(repo)
    except (OSError, ValueError) as error:
        raise ValueError(f"{label} must be inside the repository") from error
    if not relative.parts or relative.parts[0] == ".git":
        raise ValueError(f"{label} must be a tracked repository file")
    working = read_bytes(resolved, maximum, label=label)
    completed = subprocess.run(
        ["git", "show", f"{commit}:{relative.as_posix()}"], cwd=repo,
        capture_output=True, timeout=30, check=False,
    )
    if completed.returncode or len(completed.stdout) > maximum:
        raise ValueError(f"{label} must be tracked at HEAD")
    if completed.stdout != working:
        raise ValueError(f"{label} bytes must exactly match HEAD")
    return working


def screen_selection_policy(
    dataset, dataset_payload, split_manifest, split_manifest_payload, split,
    parent_dataset, parent_manifest, vote_ledger, selection_receipt,
    subject_commit="HEAD",
):
    """Recompute a screened subset and bind its full ancestry to tracked receipts."""
    parent_before = read_bytes(
        parent_dataset, MAX_DATASET_BYTES, label="selection parent dataset"
    )
    parent_manifest_before = head_bound_bytes(
        parent_manifest, MAX_MANIFEST_BYTES, "selection parent manifest", subject_commit
    )
    votes_before = read_bytes(
        vote_ledger, MAX_MANIFEST_BYTES, label="selection vote ledger"
    )
    receipt_before = head_bound_bytes(
        selection_receipt, MAX_MANIFEST_BYTES, "selection receipt", subject_commit
    )
    try:
        manifest_document = bind_subset._loads_strict(split_manifest_payload)
    except (json.JSONDecodeError, ValueError) as error:
        raise ValueError("screened split manifest is not valid JSON") from error
    expected_manifest = bind_subset.build_manifest_bytes(
        dataset_payload, [parent_before], parent_manifest_before, split,
        vote_ledger_payload=votes_before,
    )
    if bind_subset.manifest_bytes(expected_manifest) != split_manifest_payload:
        raise ValueError("split manifest does not match the deterministic screen result")
    expected_receipt = bind_subset.build_screen_receipt_bytes(
        dataset_payload, parent_before, parent_manifest_before, votes_before,
        split, manifest_document,
    )
    if bind_subset.receipt_bytes(expected_receipt) != receipt_before:
        raise ValueError("selection receipt does not match the deterministic screen result")
    parent_after = read_bytes(
        parent_dataset, MAX_DATASET_BYTES, label="selection parent dataset"
    )
    votes_after = read_bytes(
        vote_ledger, MAX_MANIFEST_BYTES, label="selection vote ledger"
    )
    if (dataset_payload != read_bytes(
            dataset, MAX_DATASET_BYTES, label="split dataset provenance") or
            split_manifest_payload != head_bound_bytes(
                split_manifest, MAX_MANIFEST_BYTES, "split manifest provenance",
                subject_commit,
            ) or parent_before != parent_after or votes_before != votes_after or
            parent_manifest_before != head_bound_bytes(
                parent_manifest, MAX_MANIFEST_BYTES, "selection parent manifest",
                subject_commit,
            ) or receipt_before != head_bound_bytes(
                selection_receipt, MAX_MANIFEST_BYTES, "selection receipt",
                subject_commit,
            )):
        raise ValueError("screen selection ancestry changed during frozen preflight")
    return hashlib.sha256(receipt_before).hexdigest()


def peer_policy(manifest_path, peer_id, rows, subject_commit="HEAD"):
    """Bind a label-blind peer lane to one frozen manifest entry."""
    if manifest_path is None or not isinstance(peer_id, str) or not peer_id:
        raise ValueError("peer manifest and peer ID must be declared together")
    path = Path(manifest_path)
    try:
        before = head_bound_bytes(
            path, peer_protocol.MAX_MANIFEST_BYTES, "peer manifest provenance",
            subject_commit,
        )
    except (OSError, ValueError) as error:
        raise ValueError("peer manifest is unavailable") from error
    manifest = peer_protocol.load_manifest_bytes(before)
    try:
        after = head_bound_bytes(
            path, peer_protocol.MAX_MANIFEST_BYTES, "peer manifest provenance",
            subject_commit,
        )
    except (OSError, ValueError) as error:
        raise ValueError("peer manifest is unavailable") from error
    if before != after:
        raise ValueError("peer manifest changed during frozen preflight")
    selected = next((item for item in manifest["peers"] if item["id"] == peer_id), None)
    if selected is None:
        raise ValueError("peer ID is absent from frozen manifest")
    languages = {row.get("language") for row in rows if isinstance(row, dict)}
    if not languages or not languages <= set(peer_protocol.LANGUAGES):
        raise ValueError("peer input languages are invalid")
    unavailable = sorted(
        language for language in languages
        if selected["applicability"][language]["state"] != "applicable"
    )
    if unavailable:
        raise ValueError("peer is not applicable to: " + ", ".join(unavailable))
    return {
        "peer_id": peer_id,
        "peer_manifest_sha256": peer_protocol._manifest_sha256(manifest),
        "peer_config_sha256": selected["config_sha256"],
        "peer_source": selected["source"],
        "peer_source_sha256": peer_protocol._source_sha256(selected["source"]),
    }


def load_peer_key(path, repo):
    path = Path(path)
    repo = Path(repo).resolve()
    if not path.is_absolute():
        raise ValueError("peer key file must be an absolute external path")
    descriptor = None
    try:
        before = os.lstat(path)
        resolved = path.resolve(strict=True)
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o077
        ):
            raise ValueError("peer key file must be a private user-owned regular file")
        flags = os.O_RDONLY
        for name in ("O_CLOEXEC", "O_NOFOLLOW", "O_NONBLOCK"):
            flags |= getattr(os, name, 0)
        descriptor = os.open(path, flags)
        opened = os.fstat(descriptor)
        after_open = os.lstat(path)
    except OSError as error:
        if descriptor is not None:
            os.close(descriptor)
        raise ValueError("peer key file is unavailable") from error
    if path == repo or repo in path.parents or resolved == repo or repo in resolved.parents:
        os.close(descriptor)
        raise ValueError("peer key file must be outside the repository")
    def identity(value):
        return (
            value.st_dev, value.st_ino, value.st_mode, value.st_uid, value.st_nlink,
            value.st_size, value.st_mtime_ns, value.st_ctime_ns,
        )
    try:
        if (
            not stat.S_ISREG(before.st_mode)
            or before.st_uid != os.getuid()
            or before.st_nlink != 1
            or stat.S_IMODE(before.st_mode) & 0o077
            or identity(before) != identity(opened)
            or identity(opened) != identity(after_open)
        ):
            raise ValueError("peer key file must be a private user-owned regular file")
        key = os.read(descriptor, 33)
        after_read = os.fstat(descriptor)
        after_path = os.lstat(path)
        if identity(opened) != identity(after_read) or identity(opened) != identity(after_path):
            raise ValueError("peer key file changed while it was read")
        if len(key) != 32:
            raise ValueError("peer opaque-ID key must contain exactly 32 bytes")
        return key
    except OSError as error:
        raise ValueError("peer key file changed while it was read") from error
    finally:
        os.close(descriptor)


def run_policy(
    _dataset, rows, resolver, split_manifest, split, context_protocol,
    selection_parent_dataset=None, selection_parent_manifest=None,
    selection_vote_ledger=None, selection_receipt=None, subject_commit="HEAD",
    max_provider_attempts=None,
):
    """Validate and return immutable detector-policy provenance for a frozen lane."""
    if resolver not in ("v1", "v2", "v3"):
        raise ValueError("resolver must be v1, v2, or v3")
    if resolver == "v3":
        if (not isinstance(max_provider_attempts, int) or
                isinstance(max_provider_attempts, bool) or max_provider_attempts <= 0):
            raise ValueError(
                "resolver v3 requires --max-provider-attempts to be a positive integer"
            )
    elif max_provider_attempts is not None:
        raise ValueError("--max-provider-attempts requires resolver v3")
    if context_protocol not in ("none", *JAVA_CONTEXT_PROTOCOLS):
        raise ValueError("unknown context protocol")
    if resolver == "v2" and (split_manifest is None or split is None):
        raise ValueError("resolver v2 requires --split-manifest and --split")
    if (resolver == "v2" and
            any(row.get("language", "python").casefold() == "java" for row in rows) and
            context_protocol == "none"):
        raise ValueError("Java resolver v2 requires a declared context protocol")
    if (split_manifest is None) != (split is None):
        raise ValueError("split manifest and split must be declared together")
    selection_paths = (
        selection_parent_dataset, selection_parent_manifest,
        selection_vote_ledger, selection_receipt,
    )
    if any(value is not None for value in selection_paths) and not all(
            value is not None for value in selection_paths):
        raise ValueError("screen selection ancestry must be declared together")
    if any(value is not None for value in selection_paths) and split_manifest is None:
        raise ValueError("screen selection ancestry requires a split manifest")
    manifest_sha256 = None
    dataset_sha256 = None
    if split_manifest is not None:
        if split not in ("dev", "holdout"):
            raise ValueError("split must be dev or holdout")
        dataset_before = read_bytes(
            _dataset, MAX_DATASET_BYTES, label="split dataset provenance"
        )
        dataset_sha256 = hashlib.sha256(dataset_before).hexdigest()
        before = head_bound_bytes(
            split_manifest, MAX_MANIFEST_BYTES, "split manifest provenance",
            subject_commit,
        )
        assignments, row_datasets, declarations = load_split_bindings_bytes(before)
        after = read_bytes(
            split_manifest, MAX_MANIFEST_BYTES, label="split manifest provenance"
        )
        dataset_after = read_bytes(
            _dataset, MAX_DATASET_BYTES, label="split dataset provenance"
        )
        if before != after:
            raise ValueError("split manifest changed during frozen preflight")
        if dataset_before != dataset_after:
            raise ValueError("split dataset changed during frozen preflight")
        input_ids = {row.get("id") for row in rows}
        bound_ids = {
            pair_id for pair_id, digest in row_datasets.items()
            if digest == dataset_sha256
        }
        if input_ids != bound_ids:
            raise ValueError("split manifest does not bind the exact input dataset bytes")
        input_languages = {row.get("language", "python") for row in rows}
        declared_languages = {
            declaration[1] for declaration in declarations
            if len(declaration) == 2 and declaration[0] == dataset_sha256
        }
        if declared_languages and declared_languages != input_languages:
            raise ValueError("split manifest dataset language does not match input rows")
        if any(assignments.get(row.get("id")) != split for row in rows):
            raise ValueError("every input row must belong to the declared split")
        manifest_sha256 = hashlib.sha256(before).hexdigest()
    non_java_v2 = resolver == "v2" and any(
        row.get("language", "python").casefold() != "java" for row in rows
    )
    if non_java_v2 and not all(value is not None for value in selection_paths):
        raise ValueError("non-Java resolver v2 requires complete screen selection ancestry")
    selection_receipt_sha256 = None
    if all(value is not None for value in selection_paths):
        selection_receipt_sha256 = screen_selection_policy(
            _dataset, dataset_before, split_manifest, before, split,
            selection_parent_dataset, selection_parent_manifest,
            selection_vote_ledger, selection_receipt, subject_commit,
        )
    if context_protocol == "none":
        if any("context" in row for row in rows):
            raise ValueError("dataset context is present but not declared")
    else:
        if any(row.get("language", "python").casefold() != "java" or
               "context" not in row for row in rows):
            raise ValueError("context protocol requires context on every Java input row")
        for row in rows:
            validate_context(row["context"], context_protocol)
    result = {
        "resolver": resolver,
        "context_protocol": context_protocol,
        "split_manifest_sha256": manifest_sha256,
        "split": split,
        "selection_receipt_sha256": selection_receipt_sha256,
        "_validated_dataset_sha256": dataset_sha256,
    }
    if resolver == "v3":
        # Folded in only for v3, so v1/v2 settings — and therefore their metadata and artifact
        # filenames — stay byte-identical to runs from before the ceiling ever existed.
        result["max_provider_attempts"] = max_provider_attempts
    return result


def require_dataset_binding(validated_sha256, metadata):
    """Keep artifact capture bound to the dataset bytes validated by split preflight."""
    if (validated_sha256 is not None and
            metadata.get("dataset", {}).get("sha256") != validated_sha256):
        raise ValueError("dataset changed after split preflight")


def validate_locations(repo, archive):
    repo = Path(repo).resolve()
    archive = Path(archive)
    lowered = [part.casefold() for part in repo.parts]
    managed = any(
        lowered[index] == "plugins" and lowered[index + 1] in ("marketplaces", "cache")
        for index in range(len(lowered) - 1)
    )
    if managed:
        raise ValueError("benchmark runs are forbidden in a managed plugin checkout")
    if not archive.is_absolute():
        raise ValueError("archive directory must be absolute")
    archive = archive.resolve()
    if archive == repo or repo in archive.parents:
        raise ValueError("archive directory must be outside the repository")


def require_pushed(commit, remote_output):
    tips = {
        line.split()[0]
        for line in remote_output.splitlines()
        if len(line.split()) >= 2
    }
    if commit not in tips:
        raise ValueError(f"benchmark commit {commit} is not pushed as a remote ref tip")


def guard_reason(expected_identity, current_identity, free_bytes, minimum_free_bytes):
    if current_identity != expected_identity:
        return "repository identity changed during benchmark"
    if free_bytes < minimum_free_bytes:
        return "free disk fell below the declared benchmark minimum"
    return None


def workspace_token(repo):
    repo = Path(repo)
    return path_token(repo), path_token(repo / ".git")


def path_token(path):
    status = os.lstat(path)
    return status.st_dev, status.st_ino, status.st_mode


def encoded_token(path):
    return ":".join(str(value) for value in path_token(path))


def acquire_lock(lock_path):
    descriptor = os.open(
        Path(lock_path), os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600
    )
    lock = os.fdopen(descriptor, "a+b")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise ValueError("another frozen benchmark lane already owns this archive") from None
    return lock


def _artifact(raw, expected_metadata, dataset_rows=None, peer_request=None):
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("metadata") != expected_metadata:
        return None
    try:
        if peer_request is None:
            resume_state(value, expected_metadata, dataset_rows=dataset_rows)
        else:
            peer_protocol.validate_run_document(value, expected_metadata, peer_request)
    except (ValueError, peer_protocol.PeerError):
        return None
    return value


def _commit_directory(archive_root, commit):
    archive_root = Path(archive_root)
    archive_root.mkdir(parents=True, exist_ok=True)
    directory = archive_root / commit
    try:
        status = os.lstat(directory)
    except FileNotFoundError:
        directory.mkdir()
        descriptor = os.open(archive_root, os.O_RDONLY)
        try:
            os.fsync(descriptor)
        finally:
            os.close(descriptor)
    else:
        if not stat.S_ISDIR(status.st_mode):
            raise ValueError("benchmark archive commit path must be a real directory")
    return directory


def _atomic_bytes(path, raw):
    path = Path(path)
    try:
        parent_status = os.lstat(path.parent)
    except FileNotFoundError:
        path.parent.mkdir(parents=True)
        parent = os.open(path.parent.parent, os.O_RDONLY)
        try:
            os.fsync(parent)
        finally:
            os.close(parent)
    else:
        if not stat.S_ISDIR(parent_status.st_mode):
            raise ValueError("benchmark archive destination parent must be a real directory")
    descriptor, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(descriptor, "wb") as output:
            output.write(raw)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, path)
        directory = os.open(path.parent, os.O_RDONLY)
        try:
            os.fsync(directory)
        finally:
            os.close(directory)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def archive_checkpoint(
    live_path, archive_root, expected_metadata, dataset_rows=None, peer_request=None,
):
    live_path = Path(live_path)
    raw = read_bytes(live_path, MAX_ARTIFACT_BYTES, label="benchmark artifact")
    value = _artifact(raw, expected_metadata, dataset_rows, peer_request)
    if value is None:
        raise LiveArtifactIncompatible(
            "live benchmark artifact has invalid or incompatible provenance"
        )
    commit = expected_metadata["git"]["commit"]
    digest = hashlib.sha256(raw).hexdigest()
    destination = (
        _commit_directory(archive_root, commit) /
        f"{live_path.stem}.rows-{len(value['rows'])}.{digest}.json"
    )
    if destination.exists():
        if hashlib.sha256(read_bytes(
                destination, MAX_ARTIFACT_BYTES, label="archived benchmark artifact"
        )).hexdigest() != digest:
            raise ValueError("content-addressed archive path contains different bytes")
        return destination
    _atomic_bytes(destination, raw)
    return destination


def restore_latest(
    live_path, archive_root, expected_metadata, dataset_rows=None, peer_request=None,
):
    live_path = Path(live_path)
    candidates = []
    commit = expected_metadata["git"]["commit"]
    directory = Path(archive_root) / commit
    for path in directory.glob(f"{live_path.stem}.rows-*.json") if directory.exists() else ():
        match = ARCHIVE_NAME.search(path.name)
        if match is None:
            continue
        try:
            raw = read_bytes(path, MAX_ARTIFACT_BYTES, label="archived benchmark artifact")
        except (OSError, ValueError):
            continue
        if hashlib.sha256(raw).hexdigest() != match.group(2):
            continue
        value = _artifact(raw, expected_metadata, dataset_rows, peer_request)
        if value is None or len(value["rows"]) != int(match.group(1)):
            continue
        candidates.append((len(value["rows"]), path, raw))
    if not candidates:
        return None
    _rows, source, raw = max(candidates, key=lambda item: (item[0], item[1].name))
    _atomic_bytes(live_path, raw)
    return source


def quarantine_live(live_path, archive_root, expected_metadata):
    live_path = Path(live_path)
    raw = read_bytes(live_path, MAX_ARTIFACT_BYTES, label="incompatible benchmark artifact")
    digest = hashlib.sha256(raw).hexdigest()
    commit = expected_metadata["git"]["commit"]
    destination = _commit_directory(archive_root, commit) / "quarantine" / (
        f"{live_path.stem}.{digest}.json"
    )
    if not destination.exists():
        _atomic_bytes(destination, raw)
    elif hashlib.sha256(read_bytes(
            destination, MAX_ARTIFACT_BYTES, label="quarantined benchmark artifact"
    )).hexdigest() != digest:
        raise ValueError("quarantine path contains different bytes")
    live_path.unlink()
    descriptor = os.open(live_path.parent, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)
    return destination


def prepare_output(
    live_path, archive_root, expected_metadata, dataset_rows=None, peer_request=None,
):
    live_path = Path(live_path)
    if live_path.exists():
        try:
            archive_checkpoint(
                live_path, archive_root, expected_metadata, dataset_rows=dataset_rows,
                peer_request=peer_request,
            )
        except LiveArtifactIncompatible:
            quarantine_live(live_path, archive_root, expected_metadata)
    return restore_latest(
        live_path, archive_root, expected_metadata, dataset_rows=dataset_rows,
        peer_request=peer_request,
    )


def _remote_refs(repo):
    completed = subprocess.run(
        ["git", "ls-remote", "origin"], cwd=repo, capture_output=True, text=True,
        timeout=30, check=False,
    )
    if completed.returncode:
        raise ValueError("cannot verify that benchmark commit is pushed")
    if len(completed.stdout.encode()) > 1024 * 1024:
        raise ValueError("remote ref response exceeds benchmark safety limit")
    return completed.stdout


def _stop(process):
    if process.poll() is not None:
        return
    os.killpg(process.pid, signal.SIGTERM)
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait()


def monitor_process(
    *, process, output, archive, expected_metadata, expected_identity, expected_workspace,
    minimum_free, poll_seconds, archive_fn=archive_checkpoint,
    identity_fn=None, workspace_fn=None, free_fn=None, stop_fn=_stop,
    sleep_fn=time.sleep,
):
    identity_fn = identity_fn or (lambda: git_identity(REPO))
    workspace_fn = workspace_fn or (lambda: workspace_token(REPO))
    free_fn = free_fn or (lambda: min(
        shutil.disk_usage(REPO).free, shutil.disk_usage(archive).free
    ))
    try:
        while process.poll() is None:
            reason = guard_reason(
                expected_identity, identity_fn(), free_fn(), minimum_free
            )
            if reason is None and workspace_fn() != expected_workspace:
                reason = "repository directory was replaced during benchmark"
            if reason:
                stop_fn(process)
                raise RuntimeError(reason)
            if output.exists():
                archive_fn(output, archive, expected_metadata)
            sleep_fn(poll_seconds)
    except BaseException:
        stop_fn(process)
        raise
    if output.exists():
        archive_fn(output, archive, expected_metadata)
    return process.returncode


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--archive-dir", required=True, type=Path)
    parser.add_argument("--provider", choices=("claude", "codex"), default="codex")
    parser.add_argument("--strong-model", default="gpt-5.6-sol")
    parser.add_argument("--cheap-model", default="gpt-5.6-sol")
    parser.add_argument("--resolver", choices=("v1", "v2", "v3"), default="v1")
    parser.add_argument("--max-provider-attempts", type=int)
    parser.add_argument("--split-manifest", type=Path)
    parser.add_argument("--split", choices=("dev", "holdout"))
    parser.add_argument("--selection-parent-dataset", type=Path)
    parser.add_argument("--selection-parent-manifest", type=Path)
    parser.add_argument("--selection-vote-ledger", type=Path)
    parser.add_argument("--selection-receipt", type=Path)
    parser.add_argument("--peer-manifest", type=Path)
    parser.add_argument("--peer-id")
    parser.add_argument("--peer-key-file", type=Path)
    parser.add_argument("--peer-checkout", type=Path)
    parser.add_argument(
        "--context-protocol", choices=("none", *JAVA_CONTEXT_PROTOCOLS), default="none"
    )
    parser.add_argument("--concurrency", type=int, default=4)
    parser.add_argument("--minimum-free-gib", type=float, default=8.0)
    parser.add_argument("--poll-seconds", type=float, default=5.0)
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    repo = REPO.resolve()
    archive = args.archive_dir.resolve()
    validate_locations(repo, args.archive_dir)
    if args.concurrency < 1 or args.poll_seconds <= 0 or args.minimum_free_gib < 0:
        raise ValueError("concurrency, polling, and free-disk bounds must be positive")
    anchored_workspace = workspace_token(repo)
    anchored_identity = git_identity(repo)
    if (anchored_identity.get("dirty") is not False or
            anchored_identity.get("commit") == "unavailable"):
        raise ValueError("benchmark repository must be clean with available provenance")
    _payload, rows = load_dataset(args.dataset)
    if (args.peer_manifest is None) != (args.peer_id is None):
        raise ValueError("peer manifest and peer ID must be declared together")
    peer_mode = args.peer_manifest is not None
    if not peer_mode:
        require_single_language(rows)
    if peer_mode != (args.peer_key_file is not None):
        raise ValueError("peer lanes require an external 32-byte opaque-ID key")
    run_settings = run_policy(
        args.dataset, rows, args.resolver, args.split_manifest, args.split,
        args.context_protocol, args.selection_parent_dataset,
        args.selection_parent_manifest, args.selection_vote_ledger,
        args.selection_receipt, anchored_identity["commit"],
        max_provider_attempts=args.max_provider_attempts,
    )
    if (git_identity(repo) != anchored_identity or
            workspace_token(repo) != anchored_workspace):
        raise ValueError("repository identity changed during benchmark preflight")
    validated_dataset_sha256 = run_settings.pop("_validated_dataset_sha256")
    peer_request = None
    peer_key = None
    peer = None
    if peer_mode:
        peer_key = load_peer_key(args.peer_key_file, repo)
        peer = peer_policy(
            args.peer_manifest, args.peer_id, rows, anchored_identity["commit"]
        )
        if peer["peer_source"]["kind"] == "git":
            if args.peer_checkout is None:
                raise ValueError("local peer requires a verified checkout")
            peer_protocol.verify_git_source(peer["peer_source"], args.peer_checkout)
        elif args.peer_checkout is not None:
            raise ValueError("protocol peer does not accept a local checkout")
        settings = {
            "provider": args.provider,
            "model": args.strong_model,
            "peer_id": args.peer_id,
            "peer_manifest_sha256": peer["peer_manifest_sha256"],
            "peer_config_sha256": peer["peer_config_sha256"],
            "peer_source_sha256": peer["peer_source_sha256"],
            "concurrency": args.concurrency,
            **run_settings,
        }
        try:
            from .run_peer import validate_settings
        except ImportError:
            from run_peer import validate_settings
        validate_settings(settings)
        peer_request = peer_protocol.freeze_request(
            peer_protocol.benchmark_private_rows(rows), peer_key,
        )
    else:
        if args.peer_checkout is not None:
            raise ValueError("peer checkout requires a peer lane")
        settings = {
            "provider": args.provider,
            "models": {"strong": args.strong_model, "cheap": args.cheap_model},
            "concurrency": args.concurrency,
            **run_settings,
        }
    expected_metadata = artifact_metadata(args.dataset, repo, settings)
    require_dataset_binding(validated_dataset_sha256, expected_metadata)
    expected = expected_metadata["git"]
    if expected != anchored_identity:
        raise ValueError("repository identity changed during benchmark preflight")
    require_pushed(expected["commit"], _remote_refs(repo))
    if git_identity(repo) != expected or workspace_token(repo) != anchored_workspace:
        raise ValueError("repository identity changed during benchmark preflight")
    archive.mkdir(parents=True, exist_ok=True)
    archive_parent = os.open(archive.parent, os.O_RDONLY)
    try:
        os.fsync(archive_parent)
    finally:
        os.close(archive_parent)
    anchored_archive = path_token(archive)
    minimum_free = int(args.minimum_free_gib * 1024 ** 3)
    initial_free = min(shutil.disk_usage(repo).free, shutil.disk_usage(archive).free)
    reason = guard_reason(expected, expected, initial_free, minimum_free)
    if reason:
        raise ValueError(reason)

    global_lock = Path("/tmp") / f"evergreen-benchmark-{os.getuid()}.lock"
    lock = acquire_lock(global_lock)
    try:
        output = HERE / "out" / (
            peer_protocol.artifact_filename(
                args.dataset, args.provider, args.strong_model, args.peer_id,
            ) if peer_mode else artifact_filename(
                args.dataset, args.strong_model, args.provider, args.resolver,
            )
        )
        if peer_mode:
            prepare_output(
                output, archive, expected_metadata, peer_request=peer_request,
            )
        else:
            prepare_output(output, archive, expected_metadata, dataset_rows=rows)
        environment = dict(os.environ)
        read_fd, write_fd = os.pipe()
        peer_read_fd = None
        token = secrets.token_bytes(32)
        try:
            os.write(write_fd, token)
        finally:
            os.close(write_fd)
        environment.update({
            "EVAL_FROZEN_FD": str(read_fd),
            "EVAL_FROZEN_TOKEN_SHA256": hashlib.sha256(token).hexdigest(),
            "EVAL_FROZEN_ARCHIVE_DIR": str(archive),
            "EVAL_FROZEN_ARCHIVE_TOKEN": encoded_token(archive),
            "EVAL_PROVIDER": args.provider,
            "EVAL_MODEL_STRONG": args.strong_model,
            "EVAL_MODEL_CHEAP": args.cheap_model,
            "EVAL_CONCURRENCY": str(args.concurrency),
            "EVAL_RESOLVER": args.resolver,
            "EVAL_CONTEXT_PROTOCOL": args.context_protocol,
            "EVAL_SPLIT_MANIFEST_SHA256": settings["split_manifest_sha256"] or "",
            "EVAL_SPLIT": settings["split"] or "",
            "EVAL_SELECTION_RECEIPT_SHA256":
                settings["selection_receipt_sha256"] or "",
        })
        if args.resolver == "v3":
            environment["EVAL_MAX_PROVIDER_ATTEMPTS"] = str(args.max_provider_attempts)
        inherited_fds = [read_fd]
        if peer_mode:
            peer_read_fd, peer_write_fd = os.pipe()
            try:
                os.write(peer_write_fd, peer_key)
            finally:
                os.close(peer_write_fd)
            inherited_fds.append(peer_read_fd)
            environment.update({
                "EVAL_PEER_KEY_FD": str(peer_read_fd),
                "EVAL_PEER_SETTINGS_JSON": json.dumps(
                    settings, sort_keys=True, separators=(",", ":"),
                ),
            })
            if args.peer_checkout is not None:
                environment["EVAL_PEER_CHECKOUT"] = str(args.peer_checkout.resolve())
        if (git_identity(repo) != expected or workspace_token(repo) != anchored_workspace or
                path_token(archive) != anchored_archive):
            raise ValueError("repository or archive changed before benchmark spawn")
        try:
            process = subprocess.Popen(
                [
                    sys.executable,
                    str(HERE / ("run_peer.py" if peer_mode else "run_bench.py")),
                    "--dataset", str(args.dataset),
                ],
                cwd=repo, env=environment, start_new_session=True,
                pass_fds=tuple(inherited_fds),
            )
        finally:
            os.close(read_fd)
            if peer_read_fd is not None:
                os.close(peer_read_fd)
        archive_fn = (
            lambda live, root, metadata: archive_checkpoint(
                live, root, metadata, peer_request=peer_request,
            )
        ) if peer_mode else archive_checkpoint
        return monitor_process(
            process=process, output=output, archive=archive,
            expected_metadata=expected_metadata,
            expected_identity=expected,
            expected_workspace=(anchored_workspace, anchored_archive),
            minimum_free=minimum_free, poll_seconds=args.poll_seconds,
            identity_fn=lambda: git_identity(repo),
            workspace_fn=lambda: (workspace_token(repo), path_token(archive)),
            free_fn=lambda: min(
                shutil.disk_usage(repo).free, shutil.disk_usage(archive).free
            ),
            archive_fn=archive_fn,
        )
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
