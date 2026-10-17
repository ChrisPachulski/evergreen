#!/usr/bin/env python3
"""Parse and validate one Evergreen CI result envelope."""

import json
from pathlib import Path
import re
import time

try:
    from .bounded_process import OUTPUT_EXIT, TIMEOUT_EXIT, run_bounded
    from .path_policy import MAX_PATH, is_protocol_path
except ImportError:  # Direct script execution.
    from bounded_process import OUTPUT_EXIT, TIMEOUT_EXIT, run_bounded
    from path_policy import MAX_PATH, is_protocol_path


SCHEMA_VERSION = 3
MAX_ITEMS = 100
MAX_TEXT = 4096
MAX_RUNTIME_TEXT = 256
MAX_ID = 64
MAX_REPORTED_IDS = 5
MAX_CITATION_BYTES = 1_048_576
CITATION_TIMEOUT_SECONDS = 3
MAX_CITATION_GIT_CALLS = 600

# The model returns one verdict per trusted claim id; it never reports counts. The validator
# derives the claims object from the verdicts and adds it, and the controls grades when the
# run carried calibration controls, only to a result that validated.
RESULT_FIELDS = {
    "schema_version", "status", "base", "head", "verdicts", "findings",
    "unverified", "errors", "runtime",
}
VERDICT_FIELDS = {"id", "verdict"}
VERDICTS = {"certified", "drift", "unverified"}
FINDING_FIELDS = {
    "id", "severity", "category", "rung", "doc_path", "doc_line", "claim",
    "code_path", "code_line", "why", "fix_or_flag",
}
UNVERIFIED_FIELDS = {"id", "doc_path", "doc_line", "claim", "reason"}
CONTROL_ANSWERS = {"positive": "drift", "negative": "certified"}
RUNTIME_FIELDS = {"provider", "model", "cli_version"}
STATUSES = {"complete", "inconclusive"}
SEVERITIES = {"high", "med", "low"}
CATEGORIES = {
    "in_docs_not_code", "name_mismatch", "in_code_not_docs", "release_identity_drift",
}
# Which ladder rung proved the finding, in ladder order. Category says what kind of
# disagreement it is; rung says how it was found, and the two are independent — an
# in_docs_not_code finding can come from a vanished path, a dead flag, or a prose read.
# `prose` is the only rung that rests on judgment rather than a mechanical check, which
# is what makes the distribution worth recording.
RUNGS = {"path", "contract", "snippet", "prose"}
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
    state: dict,
) -> tuple[list[str] | None, str | None]:
    status, entry, failure = _citation_git(
        ["git", "--no-replace-objects", "-C", str(repo), "ls-tree", "-z", head,
         "--", f":(literal){path}"],
        max_output_bytes=4096,
        state=state,
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

    status, content, failure = _citation_git(
        ["git", "--no-replace-objects", "-C", str(repo), "cat-file", "blob", object_id],
        max_output_bytes=MAX_CITATION_BYTES,
        state=state,
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


def _citation_git(
    command: list[str], *, max_output_bytes: int, state: dict
) -> tuple[int, bytes, str | None]:
    if state["failure"]:
        return TIMEOUT_EXIT, b"", state["failure"]
    remaining = state["deadline"] - time.monotonic()
    if remaining <= 0:
        state["failure"] = "citation validation timed out; aggregate wall-clock limit exceeded"
        return TIMEOUT_EXIT, b"", state["failure"]
    if state["calls"] >= MAX_CITATION_GIT_CALLS:
        state["failure"] = f"citation Git call limit exceeded ({MAX_CITATION_GIT_CALLS})"
        return TIMEOUT_EXIT, b"", state["failure"]
    state["calls"] += 1
    status, output, failure = run_bounded(
        command,
        timeout_seconds=remaining,
        max_output_bytes=max_output_bytes,
        clean_env=True,
        keep_env=[],
    )
    if status == TIMEOUT_EXIT:
        state["failure"] = "citation validation timed out; aggregate wall-clock limit exceeded"
        return status, output, state["failure"]
    return status, output, failure


def _head_lines(
    repo: Path,
    head: str,
    path: str,
    label: str,
    errors: list[str],
    cache: dict[str, tuple[list[str] | None, str | None]],
    state: dict,
) -> list[str] | None:
    if path not in cache:
        cache[path] = _head_blob(repo, head, path, state)
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
    state: dict,
    *,
    verify_claim: bool,
) -> None:
    path_key = f"{prefix}_path"
    line_key = f"{prefix}_line"
    path = _repo_path(item.get(path_key), path_key, repo, errors)
    line_ok = _integer(item.get(line_key), line_key, errors, positive=True)
    if path is None or not line_ok:
        return
    lines = _head_lines(repo, head, path, path_key, errors, cache, state)
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
    state: dict,
) -> None:
    label = f"findings[{index}]"
    if not _shape(item, FINDING_FIELDS, label, errors):
        return
    assert isinstance(item, dict)
    _text(item["id"], f"{label}.id", errors, limit=MAX_ID, single_line=True)
    _enum(item["severity"], SEVERITIES, f"{label}.severity", errors)
    _enum(item["category"], CATEGORIES, f"{label}.category", errors)
    _enum(item["rung"], RUNGS, f"{label}.rung", errors)
    _enum(item["fix_or_flag"], FIX_OR_FLAG, f"{label}.fix_or_flag", errors)
    _text(item["claim"], f"{label}.claim", errors, single_line=True)
    _text(item["why"], f"{label}.why", errors)
    _citation(item, "doc", repo, head, errors, cache, state, verify_claim=True)
    if not state["failure"]:
        _citation(item, "code", repo, head, errors, cache, state, verify_claim=False)


