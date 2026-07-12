#!/usr/bin/env python3
"""Build a deterministic, bounded manifest of changes between two Git refs."""

import argparse
import json
from pathlib import Path, PurePosixPath
import re
import subprocess


SCHEMA_VERSION = 1
HUNK_RE = re.compile(r"(?ms)^@@ .*?(?=^@@ |\Z)")
SEED_RES = (
    re.compile(r"--[A-Za-z0-9][A-Za-z0-9_-]*"),
    re.compile(r"\b[A-Z][A-Z0-9_]{2,}\b"),
    re.compile(r"/[A-Za-z0-9][A-Za-z0-9._~!$&()*+,;=:@%/-]*"),
    re.compile(r"\b[A-Za-z_][A-Za-z0-9_]{2,}\b"),
)


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[bytes]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )


def _resolve(repo: Path, ref: str, label: str, errors: list[str]) -> str | None:
    result = _git(repo, "rev-parse", "--verify", f"{ref}^{{commit}}")
    if result.returncode:
        errors.append(f"invalid {label} ref: {ref}")
        return None
    return result.stdout.decode("utf-8", errors="replace").strip()


def _path(value: bytes) -> str:
    decoded = value.decode("utf-8", errors="replace")
    normalized = PurePosixPath(decoded).as_posix()
    return normalized[2:] if normalized.startswith("./") else normalized


def _changed_files(repo: Path, base: str, head: str, errors: list[str]) -> list[dict]:
    result = _git(repo, "diff", "--name-status", "-z", "--find-renames", base, head)
    if result.returncode:
        errors.append("git diff --name-status failed")
        return []

    fields = result.stdout.split(b"\0")
    if fields and not fields[-1]:
        fields.pop()
    files = []
    index = 0
    while index < len(fields):
        raw_status = fields[index].decode("utf-8", errors="replace")
        index += 1
        status = raw_status[:1]
        if status in {"R", "C"}:
            if index + 1 >= len(fields):
                errors.append("malformed git diff --name-status output")
                break
            old_path, path = _path(fields[index]), _path(fields[index + 1])
            index += 2
            files.append({"status": status, "old_path": old_path, "path": path, "hunks": []})
        else:
            if index >= len(fields):
                errors.append("malformed git diff --name-status output")
                break
            files.append({"status": status, "path": _path(fields[index]), "hunks": []})
            index += 1
    return sorted(files, key=lambda item: (item["path"], item.get("old_path", ""), item["status"]))


def _patch(repo: Path, base: str, head: str, file: dict, errors: list[str]) -> str:
    paths = [file["path"]]
    if "old_path" in file:
        paths.insert(0, file["old_path"])
    result = _git(repo, "diff", "--unified=3", "--no-ext-diff", "--no-color", base, head, "--", *paths)
    if result.returncode:
        errors.append(f"git diff failed for {file['path']}")
        return ""
    return result.stdout.decode("utf-8", errors="replace")


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


def build_manifest(
    repo: Path,
    base: str,
    head: str = "HEAD",
    max_bytes: int = 120000,
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
    base_sha = _resolve(repo, base, "base", errors)
    head_sha = _resolve(repo, head, "head", errors)
    if not base_sha or not head_sha:
        return manifest

    files = _changed_files(repo, base_sha, head_sha, errors)
    all_seeds: set[str] = set()
    remaining = max(0, max_bytes)
    exhausted = False
    for file in files:
        hunks = HUNK_RE.findall(_patch(repo, base_sha, head_sha, file, errors))
        all_seeds.update(_seeds(hunks))
        for hunk in hunks:
            size = len(hunk.encode("utf-8"))
            if exhausted or size > remaining:
                manifest["truncated"] = True
                exhausted = True
                continue
            file["hunks"].append(hunk)
            remaining -= size

    manifest["files"] = files
    manifest["contract_seeds"] = sorted(all_seeds)
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--base", required=True)
    parser.add_argument("--head", default="HEAD")
    parser.add_argument("--repo", type=Path, default=Path.cwd())
    parser.add_argument("--max-bytes", type=int, default=120000)
    args = parser.parse_args()
    manifest = build_manifest(args.repo, args.base, args.head, args.max_bytes)
    print(json.dumps(manifest, ensure_ascii=False, separators=(",", ":")))


if __name__ == "__main__":
    main()
