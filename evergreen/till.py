"""Deterministic whole-surface inventory of publicly reachable declarations.

On a fixed tree with a fixed command line, the no-warning path is bitwise
reproducible; the two wall-time deadlines are the only machine-dependent
inputs, and they can only add a `truncated` warning -- flipping the run from
usable to fail-closed, never from one complete inventory to a different one.
"""

import errno
import os
import re
import stat
import time
from collections import Counter
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .impact import (
    DOC_SEARCH_TIMEOUT_SECONDS,
    MAX_DOC_LIST_BYTES,
    MAX_FILE_BYTES,
    TYPE_DECLARATION_KEYWORDS,
    _WarningCollector,
    _bounded_git_output,
    _bounded_regular_bytes,
    _masked_literals,
    _path,
    _reachable_declarations,
    _require_repository_root,
)

MAX_GAP_SOURCE_FILES = 10_000
MAX_GAP_SCAN_BYTES = 64 * 1024 * 1024
MAX_GAP_CANDIDATES = 50_000
MAX_GAP_SCOPE_PATHS = 1000
GAP_SCAN_TIMEOUT_SECONDS = 10

# Only suffixes the declaration scan actually parses. Adding a language is one entry here once
# the scan knows its declaration keywords -- and it may also need a branch in
# `_masked_literals` (or comments read as declarations) and a member pattern for methods.
GAP_SOURCE_SUFFIXES = frozenset((".py", ".go", ".rs", ".swift", ".sh", ".bash", ".java",
                                 ".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs"))
# Suffixes that plainly hold source the declaration scan cannot parse. Files wearing one are
# never inventoried, so their presence is surfaced as a scope note instead of vanishing silently.
UNPARSED_SOURCE_SUFFIXES = frozenset((
    ".kt", ".kts", ".scala", ".groovy", ".c", ".h", ".cc", ".cpp", ".hpp",
    ".cs", ".m", ".mm", ".rb", ".php", ".pl", ".lua", ".zsh", ".ps1",
    ".ex", ".exs", ".erl", ".hs", ".ml", ".clj", ".dart", ".r", ".jl", ".zig", ".nim",
))
EXEMPT_SOURCE_PARTS = {"tests", "test", "testdata", "vendor", "vendored",
                       "node_modules", "third_party"}
# Applied to the lowercased filename: test.py / test_x.py / conftest.py / x_test.go / x.test.ts
TEST_FILE_RE = re.compile(r"^(test|test_.*|conftest)\.[^.]+$|.*[._]test\.[^.]+$")
IDENTIFIER_RE = re.compile(rb"[A-Za-z_$][A-Za-z0-9_$]*")
# Only lines that pull a name across a file boundary count as usage: Python/JS/TS/Swift
# import lines, Rust `use` paths, export/re-export lists, and CommonJS require bindings.
# A bare identifier anywhere else is as likely a local, a kwarg, or another object's
# attribute as a reference to the declaration, and counting those ranked `key` above the
# package's real entry points. ponytail: Go imports packages, not symbols, so Go candidates
# ride the structural tie-break; symbol-level Go usage needs a package resolver.
IMPORT_LINE_RE = re.compile(
    rb"(?m)^[ \t]*(?:from|import|export|use|pub[ \t]+use)[ \t{(][^\r\n]*"
    rb"|^[^\r\n]*?(?<![A-Za-z0-9_$])require[ \t]*\([^\r\n]*"
)
# A parenthesised or braced import puts most of its names on continuation lines; reading only
# the opening line scored every one of them zero.
MAX_IMPORT_CONTINUATION_LINES = 200
_TYPE_KINDS = frozenset(keyword.decode("ascii") for keyword in TYPE_DECLARATION_KEYWORDS)
MAX_NAMED_EXCLUSIONS = 5
SHEBANG_SNIFF_BYTES = 128
SCAN_DEADLINE_WARNING = f"source scan truncated (maximum {GAP_SCAN_TIMEOUT_SECONDS} seconds)"


@dataclass(frozen=True)
class GapCandidate:
    symbol: str
    kind: str
    path: str
    line: int
    rank: int


@dataclass(frozen=True)
class TillReport:
    candidates: tuple[GapCandidate, ...]
    source_files_in_scope: int
    source_files_scanned: int
    warnings: tuple[str, ...]