def _validate_unverified(
    item: object,
    index: int,
    repo: Path,
    head: str,
    errors: list[str],
    cache: dict[str, tuple[list[str] | None, str | None]],
    state: dict,
) -> None:
    label = f"unverified[{index}]"
    if not _shape(item, UNVERIFIED_FIELDS, label, errors):
        return
    assert isinstance(item, dict)
    _text(item["id"], f"{label}.id", errors, limit=MAX_ID, single_line=True)
    _text(item["claim"], f"{label}.claim", errors, single_line=True)
    _text(item["reason"], f"{label}.reason", errors)
    _citation(item, "doc", repo, head, errors, cache, state, verify_claim=True)


def _report_ids(errors: list[str], label: str, ids: list[str]) -> None:
    if ids:
        shown = ", ".join(ids[:MAX_REPORTED_IDS]) + (", ..." if len(ids) > MAX_REPORTED_IDS else "")
        errors.append(f"verdicts contain {len(ids)} {label} claim id(s): {shown}")


def _verdicts(value: object, claim_ids: list[str], errors: list[str]) -> dict[str, str] | None:
    """Map each trusted claim id to its one verdict, rejecting missing, repeated, or unknown ids."""
    if not isinstance(value, list):
        errors.append("verdicts must be an array")
        return None
    if len(value) > len(claim_ids) + MAX_ITEMS:
        errors.append(f"verdicts has {len(value)} entries for {len(claim_ids)} supplied claims")
        return None
    known = set(claim_ids)
    verdicts: dict[str, str] = {}
    unknown, repeated = [], []
    for index, item in enumerate(value):
        label = f"verdicts[{index}]"
        if not _shape(item, VERDICT_FIELDS, label, errors):
            continue
        assert isinstance(item, dict)
        id_ok = _text(item["id"], f"{label}.id", errors, limit=MAX_ID, single_line=True)
        if not _enum(item["verdict"], VERDICTS, f"{label}.verdict", errors) or not id_ok:
            continue
        if item["id"] not in known:
            unknown.append(item["id"])
        elif item["id"] in verdicts:
            repeated.append(item["id"])
        else:
            verdicts[item["id"]] = item["verdict"]
    _report_ids(errors, "unknown", unknown)
    _report_ids(errors, "duplicate", repeated)
    _report_ids(errors, "missing", [claim for claim in claim_ids if claim not in verdicts])
    return verdicts


def _without_controls(value: object, control_ids: set[str]) -> object:
    """Drop entries a model wrote for a calibration control; a control never cites head."""
    if not isinstance(value, list):
        return value
    return [
        item for item in value
        if not (isinstance(item, dict) and isinstance(item.get("id"), str)
                and item["id"] in control_ids)
    ]


def _bind(
    items: list,
    label: str,
    verdict: str,
    verdicts: dict[str, str],
    locations: dict[str, tuple[str, int]],
    errors: list[str],
) -> None:
    """Require one entry per claim with this verdict, citing that claim's own line, and no other."""
    expected = {claim for claim, value in verdicts.items() if value == verdict}
    cited = []
    for index, item in enumerate(items):
        if not isinstance(item, dict) or not isinstance(item.get("id"), str):
            continue  # its shape error is already reported
        cited.append(item["id"])
        if item["id"] not in expected:
            errors.append(f"{label}[{index}].id is not a supplied claim with verdict {verdict}")
        elif (item.get("doc_path"), item.get("doc_line")) != locations[item["id"]]:
            errors.append(f"{label}[{index}] does not cite its claim's documentation line")
    if len(cited) != len(set(cited)):
        errors.append(f"{label} repeat a claim id")
    missing = expected - set(cited)
    if missing:
        errors.append(f"{len(missing)} {verdict} verdict(s) have no matching {label} entry")


