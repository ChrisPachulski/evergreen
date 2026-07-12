#!/usr/bin/env python3
"""Build a deterministic, bounded manifest of changes between two Git refs."""

import argparse
import base64
import json
import os
from pathlib import Path
import re
import time

try:
    from .bounded_process import OUTPUT_EXIT, TIMEOUT_EXIT, run_bounded
    from .path_policy import is_protocol_path
except ImportError:  # Direct script execution.
    from bounded_process import OUTPUT_EXIT, TIMEOUT_EXIT, run_bounded
    from path_policy import is_protocol_path


SCHEMA_VERSION = 1
MAX_FILES = 4096
DIFF_HEADER_ALLOWANCE = 16_384
DEFAULT_TIMEOUT_SECONDS = 15
HUNK_RE = re.compile(r"(?ms)^@@ .*?(?=^@@ |\Z)")
SEED_RES = (
    re.compile(r"--[A-Za-z0-9][A-Za-z0-9_-]*"),
    re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b"),
    re.compile(r"/[A-Za-z0-9][A-Za-z0-9._~!$&()*+,;=:@%/-]*"),
    re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b"),
)


def _git(repo: Path, deadline: float, limit: int, *args: str) -> tuple[int, bytes, str | None]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return TIMEOUT_EXIT, b"", "change manifest exceeded wall-clock limit"
    return run_bounded(
        ["git", "--no-replace-objects", "-C", str(repo), *args],
        timeout_seconds=remaining,
        max_output_bytes=limit,
        clean_env=True,
        keep_env=[],
    )


