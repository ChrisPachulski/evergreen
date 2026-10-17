#!/usr/bin/env python3
"""Build bounded documentation context from one exact Git commit and change manifest."""

import argparse
import hashlib
import json
from pathlib import Path, PurePosixPath
import random
import re
import secrets
import sys
import time

try:
    from .bounded_process import run_bounded
    from .path_policy import is_protocol_path
except ImportError:  # Direct script execution.
    from bounded_process import run_bounded
    from path_policy import is_protocol_path


SCHEMA_VERSION = 2
MAX_DOC_LIST_BYTES = 1024 * 1024
MAX_DOC_FILES = 1000
MAX_BLOB_BYTES = 1024 * 1024
MAX_TOTAL_SCAN_BYTES = 8 * 1024 * 1024
MAX_TERMS = 1000
MAX_CANDIDATES = 200
MAX_OUTPUT_BYTES = 120_000
MAX_ERRORS = 100
TIMEOUT_SECONDS = 3
CONTEXT_LINES = 2
CLAIM_ID_HEX = 16
MAX_CONTROL_TEXT = 500
EXEMPT_DOC_PARTS = {
    "adr", "adrs", "archive", "archives", "audit", "audits", "plans", "readiness",
    "roadmaps", "specs",
}
DATED_DOC_RE = re.compile(r"^\d{4}-\d{2}-\d{2}(?:[-_.]|$)")


def encode_context(context: dict) -> bytes:
    return json.dumps(
        context, ensure_ascii=True, separators=(",", ":"), sort_keys=True
    ).encode("ascii")


def _empty(head: str) -> dict:
    return {
        "schema_version": SCHEMA_VERSION,
        "head": head,
        "candidates": [],
        "claims": [],
        "truncated": False,
        "errors": [],
    }


def claim_id(*parts: str) -> str:
    """Return the opaque, stable id of one claim; no part may contain NUL except the last."""
    return hashlib.sha256("\0".join(parts).encode("utf-8")).hexdigest()[:CLAIM_ID_HEX]


def _git(repo: Path, deadline: float, limit: int, *args: str) -> tuple[bytes | None, str | None]:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        return None, "review context exceeded wall-clock limit"
    status, output, error = run_bounded(
        ["git", "--no-replace-objects", "-C", str(repo), *args],
        timeout_seconds=remaining,
        max_output_bytes=limit,
        clean_env=True,
        keep_env=[],
    )
    if status:
        return None, error or f"git {' '.join(args[:2])} failed"
    return output, None


def _living_doc(path: str) -> bool:
    pure = PurePosixPath(path)
    lower_parts = {part.casefold() for part in pure.parts}
    return (
        pure.suffix.casefold() in {".md", ".markdown", ".rst"}
        and not lower_parts & EXEMPT_DOC_PARTS
        and not pure.name.casefold().startswith("changelog")
        and DATED_DOC_RE.match(pure.name) is None
    )


def _terms(manifest: dict, context: dict) -> list[str]:
    if (
        not isinstance(manifest, dict)
        or manifest.get("schema_version") != 1
        or manifest.get("head") != context["head"]
        or manifest.get("truncated") is not False
        or manifest.get("errors") != []
        or not isinstance(manifest.get("files"), list)
        or not isinstance(manifest.get("contract_seeds"), list)
    ):
        context["errors"].append("change manifest is not complete and bound to the requested head")
        return []
    values = list(manifest["contract_seeds"])
    for item in manifest["files"]:
        if not isinstance(item, dict):
            context["errors"].append("change manifest contains an invalid file record")
            return []
        values.extend(item.get(name) for name in ("path", "old_path") if item.get(name) is not None)
    if any(not isinstance(value, str) or not value for value in values):
        context["errors"].append("change manifest contains an invalid search term")
        return []
    deduplicated = {}
    for value in values:
        deduplicated.setdefault(value.casefold(), value)
    terms = [deduplicated[key] for key in sorted(deduplicated)]
    if len(terms) > MAX_TERMS:
        context["truncated"] = True
        return terms[:MAX_TERMS]
    return terms


