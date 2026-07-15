"""Experimental Python proof that an in-project test can bind an AST mutation.

The pilot executes project Python as the current user.  Use only repositories whose code is
trusted; its bounded environment is reproducibility hardening, not a security sandbox.
"""

import ast
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import subprocess
import sys
import tempfile

MAX_PROJECT_BYTES = 4 * 1024 * 1024
MAX_FILES = 1000
MAX_OUTPUT_BYTES = 4096
ADAPTER_PROTOCOL = "evergreen-oracle-pilot-result-v1"


class PilotError(ValueError):
    """The project cannot produce an exact, mechanically bound pilot derivation."""


def canonical(value):
    """Return deterministic JSON bytes for pilot identity comparisons."""
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True,
        ).encode()
    except (TypeError, ValueError, RecursionError):
        raise PilotError("pilot value is not canonical JSON") from None


def _sha(value):
    return hashlib.sha256(value).hexdigest()


def _safe_path(value):
    if not isinstance(value, str):
        raise PilotError("project path is invalid")
    path = PurePosixPath(value)
    if (path.is_absolute() or path.as_posix() != value or "\\" in value or
            any(part in ("", ".", "..") or part.startswith(".") for part in path.parts)):
        raise PilotError("project path is invalid")
    return value


def _git(repo, *arguments, maximum=MAX_PROJECT_BYTES):
    environment = {
        "GIT_CONFIG_GLOBAL": os.devnull,
        "GIT_CONFIG_NOSYSTEM": "1",
        "GIT_OPTIONAL_LOCKS": "0",
        "GIT_TERMINAL_PROMPT": "0",
        "LC_ALL": "C",
        "PATH": os.environ.get("PATH", ""),
    }
    try:
        result = subprocess.run(
            ["git", "--no-replace-objects", "-C", str(repo), *arguments],
            check=False, capture_output=True, stdin=subprocess.DEVNULL, timeout=20,
            env=environment,
        )
    except (OSError, subprocess.SubprocessError):
        raise PilotError("exact Git project could not be read") from None
    if result.returncode or len(result.stdout) > maximum or len(result.stderr) > MAX_OUTPUT_BYTES:
        raise PilotError("exact Git project could not be read")
    return result.stdout


def _tree(repo):
    commit = _git(repo, "rev-parse", "--verify", "HEAD^{commit}", maximum=256).decode().strip()
    if _git(repo, "status", "--porcelain=v1", "-z", "--untracked-files=all"):
        raise PilotError("exact Git project must be clean")
    records = []
    total = 0
    raw = _git(repo, "ls-tree", "-rz", "--full-tree", commit)
    for entry in raw.split(b"\0"):
        if not entry:
            continue
        try:
            metadata, raw_path = entry.split(b"\t", 1)
            mode, kind, object_id = metadata.decode("ascii").split(" ")
            path = _safe_path(raw_path.decode("utf-8"))
        except (UnicodeError, ValueError):
            raise PilotError("Git tree entry is invalid") from None
        if mode not in ("100644", "100755") or kind != "blob":
            raise PilotError("pilot accepts regular Git blobs only")
        content = _git(repo, "cat-file", "blob", object_id)
        total += len(content)
        records.append((path, content))
        if len(records) > MAX_FILES or total > MAX_PROJECT_BYTES:
            raise PilotError("Git project exceeds pilot bounds")
    if not records:
        raise PilotError("Git project is empty")
    return commit, tuple(records)


def _byte_offsets(source):
    lines = source.encode().splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    return offsets


def _span(source, node):
    offsets = _byte_offsets(source)
    try:
        # CPython AST columns are UTF-8 byte offsets, not Unicode character indexes.
        start = offsets[node.lineno - 1] + node.col_offset
        end = offsets[node.end_lineno - 1] + node.end_col_offset
    except (AttributeError, IndexError):
        raise PilotError("AST source span is unavailable") from None
    if end <= start:
        raise PilotError("AST source span is invalid")
    return start, end