def _resolve(
    repo: Path, ref: str, label: str, errors: list[str], deadline: float
) -> str | None:
    status, output, error = _git(repo, deadline, 256, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if status:
        errors.append(error or f"invalid {label} ref: {ref}")
        return None
    return output.decode("utf-8", errors="replace").strip()


def _git_limited(
    repo: Path, deadline: float, limit: int, *args: str
) -> tuple[bytes, int, bool, str | None]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return b"", TIMEOUT_EXIT, False, "change manifest exceeded wall-clock limit"
    status, output, error = run_bounded(
        ["git", "--no-replace-objects", "-C", str(repo), *args],
        timeout_seconds=remaining,
        max_output_bytes=limit + 1,
        clean_env=True,
        keep_env=[],
        preserve_partial=True,
    )
    truncated = status == OUTPUT_EXIT or len(output) > limit
    return output[:limit], status, truncated, error


def _raw_path(value: bytes) -> bytes:
    return value[2:] if value.startswith(b"./") else value


def _display_path(raw: bytes, errors: list[str], seen: dict[str, bytes]) -> dict:
    try:
        display = raw.decode("utf-8")
        result = {"path": display}
    except UnicodeDecodeError:
        display = raw.decode("utf-8", errors="replace")
        result = {"path": display, "path_bytes_b64": base64.b64encode(raw).decode("ascii")}
        errors.append(f"path contains invalid UTF-8; display uses replacement characters: {display}")
    previous = seen.get(display)
    if not is_protocol_path(display):
        errors.append(f"changed path is not citable by the result protocol: {display}")
    if previous is not None and previous != raw:
        errors.append(f"path display collision after UTF-8 replacement: {display}")
    seen[display] = raw
    return result


def _changed_files(
    repo: Path,
    base: str,
    head: str,
    max_bytes: int,
    errors: list[str],
    deadline: float,
) -> tuple[list[dict], bool]:
    output, returncode, truncated, failure = _git_limited(
        repo,
        deadline,
        max(4096, min(1_000_000, max_bytes * 2)),
        "diff",
        "--name-status",
        "-z",
        "--find-renames",
        base,
        head,
    )
    if returncode and not truncated:
        errors.append(failure or "git diff --name-status failed")
        return [], truncated

    fields = output.split(b"\0")
    if output.endswith(b"\0"):
        fields.pop()
    elif fields:
        fields.pop()
    files = []
    index = 0
    while index < len(fields) and len(files) < MAX_FILES:
        raw_status = fields[index].decode("utf-8", errors="replace")
        index += 1
        status = raw_status[:1]
        if status in {"R", "C"}:
            if index + 1 >= len(fields):
                errors.append("malformed git diff --name-status output")
                break
            old_path, path = _raw_path(fields[index]), _raw_path(fields[index + 1])
            index += 2
            files.append({"status": status, "_old_path": old_path, "_path": path, "hunks": []})
        else:
            if index >= len(fields):
                errors.append("malformed git diff --name-status output")
                break
            files.append({"status": status, "_path": _raw_path(fields[index]), "hunks": []})
            index += 1
    if index < len(fields):
        truncated = True
    return sorted(
        files,
        key=lambda item: (item["_path"], item.get("_old_path", b""), item["status"]),
    ), truncated


def _patch(
    repo: Path,
    base: str,
    head: str,
    file: dict,
    limit: int,
    errors: list[str],
    deadline: float,
) -> tuple[str, bool]:
    paths = [os.fsdecode(file["_path"])]
    if "_old_path" in file:
        paths.insert(0, os.fsdecode(file["_old_path"]))
    output, returncode, truncated, failure = _git_limited(
        repo,
        deadline,
        limit,
        "diff",
        "--unified=3",
        "--no-ext-diff",
        "--no-color",
        base,
        head,
        "--",
        *paths,
    )
    if returncode and not truncated:
        display = file["_path"].decode("utf-8", errors="replace")
        errors.append(failure or f"git diff failed for {display}")
        return "", truncated
    return output.decode("utf-8", errors="replace"), truncated


def _seeds(hunks: list[str]) -> set[str]:
    seeds = set()
    for hunk in hunks:
        changed = "\n".join(
            line[1:]
            for line in hunk.splitlines()
            if line[:1] in {"+", "-"} and not line.startswith(("+++", "---"))
        )
        for pattern in SEED_RES:
            seeds.update(pattern.findall(changed))
    return seeds


def _bounded_seeds(candidates: set[str], max_bytes: int) -> tuple[list[str], bool]:
    seeds = []
    used = 2  # JSON list brackets
    for seed in sorted(candidates):
        encoded = json.dumps(seed, ensure_ascii=False).encode("utf-8")
        cost = len(encoded) + (1 if seeds else 0)
        if used + cost > max(2, max_bytes):
            return seeds, True
        seeds.append(seed)
        used += cost
    return seeds, False


def build_manifest(
    repo: Path,
    base: str,
    head: str = "HEAD",
    max_bytes: int = 120000,
    timeout_seconds: float = DEFAULT_TIMEOUT_SECONDS,
) -> dict:
    """Return changed files, bounded unified hunks, and deterministic contract seeds."""
    repo = Path(repo).resolve()
    errors: list[str] = []
    manifest = {
        "schema_version": SCHEMA_VERSION,
        "base": base,
        "head": head,
        "files": [],
        "contract_seeds": [],
        "truncated": False,
        "errors": errors,
    }
    deadline = time.monotonic() + timeout_seconds
    base_sha = _resolve(repo, base, "base", errors, deadline)
    head_sha = _resolve(repo, head, "head", errors, deadline)
    if not base_sha or not head_sha:
        return manifest

    files, names_truncated = _changed_files(
        repo, base_sha, head_sha, max_bytes, errors, deadline
    )
    manifest["truncated"] = names_truncated
    seed_candidates: set[str] = set()
    remaining = max(0, max_bytes)
    exhausted = False
    for file in files:
        if exhausted:
            manifest["truncated"] = True
            continue
        patch, capture_truncated = _patch(
            repo,
            base_sha,
            head_sha,
            file,
            remaining + DIFF_HEADER_ALLOWANCE,
            errors,
            deadline,
        )
        hunks = HUNK_RE.findall(patch)
        if capture_truncated and hunks:
            hunks.pop()  # the final captured hunk may be partial
        manifest["truncated"] = manifest["truncated"] or capture_truncated
        for hunk in hunks:
            size = len(hunk.encode("utf-8"))
            if size > remaining:
                manifest["truncated"] = True
                exhausted = True
                break
            file["hunks"].append(hunk)
            seed_candidates.update(_seeds([hunk]))
            remaining -= size

    seen_paths: dict[str, bytes] = {}
    for file in files:
        file.update(_display_path(file.pop("_path"), errors, seen_paths))
        if "_old_path" in file:
            old = _display_path(file.pop("_old_path"), errors, seen_paths)
            file["old_path"] = old.pop("path")
            if "path_bytes_b64" in old:
                file["old_path_bytes_b64"] = old["path_bytes_b64"]
    seeds, seeds_truncated = _bounded_seeds(seed_candidates, max_bytes)
    manifest["files"] = files
    manifest["contract_seeds"] = seeds
    manifest["truncated"] = manifest["truncated"] or seeds_truncated
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--max-bytes", type=int, default=120000)
    parser.add_argument("--timeout-seconds", type=float, default=DEFAULT_TIMEOUT_SECONDS)
    args = parser.parse_args()
    manifest = build_manifest(
        args.repo, args.base, args.head, args.max_bytes, args.timeout_seconds
    )
    print(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