def _tree_docs(payload: bytes, context: dict) -> list[tuple[str, str, int]]:
    docs = []
    for raw in payload.split(b"\0"):
        if not raw:
            continue
        try:
            metadata, raw_path = raw.split(b"\t", 1)
        except ValueError:
            context["errors"].append("Git tree contains invalid documentation metadata")
            continue
        if not raw_path.lower().endswith((b".md", b".markdown", b".rst")):
            continue
        try:
            mode, kind, object_id, raw_size = metadata.decode("ascii").split()
            path = raw_path.decode("utf-8")
            size = int(raw_size)
        except (UnicodeError, ValueError):
            context["errors"].append("Git tree contains invalid documentation metadata")
            continue
        if not _living_doc(path):
            continue
        if not is_protocol_path(path):
            context["errors"].append(f"tracked documentation path is not citable: {path}")
            continue
        if mode == "120000":
            context["errors"].append(f"tracked documentation symlink is not reviewable: {path}")
            continue
        if kind != "blob" or size < 0:
            context["errors"].append(f"tracked documentation is not a file blob: {path}")
            continue
        docs.append((path, object_id, size))
    docs.sort(key=lambda item: item[0].encode("utf-8"))
    if len(docs) > MAX_DOC_FILES:
        context["truncated"] = True
        docs = docs[:MAX_DOC_FILES]
    return docs


def _excerpts(
    path: str, text: str, terms: list[str], deadline: float
) -> list[tuple[dict, list[dict]]]:
    lines = text.split("\n") if text else []
    if text.endswith("\n"):
        lines.pop()
    lines = [line[:-1] if line.endswith("\r") else line for line in lines]
    word = re.compile(r"[A-Za-z0-9_]")
    folded_terms = [
        (term, re.compile(
            (r"(?<![A-Za-z0-9_])" if word.match(term[0]) else "")    # boundary only on a word-character edge,
            + re.escape(term.casefold())                             # so "/v1/items" still matches inside a URL
            + (r"(?![A-Za-z0-9_])" if word.match(term[-1]) else "")
        ))
        for term in terms
    ]
    matching = []
    for index, line in enumerate(lines):
        if time.monotonic() > deadline:
            raise TimeoutError
        folded = line.casefold()
        for _term, needle in folded_terms:
            if time.monotonic() > deadline:
                raise TimeoutError
            if needle.search(folded):
                matching.append(index)
                break
    ranges = []
    for index in matching:
        start, end = max(0, index - CONTEXT_LINES), min(len(lines), index + CONTEXT_LINES + 1)
        if ranges and start <= ranges[-1][1]:
            ranges[-1][1] = max(ranges[-1][1], end)
            ranges[-1][2].append(index)
        else:
            ranges.append([start, end, [index]])
    result = []
    for start, end, claimed in ranges:
        excerpt = "\n".join(lines[start:end])
        folded = excerpt.casefold()
        matched = []
        for term, needle in folded_terms:
            if time.monotonic() > deadline:
                raise TimeoutError
            if needle.search(folded):
                matched.append(term)
        window = {
            "path": path,
            "start_line": start + 1,
            "end_line": end,
            "matched_terms": matched,
            "excerpt": excerpt,
        }
        # One claim per matching line; its id changes whenever the line's place or text does.
        claims = [
            {"id": claim_id(path, str(index + 1), lines[index]),
             "path": path, "line": index + 1, "text": lines[index]}
            for index in claimed
        ]
        result.append((window, claims))
    return result


def _fit(context: dict, candidate: dict, claims: list[dict]) -> bool:
    if len(context["candidates"]) >= MAX_CANDIDATES:
        context["truncated"] = True
        return False
    context["candidates"].append(candidate)
    context["claims"].extend(claims)
    if len(encode_context(context)) + 1 <= MAX_OUTPUT_BYTES:
        return True
    context["candidates"].pop()
    del context["claims"][len(context["claims"]) - len(claims):]
    context["truncated"] = True
    return False


def build_context(repo: Path, head: str, manifest: dict) -> dict:
    repo = Path(repo).resolve()
    context = _empty(head)
    terms = _terms(manifest, context)
    if context["errors"] or context["truncated"] or not terms:
        return context
    deadline = time.monotonic() + TIMEOUT_SECONDS
    resolved, error = _git(repo, deadline, 256, "rev-parse", "--verify", f"{head}^{{commit}}")
    if error or resolved is None or resolved.decode("ascii", "replace").strip() != head:
        context["errors"].append(error or "requested head is not one exact commit")
        return context
    tree, error = _git(
        repo, deadline, MAX_DOC_LIST_BYTES,
        "ls-tree", "-r", "-z", "-l", "--full-tree", head,
    )
    if error or tree is None:
        context["errors"].append(error or "could not enumerate documentation at head")
        return context
    docs = _tree_docs(tree, context)
    scanned = 0
    for path, object_id, size in docs:
        if context["errors"]:
            break
        if time.monotonic() > deadline:
            context["errors"].append("review context exceeded wall-clock limit")
            break
        if size > MAX_BLOB_BYTES or scanned + size > MAX_TOTAL_SCAN_BYTES:
            context["truncated"] = True
            break
        payload, error = _git(repo, deadline, size + 1, "cat-file", "blob", object_id)
        if error or payload is None:
            context["errors"].append(error or f"could not read documentation blob: {path}")
            break
        if len(payload) != size:
            context["errors"].append(f"documentation blob size changed unexpectedly: {path}")
            break
        scanned += size
        try:
            text = payload.decode("utf-8")
        except UnicodeDecodeError:
            context["errors"].append(f"documentation is not UTF-8: {path}")
            break
        try:
            excerpts = _excerpts(path, text, terms, deadline)
        except TimeoutError:
            context["errors"].append("review context exceeded wall-clock limit")
            break
        for candidate, claims in excerpts:
            if not _fit(context, candidate, claims):
                break
        if context["truncated"]:
            break
    if len(context["errors"]) > MAX_ERRORS or len(encode_context(context)) + 1 > MAX_OUTPUT_BYTES:
        context["candidates"] = []
        context["claims"] = []
        context["errors"] = ["review context errors exceed output bound"]
        context["truncated"] = True
    return context


