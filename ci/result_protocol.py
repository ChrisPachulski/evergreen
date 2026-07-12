#!/usr/bin/env python3
"""Parse and validate one Evergreen CI result envelope."""

import json
from pathlib import Path
import re

try:
    from .bounded_process import OUTPUT_EXIT, run_bounded
    from .path_policy import MAX_PATH, is_protocol_path
except ImportError:  # Direct script execution.
    from bounded_process import OUTPUT_EXIT, run_bounded
    from path_policy import MAX_PATH, is_protocol_path


SCHEMA_VERSION = 1
MAX_ITEMS = 100
MAX_TEXT = 4096
MAX_RUNTIME_TEXT = 256
MAX_CITATION_BYTES = 1_048_576
CITATION_TIMEOUT_SECONDS = 3

RESULT_FIELDS = {
    "schema_version", "status", "base", "head", "claims", "findings",
    "unverified", "errors", "runtime",
}
CLAIM_FIELDS = {"total", "certified", "drift", "unverified"}
FINDING_FIELDS = {
    "severity", "category", "doc_path", "doc_line", "claim",
    "code_path", "code_line", "why", "fix_or_flag",
}
UNVERIFIED_FIELDS = {"doc_path", "doc_line", "claim", "reason"}
RUNTIME_FIELDS = {"provider", "model", "cli_version"}
STATUSES = {"complete", "inconclusive"}
SEVERITIES = {"high", "med", "low"}
CATEGORIES = {
    "in_docs_not_code", "name_mismatch", "in_code_not_docs", "release_identity_drift",
}
FIX_OR_FLAG = {"fix", "flag"}
FENCE_RE = re.compile(r"```evergreen-result[ \t]*\r?\n(.*?)\r?\n```", re.DOTALL)


def parse_result(text: str) -> dict:
    """Return the sole whole-output JSON object or fenced Evergreen envelope."""
    stripped = text.strip()
    try:
        value = json.loads(stripped)
    except (json.JSONDecodeError, TypeError):
        matches = FENCE_RE.findall(text)
        if len(matches) != 1:
            raise ValueError(
                "expected one evergreen-result envelope or one whole-output JSON object"
            ) from None
        try:
            value = json.loads(matches[0])
        except json.JSONDecodeError as error:
            raise ValueError(f"malformed evergreen-result JSON: {error.msg}") from None
    if not isinstance(value, dict):
        raise ValueError("result envelope must be a JSON object")
    return value


def _shape(value: object, fields: set[str], label: str, errors: list[str]) -> bool:
    if not isinstance(value, dict):
        errors.append(f"{label} must be an object")
        return False
    missing = fields - value.keys()
    unknown = value.keys() - fields
    if missing:
        errors.append(f"missing {label} fields: {', '.join(sorted(missing))}")
    if unknown:
        errors.append(f"unknown {label} fields: {', '.join(sorted(unknown))}")
    return not missing and not unknown


def _text(
    value: object,
    label: str,
    errors: list[str],
    *,
    limit: int = MAX_TEXT,
    single_line: bool = False,
) -> bool:
    if not isinstance(value, str) or not value.strip():
        errors.append(f"{label} must contain non-whitespace text")
        return False
    if "\x00" in value or any("\ud800" <= char <= "\udfff" for char in value):
        errors.append(f"{label} must be a non-empty string")
        return False
    if len(value) > limit:
        errors.append(f"{label} exceeds {limit} characters")
        return False
    if single_line and ("\n" in value or "\r" in value):
        errors.append(f"{label} must be one line")
        return False
    return True


def _integer(value: object, label: str, errors: list[str], *, positive: bool = False) -> bool:
    if isinstance(value, bool) or not isinstance(value, int):
        errors.append(f"{label} must be an integer")
        return False
    minimum = 1 if positive else 0
    if value < minimum:
        errors.append(f"{label} must be >= {minimum}")
        return False
    return True


def _enum(value: object, allowed: set[str], label: str, errors: list[str]) -> bool:
    if not isinstance(value, str) or value not in allowed:
        errors.append(f"{label} is invalid")
        return False
    return True