def _binding(source_path, source_bytes, test_path, test_bytes):
    try:
        source_text = source_bytes.decode("utf-8")
        test_text = test_bytes.decode("utf-8")
        source_tree = ast.parse(source_text, filename=source_path)
        test_tree = ast.parse(test_text, filename=test_path)
    except (UnicodeError, SyntaxError):
        raise PilotError("project Python source is not parseable UTF-8") from None

    candidate_assertions = []
    for node in ast.walk(test_tree):
        if not (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and
                node.func.attr == "assertEqual" and len(node.args) == 2 and not node.keywords):
            continue
        actual, expected = node.args
        if (isinstance(actual, ast.Call) and isinstance(actual.func, ast.Name) and
                not actual.args and not actual.keywords and isinstance(expected, ast.Constant) and
                type(expected.value) is int):
            candidate_assertions.append((node, actual.func.id, expected.value))
    if len(candidate_assertions) != 1:
        raise PilotError("expected exactly one bound unittest assertion")

    production = []
    for function in (node for node in ast.walk(source_tree)
                     if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))):
        for node in ast.walk(function):
            if (isinstance(node, ast.Return) and isinstance(node.value, ast.Constant) and
                    type(node.value.value) is int):
                production.append((function.name, node.value, node.value.value))
    if len(production) != 1:
        raise PilotError("expected exactly one production mutation site")
    function_name, literal, baseline = production[0]

    assertion, asserted_function, expected = candidate_assertions[0]
    if asserted_function != function_name or expected != baseline:
        raise PilotError("assertion does not bind the production return")
    source_start, source_end = _span(source_text, literal)
    assertion_start, assertion_end = _span(test_text, assertion)
    assertion_bytes = test_bytes[assertion_start:assertion_end]
    assertion_id = _sha(
        test_path.encode() + b"\0" + str(assertion_start).encode() + b":" +
        str(assertion_end).encode() + b"\0" + assertion_bytes
    )
    return {
        "function_name": function_name,
        "baseline": baseline,
        "expected": expected,
        "source_span": (source_start, source_end),
        "assertion_span": (assertion_start, assertion_end),
        "assertion_id_sha256": assertion_id,
        "source_binding": {
            "path": source_path, "start": source_start, "end": source_end,
            "sha256": _sha(source_bytes[source_start:source_end]),
        },
        "assertion_binding": {
            "path": test_path, "start": assertion_start, "end": assertion_end,
            "sha256": _sha(assertion_bytes), "assertion_id_sha256": assertion_id,
        },
    }


def _materialize(root, records):
    for relative, content in records:
        target = root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(content)
        target.chmod(0o644)


def _phase(name, exit_code=0, stdout="", stderr=""):
    if len(stdout.encode()) > MAX_OUTPUT_BYTES or len(stderr.encode()) > MAX_OUTPUT_BYTES:
        raise PilotError("pilot phase output exceeds its bound")
    return {"name": name, "exit_code": exit_code, "stdout": stdout, "stderr": stderr}


def _run(argv, cwd):
    environment = {
        "HOME": str(cwd), "LC_ALL": "C", "PYTHONDONTWRITEBYTECODE": "1",
        "PYTHONHASHSEED": "0", "PYTHONNOUSERSITE": "1", "PATH": os.environ.get("PATH", ""),
    }
    try:
        result = subprocess.run(
            argv, cwd=cwd, env=environment, check=False, capture_output=True,
            stdin=subprocess.DEVNULL, timeout=20,
        )
    except (OSError, subprocess.SubprocessError):
        raise PilotError("pilot subprocess failed operationally") from None
    if len(result.stdout) > MAX_OUTPUT_BYTES or len(result.stderr) > MAX_OUTPUT_BYTES:
        raise PilotError("pilot subprocess output exceeds its bound")
    try:
        return result.returncode, result.stdout.decode(), result.stderr.decode()
    except UnicodeError:
        raise PilotError("pilot subprocess output is not UTF-8") from None


def _dependency_phase(records):
    # ponytail: this prototype supports flat local modules; add package-root discovery only when
    # a selected development fixture requires it.
    local = {PurePosixPath(path).stem for path, _content in records if path.endswith(".py")}
    allowed = set(sys.stdlib_module_names) | local
    for path, content in records:
        if not path.endswith(".py"):
            continue
        try:
            tree = ast.parse(content.decode("utf-8"), filename=path)
        except UnicodeError:
            return _phase("dependency-verify", 1, stderr="non-UTF-8 Python source\n")
        except SyntaxError:
            # Syntax validity belongs to the independently recorded whole-project compile phase.
            continue
        for node in ast.walk(tree):
            names = []
            if isinstance(node, ast.Import):
                names = [alias.name.split(".", 1)[0] for alias in node.names]
            elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                names = [node.module.split(".", 1)[0]]
            if any(name not in allowed for name in names):
                return _phase("dependency-verify", 1, stderr="non-offline dependency\n")
    return _phase("dependency-verify")


_TEST_RUNNER = """
import importlib, json, sys, unittest
sys.path.insert(0, sys.argv[1])
suite = unittest.defaultTestLoader.loadTestsFromName(sys.argv[2])
result = unittest.TestResult()
suite.run(result)
value = {"errors": len(result.errors), "failures": len(result.failures), "run": result.testsRun}
print(json.dumps(value, sort_keys=True, separators=(",", ":")))
raise SystemExit(0 if value == {"errors": 0, "failures": 0, "run": 1} else 1)
""".strip()