def add_controls(context: dict, nonce: str) -> tuple[dict, dict]:
    """Mix two calibration controls into a complete context's claim list.

    Both controls restate a documentation line the context already carries from the head
    commit. The negative control quotes its line exactly, so its correct verdict is certified;
    the positive control attributes another supplied line's text to it, so its correct verdict
    is drift. Every id in the mixed list, real or control, is rekeyed with the nonce, so no id
    can be recomputed from a claim's public fields, and the whole list is shuffled from the
    same nonce, so neither position nor id marks a control. The controls' "reads exactly:"
    form still does. Return the new context and the trusted answer key, or raise ValueError
    when the evidence cannot carry both controls.
    """
    if context["errors"] or context["truncated"]:
        raise ValueError("calibration controls need a complete review context")
    rng = random.Random(nonce)
    sources = [claim for claim in context["claims"] if len(claim["text"]) <= MAX_CONTROL_TEXT]
    quotes = sorted({
        line.strip()
        for window in context["candidates"]
        for line in window["excerpt"].split("\n")
        if line.strip() and len(line) <= MAX_CONTROL_TEXT
    })
    if not sources:
        raise ValueError("no supplied documentation line can carry a calibration control")
    negative, positive = rng.choice(sources), rng.choice(sources)
    others = [quote for quote in quotes if quote != positive["text"].strip()]
    if not others:
        raise ValueError("no second supplied documentation line can contradict a control")

    def keyed(path: str, line: int, text: str) -> dict:
        return {"id": claim_id(nonce, path, str(line), text), "path": path, "line": line,
                "text": text}

    def control(source: dict, quote: str) -> dict:
        statement = f"{source['path']} line {source['line']} reads exactly: {quote}"
        return keyed(source["path"], source["line"], statement)

    controls = [control(negative, negative["text"]), control(positive, rng.choice(others))]
    claims = [
        *(keyed(claim["path"], claim["line"], claim["text"]) for claim in context["claims"]),
        *controls,
    ]
    if len({claim["id"] for claim in claims}) != len(claims):
        raise ValueError("calibration control id collides with a claim id")
    rng.shuffle(claims)
    mixed = dict(context, claims=claims)
    if len(encode_context(mixed)) + 1 > MAX_OUTPUT_BYTES:
        raise ValueError("calibration controls exceed the review context output bound")
    return mixed, {"negative": controls[0]["id"], "positive": controls[1]["id"]}


def _controls_main(args: argparse.Namespace) -> int:
    try:
        context = json.load(sys.stdin)
        if not isinstance(context, dict) or context.get("head") != args.head:
            raise ValueError("review context is not bound to the requested head")
        mixed, key = add_controls(context, secrets.token_hex(16))
        args.add_controls.write_text(json.dumps(key, sort_keys=True), encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError, AttributeError, RecursionError):
        return 2
    sys.stdout.buffer.write(encode_context(mixed) + b"\n")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", type=Path, required=True)
    parser.add_argument("--head", required=True)
    parser.add_argument(
        "--add-controls", type=Path, metavar="KEY_FILE",
        help="read a built context on stdin, mix in calibration controls, write the answer key",
    )
    args = parser.parse_args()
    if args.add_controls is not None:
        return _controls_main(args)
    try:
        manifest = json.load(sys.stdin)
    except (json.JSONDecodeError, RecursionError):
        manifest = None
    context = build_context(args.repo, args.head, manifest)
    sys.stdout.buffer.write(encode_context(context) + b"\n")
    return 2 if context["truncated"] or context["errors"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