def _repo_path(value: object, label: str, repo: Path, errors: list[str]) -> str | None:
    if not _text(value, label, errors, limit=MAX_PATH, single_line=True):
        return None
    path = str(value)
    if not is_protocol_path(path):
        errors.append(f"{label} must be a normalized repository-relative path")
        return None
    return path


def _head_blob(
    repo: Path,
    head: str,
    path: str,
) -> tuple[list[str] | None, str | None]:
    status, entry, failure = run_bounded(
        ["git", "--no-replace-objects", "-C", str(repo), "ls-tree", "-z", head,
         "--", f":(literal){path}"],
        timeout_seconds=CITATION_TIMEOUT_SECONDS,
        max_output_bytes=4096,
        clean_env=True,
        keep_env=[],
    )
    if status or not entry:
        return None, failure or f"does not exist at head: {path}"
    try:
        record = entry.rstrip(b"\0")
        metadata, raw_path = record.split(b"\t", 1)
        mode, kind, object_id = metadata.decode("ascii").split()
        tree_path = raw_path.decode("utf-8")
    except (UnicodeDecodeError, ValueError):
        return None, f"has invalid Git tree metadata at head: {path}"
    if tree_path != path:
        return None, f"does not identify one exact Git tree object at head: {path}"
    if mode == "120000":
        return None, f"is a symlink at head: {path}"
    if kind != "blob":
        return None, f"is not a file blob at head: {path}"

    status, content, failure = run_bounded(
        ["git", "--no-replace-objects", "-C", str(repo), "cat-file", "blob", object_id],
        timeout_seconds=CITATION_TIMEOUT_SECONDS,
        max_output_bytes=MAX_CITATION_BYTES,
        clean_env=True,
        keep_env=[],
    )
    if status == OUTPUT_EXIT:
        return None, f"citation exceeds {MAX_CITATION_BYTES} bytes at head: {path}"
    if status:
        return None, failure or f"could not read file blob at head: {path}"
    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        return None, f"is not UTF-8 text at head: {path}"
    if not text:
        return [], None
    lines = text.split("\n")
    if text.endswith("\n"):
        lines.pop()
    return [line[:-1] if line.endswith("\r") else line for line in lines], None


def _head_lines(
    repo: Path,
    head: str,
    path: str,
    label: str,
    errors: list[str],
    cache: dict[str, tuple[list[str] | None, str | None]],
) -> list[str] | None:
    if path not in cache:
        cache[path] = _head_blob(repo, head, path)
    lines, failure = cache[path]
    if failure:
        errors.append(f"{label} {failure}")
    return lines


def _citation(
    item: dict,
    prefix: str,
    repo: Path,
    head: str,
    errors: list[str],
    cache: dict[str, tuple[list[str] | None, str | None]],
    *,
    verify_claim: bool,
) -> None:
    path_key = f"{prefix}_path"
    line_key = f"{prefix}_line"
    path = _repo_path(item.get(path_key), path_key, repo, errors)
    line_ok = _integer(item.get(line_key), line_key, errors, positive=True)
    if path is None or not line_ok:
        return
    lines = _head_lines(repo, head, path, path_key, errors, cache)
    line = item[line_key]
    if lines is None:
        return
    if line > len(lines):
        errors.append(f"{line_key} is outside {path} at head")
        return
    if verify_claim and isinstance(item.get("claim"), str) and item["claim"] not in lines[line - 1]:
        errors.append(f"claim does not occur on cited documentation line {path}:{line}")


def _validate_finding(
    item: object,
    index: int,
    repo: Path,
    head: str,
    errors: list[str],
    cache: dict[str, tuple[list[str] | None, str | None]],
) -> None:
    label = f"findings[{index}]"
    if not _shape(item, FINDING_FIELDS, label, errors):
        return
    assert isinstance(item, dict)
    _enum(item["severity"], SEVERITIES, f"{label}.severity", errors)
    _enum(item["category"], CATEGORIES, f"{label}.category", errors)
    _enum(item["fix_or_flag"], FIX_OR_FLAG, f"{label}.fix_or_flag", errors)
    _text(item["claim"], f"{label}.claim", errors, single_line=True)
    _text(item["why"], f"{label}.why", errors)
    _citation(item, "doc", repo, head, errors, cache, verify_claim=True)
    _citation(item, "code", repo, head, errors, cache, verify_claim=False)


