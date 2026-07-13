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
    from .artifact import (
        MAX_ARTIFACT_BYTES, artifact_metadata, git_identity, read_bytes, resume_state,
    )
    from .runner import artifact_filename, load_dataset, require_single_language
    from .java_context import PROTOCOL as JAVA_CONTEXT_PROTOCOL, validate_context
    from .split_manifest import MAX_MANIFEST_BYTES, load_split_assignments
except ImportError:
    from artifact import (
        MAX_ARTIFACT_BYTES, artifact_metadata, git_identity, read_bytes, resume_state,
    )
    from runner import artifact_filename, load_dataset, require_single_language
    from java_context import PROTOCOL as JAVA_CONTEXT_PROTOCOL, validate_context
    from split_manifest import MAX_MANIFEST_BYTES, load_split_assignments


HERE = Path(__file__).resolve().parent
REPO = HERE.parent.parent
ARCHIVE_NAME = re.compile(r"\.rows-(\d+)\.([0-9a-f]{64})\.json$")


class LiveArtifactIncompatible(ValueError):
    pass


def run_policy(_dataset, rows, resolver, split_manifest, split, context_protocol):
    """Validate and return immutable detector-policy provenance for a frozen lane."""
    if resolver not in ("v1", "v2"):
        raise ValueError("resolver must be v1 or v2")
    if context_protocol not in ("none", JAVA_CONTEXT_PROTOCOL):
        raise ValueError("unknown context protocol")
    if resolver == "v2" and (split_manifest is None or split is None):
        raise ValueError("resolver v2 requires --split-manifest and --split")
    if (split_manifest is None) != (split is None):
        raise ValueError("split manifest and split must be declared together")
    manifest_sha256 = None
    if split_manifest is not None:
        if split not in ("dev", "holdout"):
            raise ValueError("split must be dev or holdout")
        before = read_bytes(
            split_manifest, MAX_MANIFEST_BYTES, label="split manifest provenance"
        )
        assignments = load_split_assignments(Path(split_manifest))
        after = read_bytes(
            split_manifest, MAX_MANIFEST_BYTES, label="split manifest provenance"
        )
        if before != after:
            raise ValueError("split manifest changed during frozen preflight")
        if any(assignments.get(row.get("id")) != split for row in rows):
            raise ValueError("every input row must belong to the declared split")
        manifest_sha256 = hashlib.sha256(before).hexdigest()
    if context_protocol == "none":
        if any("context" in row for row in rows):
            raise ValueError("dataset context is present but not declared")
    else:
        if any(row.get("language", "python").casefold() != "java" or
               "context" not in row for row in rows):
            raise ValueError("context protocol requires context on every Java input row")
        for row in rows:
            validate_context(row["context"])
    return {
        "resolver": resolver,
        "context_protocol": context_protocol,
        "split_manifest_sha256": manifest_sha256,
        "split": split,
    }


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


def _artifact(raw, expected_metadata, dataset_rows=None):
    try:
        value = json.loads(raw)
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None
    if not isinstance(value, dict) or value.get("metadata") != expected_metadata:
        return None
    try:
        resume_state(value, expected_metadata, dataset_rows=dataset_rows)
    except ValueError:
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


def archive_checkpoint(live_path, archive_root, expected_metadata, dataset_rows=None):
    live_path = Path(live_path)
    raw = read_bytes(live_path, MAX_ARTIFACT_BYTES, label="benchmark artifact")
    value = _artifact(raw, expected_metadata, dataset_rows)
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


def restore_latest(live_path, archive_root, expected_metadata, dataset_rows=None):
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
        value = _artifact(raw, expected_metadata, dataset_rows)
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


def prepare_output(live_path, archive_root, expected_metadata, dataset_rows=None):
    live_path = Path(live_path)
    if live_path.exists():
        try:
            archive_checkpoint(
                live_path, archive_root, expected_metadata, dataset_rows=dataset_rows
            )
        except LiveArtifactIncompatible:
            quarantine_live(live_path, archive_root, expected_metadata)
    return restore_latest(
        live_path, archive_root, expected_metadata, dataset_rows=dataset_rows
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
    parser.add_argument("--resolver", choices=("v1", "v2"), default="v1")
    parser.add_argument("--split-manifest", type=Path)
    parser.add_argument("--split", choices=("dev", "holdout"))
    parser.add_argument(
        "--context-protocol", choices=("none", JAVA_CONTEXT_PROTOCOL), default="none"
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
    settings = {
        "provider": args.provider,
        "models": {"strong": args.strong_model, "cheap": args.cheap_model},
        "concurrency": args.concurrency,
    }
    _payload, rows = load_dataset(args.dataset)
    require_single_language(rows)
    settings.update(run_policy(
        args.dataset, rows, args.resolver, args.split_manifest, args.split,
        args.context_protocol,
    ))
    expected_metadata = artifact_metadata(args.dataset, repo, settings)
    expected = expected_metadata["git"]
    if expected.get("dirty") is not False or expected.get("commit") == "unavailable":
        raise ValueError("benchmark repository must be clean with available provenance")
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
        output = HERE / "out" / artifact_filename(
            args.dataset, args.strong_model, args.provider, args.resolver
        )
        prepare_output(output, archive, expected_metadata, dataset_rows=rows)
        environment = dict(os.environ)
        read_fd, write_fd = os.pipe()
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
        })
        if (git_identity(repo) != expected or workspace_token(repo) != anchored_workspace or
                path_token(archive) != anchored_archive):
            raise ValueError("repository or archive changed before benchmark spawn")
        try:
            process = subprocess.Popen(
                [sys.executable, str(HERE / "run_bench.py"), "--dataset", str(args.dataset)],
                cwd=repo, env=environment, start_new_session=True, pass_fds=(read_fd,),
            )
        finally:
            os.close(read_fd)
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
        )
    finally:
        lock.close()


if __name__ == "__main__":
    raise SystemExit(main())