SHELL_SHEBANG_RE = re.compile(rb"(?<![A-Za-z0-9_])(?:bash|sh|dash|ksh)(?![A-Za-z0-9_])")


def _bracket_depth(segment):
    return (segment.count(b"(") - segment.count(b")") +
            segment.count(b"{") - segment.count(b"}"))


def _import_segments(masked):
    """Import statements, with the continuation lines of a bracketed name list folded in."""
    lines = masked.split(b"\n")
    index = 0
    while index < len(lines):
        match = IMPORT_LINE_RE.match(lines[index])
        if match is None:
            index += 1
            continue
        segment = match.group(0)
        depth = _bracket_depth(segment)
        consumed = 0
        while (depth > 0 and index + 1 < len(lines) and
               consumed < MAX_IMPORT_CONTINUATION_LINES):
            index += 1
            consumed += 1
            segment += b"\n" + lines[index]
            depth += _bracket_depth(lines[index])
        yield segment
        index += 1


def _script_suffix(path):
    """First-line sniff of an extensionless file: which inventory bucket is this script?

    Returns ".py" for a Python shebang and ".sh" for a shell one (the file joins the scanned
    inventory -- a CLI entry point is public surface), "" for any other shebang or a symlinked
    script (surfaced as outside inventory), and None for plain data. The single O_NOFOLLOW /
    O_NONBLOCK open with a post-open fstat is the whole check: no stat-then-open window for
    a swapped symlink or FIFO to exploit, matching `_bounded_regular_bytes`.
    """
    flags = os.O_RDONLY | getattr(os, "O_NONBLOCK", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(path, flags)
    except OSError as error:
        # A symlinked script is still tracked source: refuse to follow it, never drop it.
        if error.errno in (errno.ELOOP, errno.EMLINK):
            return ""
        return None
    try:
        if not stat.S_ISREG(os.fstat(descriptor).st_mode):
            return None
        head = os.read(descriptor, SHEBANG_SNIFF_BYTES)
    except OSError:
        return None
    finally:
        os.close(descriptor)
    if not head.startswith(b"#!"):
        return None
    first = head.split(b"\n", 1)[0]
    if b"python" in first:
        return ".py"
    return ".sh" if SHELL_SHEBANG_RE.search(first) else ""


def _named_exclusions(paths):
    """Name what was excluded -- a bare count hid that the CLI itself was outside inventory."""
    shown = sorted(paths)[:MAX_NAMED_EXCLUSIONS]
    remainder = len(paths) - len(shown)
    return ", ".join(shown) + (f" and {remainder} more" if remainder else "")


def _admissible(pure):
    """Shared inventory filter: exempt directory parts and test-file names never count."""
    if {part.lower() for part in pure.parts} & EXEMPT_SOURCE_PARTS:
        return False
    return not TEST_FILE_RE.match(pure.name.lower())


def _script_bucket(path, repo, kind):
    """Bucket an extensionless path by shebang: (suffix, checked) joins the inventory;
    ("other", None) is an unparsed or unresolvable script, outside inventory; (None, None)
    is data."""
    try:
        checked = _path(path, repo, kind)
    except ValueError:
        return "other", None  # cannot prove it is not source: surface it
    script = _script_suffix(repo / checked)
    if script in GAP_SOURCE_SUFFIXES:
        return script, checked
    return ("other", None) if script is not None else (None, None)


def _scope_hits(path, scope_set):
    """Every scope entry admitting this path, by ancestor lookup instead of a scope scan."""
    hits = [path] if path in scope_set else []
    index = path.find("/")
    while index != -1:
        prefix = path[:index]
        if prefix in scope_set:
            hits.append(prefix)
        index = path.find("/", index + 1)
    return hits


def till(repo: Path, scope: tuple[str, ...] = ()) -> TillReport:
    """Inventory every declaration reachable from outside its file, ranked and gapless."""
    repo = Path(repo)
    if len(scope) > MAX_GAP_SCOPE_PATHS:
        # Every scope entry costs validation syscalls and per-file match work; an unbounded
        # list is unbounded memory and time, so it fails loudly instead of shrinking silently.
        raise ValueError(f"too many scope paths (maximum {MAX_GAP_SCOPE_PATHS})")
    scope = tuple(sorted({_path(entry, repo, "scope path") for entry in scope}))
    scope_set = set(scope)
    collector = _WarningCollector()

    # A non-root repo argument is an error, never a silently narrowed scan.
    _require_repository_root(repo)

    # One deadline covers enumeration and scan alike: per-file shebang probes are filesystem
    # work too, and leaving them outside the budget left the wall time unbounded.
    deadline = time.monotonic() + GAP_SCAN_TIMEOUT_SECONDS

    # Unfiltered listing on purpose: files the parser cannot cover must still be seen so the
    # report can say the inventory skipped them instead of posing as complete.
    payload, truncated = _bounded_git_output(repo, ["ls-files", "-z"])
    if payload is None or truncated:
        # An unenumerable scope is an incomplete inventory -- fail closed, never 0/0 silently.
        collector.add(
            f"source file list truncated (git ls-files failed or exceeded "
            f"{MAX_DOC_LIST_BYTES} bytes / {DOC_SEARCH_TIMEOUT_SECONDS} seconds)"
        )
    if payload is None:
        return TillReport((), 0, 0, collector.result())

    files = []
    unparsed = []
    exempted = 0
    matched_scopes = set()
    for encoded in payload.split(b"\0"):
        if not encoded:
            continue
        try:
            path = encoded.decode("utf-8")
            pure = PurePosixPath(path)
        except UnicodeError:
            # A name we cannot decode is a file we cannot scan -- fail closed, never silent.
            collector.add(f"source file list truncated: undecodable tracked path {encoded!r}")
            continue
        if scope:
            hits = _scope_hits(path, scope_set)
            if not hits:
                continue
            matched_scopes.update(hits)
        suffix = pure.suffix.lower()
        if not _admissible(pure):
            # Counted, not just skipped: a shell script under tests/ was absent from the
            # inventory and from the exclusion accounting alike, so nothing said it existed.
            if suffix in GAP_SOURCE_SUFFIXES or suffix in UNPARSED_SOURCE_SUFFIXES:
                exempted += 1
            continue
        if suffix in GAP_SOURCE_SUFFIXES:
            try:
                files.append((_path(path, repo, "tracked source"), suffix))
            except ValueError:
                # Escaping symlink, over-deep, or unresolvable: an in-scope file the scan
                # cannot admit makes the inventory incomplete.
                collector.add(f"source scan truncated: could not resolve {path}")
        elif suffix in UNPARSED_SOURCE_SUFFIXES:
            unparsed.append(path)
        elif not suffix:
            if time.monotonic() >= deadline:
                collector.add(SCAN_DEADLINE_WARNING)
                break
            bucket, checked = _script_bucket(path, repo, "tracked file")
            if bucket in GAP_SOURCE_SUFFIXES:
                # A shebang script is the CLI surface itself; excluding it made the tool
                # blind to its own primary documented entry point.
                files.append((checked, bucket))
            elif bucket:
                unparsed.append(path)
    if scope and not truncated:
        for entry in scope:
            if entry not in matched_scopes:
                # A scope that matches nothing is a typo until proven otherwise; a clean 0/0
                # report would read as "no gaps" and bury the mistake.
                raise ValueError(f"scope path matches no tracked file: {entry}")
    if unparsed:
        collector.add(
            f"{len(unparsed)} tracked source file(s) outside inventory "
            f"(unparsed language or no suffix): {_named_exclusions(unparsed)}"
        )
    if exempted:
        collector.add(
            f"{exempted} tracked source file(s) excluded by the "
            "tests/vendor/node_modules/third_party rule"
        )

    # Unfiltered on purpose: glob-filtering this listing silently dropped untracked Java/C++,
    # uppercase extensions, and extensionless scripts from the exclusion accounting.
    untracked_payload, untracked_truncated = _bounded_git_output(
        repo, ["ls-files", "-z", "--others", "--exclude-standard"],
    )
    if untracked_payload is None or untracked_truncated:
        collector.add(
            "untracked source audit truncated (git ls-files --others failed or exceeded "
            f"{MAX_DOC_LIST_BYTES} bytes / {DOC_SEARCH_TIMEOUT_SECONDS} seconds)"
        )
    else:
        untracked = []
        for encoded in untracked_payload.split(b"\0"):
            if not encoded:
                continue
            try:
                path = encoded.decode("utf-8")
                pure = PurePosixPath(path)
            except UnicodeError:
                untracked.append(ascii(encoded))
                continue
            if not _admissible(pure):
                continue
            if scope and not _scope_hits(path, scope_set):
                continue
            suffix = pure.suffix.lower()
            if suffix in GAP_SOURCE_SUFFIXES or suffix in UNPARSED_SOURCE_SUFFIXES:
                untracked.append(path)
            elif not suffix:
                if time.monotonic() >= deadline:
                    collector.add(SCAN_DEADLINE_WARNING)
                    break
                if _script_bucket(path, repo, "untracked file")[0]:
                    untracked.append(path)
        if untracked:
            collector.add(
                f"{len(untracked)} untracked source file(s) outside inventory "
                f"(tracked files only): {_named_exclusions(untracked)}"
            )

    files = sorted(set(files))
    if scope and not files:
        # A scope that matched only exempt/unparsed content is not a clean 0/0 -- it is zero
        # files that were ever eligible, and must say so instead of reading as "no gaps".
        collector.add(
            "scope matched no inventoriable source (only test/exempt paths, "
            "unparsed languages, or unresolvable entries)"
        )
    source_files_in_scope = len(files)
    if len(files) > MAX_GAP_SOURCE_FILES:
        collector.add(f"source files truncated (maximum {MAX_GAP_SOURCE_FILES} files)")
        files = files[:MAX_GAP_SOURCE_FILES]

    rows = []
    totals = Counter()
    declared = Counter()
    scanned = 0
    total_bytes = 0
    for path, suffix in files:
        if total_bytes >= MAX_GAP_SCAN_BYTES:
            collector.add(f"source scan truncated (maximum {MAX_GAP_SCAN_BYTES} bytes)")
            break
        if time.monotonic() >= deadline:
            collector.add(SCAN_DEADLINE_WARNING)
            break
        content = _bounded_regular_bytes(repo / path, MAX_FILE_BYTES)
        if content is None:
            # Oversize, symlink-swapped, vanished, unreadable: never a silent skip.
            collector.add(f"source scan truncated: could not read {path}")
            continue
        scanned += 1
        # May overshoot the budget by < MAX_FILE_BYTES; content-driven, deterministic.
        total_bytes += len(content)
        # Import-line mentions across the corpus are the impact signal: a symbol other files
        # pull across their file boundary outranks one nothing imports. Masking first keeps
        # comment prose and string bodies from posing as import lines.
        masked = _masked_literals(content, suffix)
        tokens = Counter()
        for segment in _import_segments(masked):
            tokens.update(IDENTIFIER_RE.findall(segment))
        totals.update(tokens)
        names = set()
        for line, depth, kind, name in _reachable_declarations(masked, suffix):
            tier = 0 if kind in _TYPE_KINDS else 1
            rows.append((depth, tier, path.lower(), path, line, name, kind))
            names.add(name.encode("ascii", "replace"))
        for name in names:
            # A file's own export/import lines never vouch for its own declarations --
            # otherwise forty `main` functions all ride one shared corpus total.
            declared[name] += tokens[name]
        if len(rows) > MAX_GAP_CANDIDATES:
            # Stop before accumulation outgrows the cap by more than one file's worth; a
            # truncated inventory fails closed either way, so bounded work wins.
            collector.add(
                f"candidates truncated (maximum {MAX_GAP_CANDIDATES} candidates)"
            )
            break

    def rank_key(row):
        depth, tier, lower, path, line, name, _kind = row
        key = name.encode("ascii", "replace")
        # Imports from outside every declaring file rank first -- the tightest usage signal
        # available without a call graph. Scope depth, then the type-before-callable tier,
        # break ties; within a tier, path/line order is positional on purpose.
        references = totals[key] - declared[key]
        return (-references, depth, tier, lower, path, line)

    rows.sort(key=rank_key)
    del rows[MAX_GAP_CANDIDATES:]
    candidates = tuple(
        GapCandidate(symbol=name, kind=kind, path=path, line=line, rank=rank)
        for rank, (_depth, _tier, _lower, path, line, name, kind) in enumerate(rows, 1)
    )
    return TillReport(candidates, source_files_in_scope, scanned, collector.result())