def _validate_unverified(
    item: object,
    index: int,
    repo: Path,
    head: str,
    errors: list[str],
    cache: dict[str, tuple[list[str] | None, str | None]],
) -> None:
    label = f"unverified[{index}]"
    if not _shape(item, UNVERIFIED_FIELDS, label, errors):
        return
    assert isinstance(item, dict)
    _text(item["claim"], f"{label}.claim", errors, single_line=True)
    _text(item["reason"], f"{label}.reason", errors)
    _citation(item, "doc", repo, head, errors, cache, verify_claim=True)


def validate_result(
    result: dict,
    repo: Path,
    expected_base: str,
    expected_head: str,
) -> list[str]:
    """Return every protocol or HEAD-citation error in a result envelope."""
    errors: list[str] = []
    citation_cache: dict[str, tuple[list[str] | None, str | None]] = {}
    repo = Path(repo).resolve()
    if not _shape(result, RESULT_FIELDS, "result", errors):
        return errors

    if type(result["schema_version"]) is not int or result["schema_version"] != SCHEMA_VERSION:
        errors.append(f"schema_version must be {SCHEMA_VERSION}")
    _enum(result["status"], STATUSES, "status", errors)
    if result["base"] != expected_base:
        errors.append("base does not match requested commit")
    if result["head"] != expected_head:
        errors.append("head does not match requested commit")

    claims = result["claims"]
    claims_valid = _shape(claims, CLAIM_FIELDS, "claims", errors)
    if claims_valid:
        assert isinstance(claims, dict)
        counts_valid = all(
            _integer(claims[name], f"claims.{name}", errors)
            for name in sorted(CLAIM_FIELDS)
        )
        if counts_valid:
            if claims["total"] != claims["certified"] + claims["drift"] + claims["unverified"]:
                errors.append("claims.total must equal certified + drift + unverified")
            if isinstance(result["findings"], list) and claims["drift"] != len(result["findings"]):
                errors.append("claims.drift must equal findings count")
            if isinstance(result["unverified"], list) and claims["unverified"] != len(result["unverified"]):
                errors.append("claims.unverified must equal unverified count")

    for name in ("findings", "unverified", "errors"):
        value = result[name]
        if not isinstance(value, list):
            errors.append(f"{name} must be an array")
        elif len(value) > MAX_ITEMS:
            errors.append(f"{name} exceeds {MAX_ITEMS} items")

    if isinstance(result["findings"], list) and len(result["findings"]) <= MAX_ITEMS:
        for index, item in enumerate(result["findings"]):
            _validate_finding(item, index, repo, expected_head, errors, citation_cache)
    if isinstance(result["unverified"], list) and len(result["unverified"]) <= MAX_ITEMS:
        for index, item in enumerate(result["unverified"]):
            _validate_unverified(item, index, repo, expected_head, errors, citation_cache)
    if isinstance(result["errors"], list) and len(result["errors"]) <= MAX_ITEMS:
        for index, item in enumerate(result["errors"]):
            _text(item, f"errors[{index}]", errors)
        if result["status"] == "complete" and result["errors"]:
            errors.append("complete result must not contain errors")

    runtime = result["runtime"]
    if _shape(runtime, RUNTIME_FIELDS, "runtime", errors):
        assert isinstance(runtime, dict)
        for name in sorted(RUNTIME_FIELDS):
            _text(runtime[name], f"runtime.{name}", errors, limit=MAX_RUNTIME_TEXT, single_line=True)
    return errors


def load_validated_result(
    text: str,
    repo: Path,
    expected_base: str,
    expected_head: str,
) -> tuple[dict | None, list[str]]:
    """Parse and validate text, returning no result when either step fails."""
    try:
        result = parse_result(text)
    except ValueError as error:
        return None, [str(error)]
    errors = validate_result(result, repo, expected_base, expected_head)
    return (None, errors) if errors else (result, [])