_OBSERVER = """
import hashlib, importlib, json, sys
sys.path.insert(0, sys.argv[1])
actual = getattr(importlib.import_module(sys.argv[2]), sys.argv[3])()
expected = int(sys.argv[4])
value = {
  "schema_version": 1,
  "kind": "evergreen-bound-assertion-observation",
  "assertion_id_sha256": sys.argv[5],
  "outcome": "pass" if actual == expected else "fail",
  "actual_sha256": hashlib.sha256(repr(actual).encode()).hexdigest(),
  "error_class": None if actual == expected else "AssertionError",
}
print(json.dumps(value, sort_keys=True, separators=(",", ":")))
""".strip()


def _execute(records, source_path, test_path, binding, variant):
    with tempfile.TemporaryDirectory(prefix="evergreen-python-pilot-") as temporary:
        root = Path(temporary)
        _materialize(root, records)
        phases = [_phase("materialize")]
        dependency = _dependency_phase(records)
        phases.append(dependency)
        if dependency["exit_code"]:
            raise PilotError("dependency verification failed")
        compile_result = _run(
            [sys.executable, "-I", "-m", "compileall", "-q", str(root)], root,
        )
        phases.append(_phase("compile", *compile_result))
        if compile_result[0]:
            raise PilotError(f"{variant} compile phase failed")
        test_module = PurePosixPath(test_path).with_suffix("").as_posix().replace("/", ".")
        source_module = PurePosixPath(source_path).with_suffix("").as_posix().replace("/", ".")
        test_id = f"{test_module}.ValueTests.test_value"
        selected = _run(
            [sys.executable, "-I", "-c", _TEST_RUNNER, str(root), test_id], root,
        )
        phases.append(_phase("selected-test", *selected))
        observed = _run([
            sys.executable, "-I", "-c", _OBSERVER, str(root), source_module,
            binding["function_name"], str(binding["expected"]),
            binding["assertion_id_sha256"],
        ], root)
        phases.append(_phase("bound-assertion", *observed))
        if observed[0] or observed[2]:
            raise PilotError("bound assertion observer failed")
        source_bytes = dict(records)[source_path]
        control = {
            "schema_version": 1,
            "protocol": "evergreen-oracle-pilot-control-v1",
            "assertion_id_sha256": binding["assertion_id_sha256"],
            "source_sha256": _sha(source_bytes),
        }
        control["control_sha256"] = _sha(canonical(control))
        return {
            "schema_version": 1,
            "protocol": ADAPTER_PROTOCOL,
            "control_sha256": control["control_sha256"],
            "assertion_id_sha256": binding["assertion_id_sha256"],
            "source_sha256": control["source_sha256"],
            "phases": phases,
        }


def run_pilot(repo, source_path, test_path):
    """Run the narrow ``ValueTests.test_value`` prototype without awarding a grade."""
    repo = Path(repo).resolve(strict=True)
    source_path = _safe_path(source_path)
    test_path = _safe_path(test_path)
    commit, base_records = _tree(repo)
    record_map = dict(base_records)
    if source_path not in record_map or test_path not in record_map:
        raise PilotError("bound source or test is absent from exact Git project")
    binding = _binding(source_path, record_map[source_path], test_path, record_map[test_path])
    start, end = binding["source_span"]
    source = record_map[source_path]
    mutant = source[:start] + str(binding["baseline"] + 1).encode() + source[end:]
    noop = source + b"\n# evergreen semantic noop pilot\n"
    variants = {}
    for name, changed in (("pristine", source), ("noop", noop), ("mutant", mutant)):
        records = tuple((path, changed if path == source_path else content)
                        for path, content in base_records)
        first = _execute(records, source_path, test_path, binding, name)
        second = _execute(records, source_path, test_path, binding, name)
        if canonical(first) != canonical(second):
            raise PilotError(f"{name} execution is not reproducible")
        variants[name] = first
    outcomes = [
        json.loads(variants[name]["phases"][4]["stdout"])["outcome"]
        for name in ("pristine", "noop", "mutant")
    ]
    exits = [variants[name]["phases"][3]["exit_code"]
             for name in ("pristine", "noop", "mutant")]
    if outcomes != ["pass", "pass", "fail"] or exits != [0, 0, 1]:
        raise PilotError("pilot variants violate the required in-situ relation")
    return {
        "schema_version": 1,
        "protocol": "evergreen-python-in-situ-pilot-v1",
        "fixture_commit": commit,
        "binding": {
            "source": binding["source_binding"],
            "assertion": binding["assertion_binding"],
        },
        **variants,
    }