def validate_result(
    result: dict,
    repo: Path,
    expected_base: str,
    expected_head: str,
    *,
    claims: list[dict],
    controls: dict[str, str] | None = None,
) -> list[str]:
    """Return every protocol or HEAD-citation error in a result envelope.

    `claims` is the trusted claim inventory the driver built and supplied to the model, never
    anything read from model output; `controls` is the trusted calibration answer key, mapping
    "positive" and "negative" to the control ids mixed into that inventory. A result that
    validates is normalized in place: control entries are removed, the claims object is derived
    from the verdicts, and a run with controls gains their grades and turns inconclusive when
    either control was answered wrong.
    """
    errors: list[str] = []
    citation_cache: dict[str, tuple[list[str] | None, str | None]] = {}
    citation_state = {
        "deadline": time.monotonic() + CITATION_TIMEOUT_SECONDS,
        "calls": 0,
        "failure": None,
    }
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

    claim_ids = [claim["id"] for claim in claims]
    locations = {claim["id"]: (claim["path"], claim["line"]) for claim in claims}
    if controls is not None and (
        set(controls) != set(CONTROL_ANSWERS) or not set(controls.values()) <= set(claim_ids)
    ):
        errors.append("trusted calibration answer key does not match the claim inventory")
        return errors
    control_ids = set((controls or {}).values())
    verdicts = _verdicts(result["verdicts"], claim_ids, errors)
    findings = _without_controls(result["findings"], control_ids)
    unverified = _without_controls(result["unverified"], control_ids)
    for name, value in (("findings", findings), ("unverified", unverified),
                        ("errors", result["errors"])):
        if not isinstance(value, list):
            errors.append(f"{name} must be an array")
        elif len(value) > MAX_ITEMS:
            errors.append(f"{name} exceeds {MAX_ITEMS} items")

    if isinstance(findings, list) and len(findings) <= MAX_ITEMS:
        for index, item in enumerate(findings):
            _validate_finding(
                item, index, repo, expected_head, errors, citation_cache, citation_state
            )
            if citation_state["failure"]:
                break
    if (not citation_state["failure"] and isinstance(unverified, list) and
            len(unverified) <= MAX_ITEMS):
        for index, item in enumerate(unverified):
            _validate_unverified(
                item, index, repo, expected_head, errors, citation_cache, citation_state
            )
            if citation_state["failure"]:
                break
    reviewed = {}
    if verdicts is not None:
        reviewed = {claim: value for claim, value in verdicts.items() if claim not in control_ids}
        if isinstance(findings, list):
            _bind(findings, "findings", "drift", reviewed, locations, errors)
        if isinstance(unverified, list):
            _bind(unverified, "unverified", "unverified", reviewed, locations, errors)
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
    if errors:
        return errors

    assert verdicts is not None
    result["verdicts"] = [item for item in result["verdicts"] if item["id"] not in control_ids]
    result["findings"] = findings
    result["unverified"] = unverified
    counted = list(reviewed.values())
    result["claims"] = {
        "total": len(counted),
        **{name: counted.count(name) for name in sorted(VERDICTS)},
    }
    if controls is not None:
        grades = {
            role: "pass" if verdicts[control] == CONTROL_ANSWERS[role] else "fail"
            for role, control in sorted(controls.items())
        }
        result["controls"] = grades
        if "fail" in grades.values():
            result["status"] = "inconclusive"
            result["errors"] = [
                *result["errors"],
                "a calibration control was answered wrong, so the review did not track the "
                "supplied evidence",
            ]
    return errors


def load_validated_result(
    text: str,
    repo: Path,
    expected_base: str,
    expected_head: str,
    *,
    claims: list[dict],
    controls: dict[str, str] | None = None,
) -> tuple[dict | None, list[str]]:
    """Parse and validate text, returning no result when either step fails."""
    try:
        result = parse_result(text)
    except ValueError as error:
        return None, [str(error)]
    errors = validate_result(
        result, repo, expected_base, expected_head, claims=claims, controls=controls
    )
    return (None, errors) if errors else (result, [])
