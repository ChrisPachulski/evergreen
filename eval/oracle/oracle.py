"""Finite executable documentation oracles with a fail-closed sandbox boundary."""

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import selectors
import secrets
import shutil
import signal
import subprocess
import tempfile
import time
from urllib.parse import urlsplit


LANGUAGES = ("python", "java", "typescript", "rust", "go")
ORACLE_KINDS = (
    "return-value", "raises", "default-value", "cardinality", "state-change",
)
LANGUAGE_ADAPTERS = {
    language: f"/opt/evergreen/bin/{language}-oracle-v1" for language in LANGUAGES
}
ORACLE_MISMATCH_EXIT = 42
MAX_SOURCE_BYTES = 1024 * 1024
MAX_DOCUMENTATION_BYTES = 256 * 1024
MAX_OBSERVABLE_BYTES = 4096
MAX_PROCESS_OUTPUT_BYTES = 64 * 1024
SANDBOX_TIMEOUT_SECONDS = 30
SANDBOX_PROFILE = "evergreen-oracle-v1"

_TOP_KEYS = {
    "schema_version", "kind", "group_id", "project", "source", "language",
    "documentation", "harness", "oracle", "mutation", "semantic_noop", "sandbox",
    "seed_sha256",
}
_SOURCE_KEYS = {"origin", "commit", "license", "path", "code", "sha256"}
_DOCUMENTATION_KEYS = {"template", "sha256"}
_HARNESS_KEYS = {"argv"}
_ORACLE_KEYS = {"kind", "expected_observable"}
_OBSERVABLE_KEYS = {"exit_code", "stdout"}
_MUTATION_KEYS = {"id", "offset", "before", "after", "derivative_sha256"}
_NOOP_KEYS = {"id", "derivative_sha256"}
_SANDBOX_KEYS = {"engine", "image", "profile"}
_HEX = re.compile(r"[0-9a-f]{64}")
_COMMIT = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_GROUP = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_PROJECT = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+")
_IMAGE = re.compile(r"[a-z0-9][a-z0-9._/-]*@sha256:[0-9a-f]{64}")
_FORBIDDEN_INPUT_KEYS = {"label", "verdict"}


class OracleError(ValueError):
    """The oracle declaration or result is invalid."""


class OracleOperationalError(OracleError):
    """A bounded local sandbox operation failed."""


def _canonical(value):
    try:
        return json.dumps(
            value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), sort_keys=True,
        ).encode()
    except (TypeError, ValueError, RecursionError):
        raise OracleError("oracle seed is not canonical JSON") from None


def seed_sha256(seed):
    """Return the identity of a seed without trusting its stored identity."""
    if not isinstance(seed, dict):
        raise OracleError("oracle seed must be an object")
    unsigned = {key: value for key, value in seed.items() if key != "seed_sha256"}
    return hashlib.sha256(_canonical(unsigned)).hexdigest()


def mismatch_stdout(kind):
    return json.dumps(
        {"kind": kind, "oracle": "mismatch"}, separators=(",", ":"), sort_keys=True,
    ) + "\n"


def semantic_noop_suffix(language):
    if language not in LANGUAGES:
        raise OracleError("oracle language is invalid")
    marker = "#" if language == "python" else "//"
    return f"\n{marker} evergreen semantic noop v1\n".encode()


def _exact_keys(value, keys, label):
    if not isinstance(value, dict) or set(value) != keys:
        raise OracleError(f"{label} fields are invalid")


def _text(value, label, maximum):
    if (type(value) is not str or not value or not value.strip() or
            len(value.encode()) > maximum or "\0" in value):
        raise OracleError(f"{label} is invalid")
    return value


def _hash(value, label):
    if type(value) is not str or not _HEX.fullmatch(value):
        raise OracleError(f"{label} SHA-256 is invalid")
    return value


def _safe_path(value):
    value = _text(value, "source path", 4096)
    pure = PurePosixPath(value)
    if (pure.is_absolute() or "\\" in value or "//" in value or
            pure.as_posix() != value or any(part in ("", ".", "..") for part in value.split("/"))):
        raise OracleError("source path must be normalized repository-relative POSIX")
    return value


def _forbid_answers(value):
    if isinstance(value, dict):
        for key, child in value.items():
            if key in _FORBIDDEN_INPUT_KEYS:
                raise OracleError("oracle input cannot supply a label or verdict")
            _forbid_answers(child)
    elif isinstance(value, list):
        for child in value:
            _forbid_answers(child)


def _mutated_source(seed):
    source = seed["source"]["code"].encode()
    mutation = seed["mutation"]
    before = mutation["before"].encode()
    after = mutation["after"].encode()
    offset = mutation["offset"]
    if offset > len(source) or source[offset:offset + len(before)] != before:
        raise OracleError("mutation does not identify the declared source bytes")
    return source[:offset] + after + source[offset + len(before):]


def validate_seed(seed):
    """Validate every seed field and every derived byte identity."""
    if not isinstance(seed, dict):
        raise OracleError("oracle seed must be an object")
    _forbid_answers(seed)
    _exact_keys(seed, _TOP_KEYS, "oracle seed")
    if type(seed["schema_version"]) is not int or seed["schema_version"] != 1:
        raise OracleError("oracle schema version is invalid")
    if seed["kind"] != "evergreen-executable-documentation-oracle":
        raise OracleError("oracle seed kind is invalid")
    if type(seed["group_id"]) is not str or not _GROUP.fullmatch(seed["group_id"]):
        raise OracleError("oracle group ID is invalid")
    if type(seed["project"]) is not str or not _PROJECT.fullmatch(seed["project"]):
        raise OracleError("oracle project is invalid")
    language = seed["language"]
    if language not in LANGUAGES:
        raise OracleError("oracle language is invalid")

    source = seed["source"]
    _exact_keys(source, _SOURCE_KEYS, "oracle source")
    parsed = urlsplit(_text(source["origin"], "source origin", 4096))
    if (parsed.scheme != "https" or not parsed.hostname or parsed.username is not None or
            parsed.password is not None or parsed.query or parsed.fragment):
        raise OracleError("source origin must be a credential-free HTTPS URL")
    if type(source["commit"]) is not str or not _COMMIT.fullmatch(source["commit"]):
        raise OracleError("source commit is invalid")
    _text(source["license"], "source license", 256)
    source_path = _safe_path(source["path"])
    code = _text(source["code"], "source code", MAX_SOURCE_BYTES).encode()
    if _hash(source["sha256"], "source") != hashlib.sha256(code).hexdigest():
        raise OracleError("source SHA-256 does not match source bytes")

    documentation = seed["documentation"]
    _exact_keys(documentation, _DOCUMENTATION_KEYS, "oracle documentation")
    template = _text(
        documentation["template"], "documentation template", MAX_DOCUMENTATION_BYTES,
    ).encode()
    if (_hash(documentation["sha256"], "documentation") !=
            hashlib.sha256(template).hexdigest()):
        raise OracleError("documentation SHA-256 does not match template bytes")

    harness = seed["harness"]
    _exact_keys(harness, _HARNESS_KEYS, "oracle harness")
    expected_argv = [LANGUAGE_ADAPTERS[language], f"/input/{source_path}"]
    if harness["argv"] != expected_argv:
        raise OracleError("harness argv is not the fixed language-allowlisted command")

    oracle = seed["oracle"]
    _exact_keys(oracle, _ORACLE_KEYS, "oracle")
    kind = oracle["kind"]
    if kind not in ORACLE_KINDS:
        raise OracleError("oracle kind is invalid")
    observable = oracle["expected_observable"]
    _exact_keys(observable, _OBSERVABLE_KEYS, "oracle observable")
    if type(observable["exit_code"]) is not int or observable["exit_code"] != 0:
        raise OracleError("oracle observable exit code is ambiguous")
    stdout = _text(observable["stdout"], "oracle observable stdout", MAX_OBSERVABLE_BYTES)
    if stdout == mismatch_stdout(kind):
        raise OracleError("oracle observable is ambiguous with the mismatch sentinel")

    mutation = seed["mutation"]
    _exact_keys(mutation, _MUTATION_KEYS, "oracle mutation")
    if mutation["id"] != f"{kind}-replace-v1":
        raise OracleError("mutation is unknown for the declared oracle kind")
    if type(mutation["offset"]) is not int or mutation["offset"] < 0:
        raise OracleError("mutation offset is invalid")
    before = _text(mutation["before"], "mutation before bytes", 256)
    after = _text(mutation["after"], "mutation after bytes", 256)
    if before == after:
        raise OracleError("mutation must change source bytes")
    derived = _mutated_source(seed)
    if (_hash(mutation["derivative_sha256"], "mutation derivative") !=
            hashlib.sha256(derived).hexdigest()):
        raise OracleError("mutation derivative SHA-256 does not match derived bytes")

    noop = seed["semantic_noop"]
    _exact_keys(noop, _NOOP_KEYS, "semantic no-op")
    if noop["id"] != "comment-v1":
        raise OracleError("semantic no-op mutation is unknown")
    noop_bytes = code + semantic_noop_suffix(language)
    if (_hash(noop["derivative_sha256"], "semantic no-op derivative") !=
            hashlib.sha256(noop_bytes).hexdigest()):
        raise OracleError("semantic no-op derivative SHA-256 does not match derived bytes")

    sandbox = seed["sandbox"]
    _exact_keys(sandbox, _SANDBOX_KEYS, "oracle sandbox")
    if sandbox["engine"] != "docker" or sandbox["profile"] != SANDBOX_PROFILE:
        raise OracleError("oracle sandbox profile is invalid")
    if type(sandbox["image"]) is not str or not _IMAGE.fullmatch(sandbox["image"]):
        raise OracleError("oracle sandbox image must be digest-addressed")
    _hash(seed["seed_sha256"], "seed")
    if seed["seed_sha256"] != seed_sha256(seed):
        raise OracleError("seed SHA-256 does not match seed bytes")


def _sandbox_command(seed, fixture, name, engine):
    mount = f"type=bind,src={fixture},dst=/input,readonly"
    return [
        str(engine), "run", "--pull=never", f"--name={name}", "--network=none",
        "--read-only", "--user=65534:65534", "--cap-drop=ALL",
        "--security-opt=no-new-privileges", "--pids-limit=64", "--memory=256m",
        "--cpus=1", "--tmpfs=/scratch:rw,nosuid,nodev,size=64m", "--workdir=/scratch",
        f"--mount={mount}", seed["sandbox"]["image"], *seed["harness"]["argv"],
    ]


def _stop_process(process):
    try:
        os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError:
        if process.poll() is None:
            process.kill()
    process.wait()


def _bounded_container(command, environment, timeout=SANDBOX_TIMEOUT_SECONDS):
    try:
        process = subprocess.Popen(
            command, env=environment, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            start_new_session=True,
        )
    except OSError:
        raise OracleOperationalError("sandbox engine could not be executed") from None
    selector = selectors.DefaultSelector()
    streams = {
        process.stdout: bytearray(),
        process.stderr: bytearray(),
    }
    for stream in streams:
        selector.register(stream, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise OracleOperationalError("sandbox execution timed out")
            events = selector.select(remaining)
            if not events:
                raise OracleOperationalError("sandbox execution timed out")
            for key, _mask in events:
                stream = key.fileobj
                output = streams[stream]
                chunk = os.read(
                    stream.fileno(), min(64 * 1024, MAX_PROCESS_OUTPUT_BYTES + 1 - len(output)),
                )
                if not chunk:
                    selector.unregister(stream)
                    continue
                output.extend(chunk)
                if len(output) > MAX_PROCESS_OUTPUT_BYTES:
                    raise OracleOperationalError("sandbox output exceeded the byte limit")
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise OracleOperationalError("sandbox execution timed out")
        try:
            return_code = process.wait(timeout=remaining)
        except subprocess.TimeoutExpired:
            raise OracleOperationalError("sandbox execution timed out") from None
    except BaseException:
        _stop_process(process)
        raise
    finally:
        selector.close()
        for stream in streams:
            stream.close()
    try:
        stdout = bytes(streams[process.stdout]).decode("utf-8")
        stderr = bytes(streams[process.stderr]).decode("utf-8")
    except UnicodeDecodeError:
        raise OracleOperationalError("sandbox output is not valid UTF-8") from None
    return {"exit_code": return_code, "stdout": stdout, "stderr": stderr}


def _remove_container(engine, name, environment):
    try:
        result = subprocess.run(
            [str(engine), "rm", "--force", name], env=environment,
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        raise OracleOperationalError("sandbox cleanup failed") from None
    if result.returncode:
        raise OracleOperationalError("sandbox cleanup failed")


def _temporary_parent():
    cwd = Path.cwd().resolve()
    for name in ("/tmp", "/var/tmp"):
        try:
            candidate = Path(name).resolve(strict=True)
        except OSError:
            continue
        if candidate.is_dir() and candidate != cwd and cwd not in candidate.parents:
            return candidate
    raise OracleOperationalError("no temporary directory exists outside the repository")


def _execute_variant(seed, source_bytes, engine):
    temporary = None
    try:
        temporary = tempfile.TemporaryDirectory(
            prefix="evergreen-oracle-", dir=_temporary_parent(),
        )
        fixture = Path(temporary.name)
        source = fixture / seed["source"]["path"]
        source.parent.mkdir(parents=True)
        source.write_bytes(source_bytes)
        source.chmod(0o444)
        docker_config = fixture / ".docker"
        docker_config.mkdir()
    except OSError:
        if temporary is not None:
            try:
                temporary.cleanup()
            except OSError:
                pass
        raise OracleOperationalError("oracle fixture could not be created") from None

    name = "evergreen-oracle-" + secrets.token_hex(12)
    environment = {
        "DOCKER_CONFIG": str(docker_config),
        "HOME": str(fixture),
        "PATH": str(engine.parent),
    }
    try:
        result = _bounded_container(
            _sandbox_command(seed, fixture, name, engine), environment,
        )
    finally:
        try:
            _remove_container(engine, name, environment)
        finally:
            try:
                temporary.cleanup()
            except OSError:
                raise OracleOperationalError("oracle fixture cleanup failed") from None
    return result


def _docker_engine():
    found = shutil.which("docker")
    if not found:
        return None
    try:
        resolved = Path(found).resolve(strict=True)
    except OSError:
        return None
    return resolved if resolved.is_file() and os.access(resolved, os.X_OK) else None


def _runtime_result(value, label):
    if (not isinstance(value, dict) or set(value) != {"exit_code", "stdout", "stderr"} or
            type(value["exit_code"]) is not int or type(value["stdout"]) is not str or
            type(value["stderr"]) is not str):
        raise OracleError(f"{label} result is invalid")
    return value


def _row(seed, variant, code, label, mutation_id):
    return {
        "id": f"{seed['group_id']}:{variant}",
        "group_id": seed["group_id"],
        "project": seed["project"],
        "language": seed["language"],
        "variant": variant,
        "code": code.decode(),
        "code_sha256": hashlib.sha256(code).hexdigest(),
        "documentation": seed["documentation"]["template"],
        "documentation_sha256": seed["documentation"]["sha256"],
        "oracle_kind": seed["oracle"]["kind"],
        "mutation_id": mutation_id,
        "seed_sha256": seed["seed_sha256"],
        "label": label,
    }


def run_seed(seed, *, approved_images):
    """Execute source, one mutation, and one no-op only through the approved sandbox."""
    validate_seed(seed)
    language = seed["language"]
    if (not isinstance(approved_images, dict) or
            approved_images.get(language) != seed["sandbox"]["image"]):
        raise OracleError("seed does not use the approved sandbox image")
    engine = _docker_engine()
    if engine is None:
        raise OracleOperationalError("approved sandbox engine is unavailable")

    source = seed["source"]["code"].encode()
    mutation = _mutated_source(seed)
    noop = source + semantic_noop_suffix(language)
    expected = {**seed["oracle"]["expected_observable"], "stderr": ""}
    mismatch = {
        "exit_code": ORACLE_MISMATCH_EXIT,
        "stdout": mismatch_stdout(seed["oracle"]["kind"]),
        "stderr": "",
    }
    source_result = _runtime_result(_execute_variant(seed, source, engine), "source")
    if source_result != expected:
        raise OracleError("source did not match the expected oracle observable")
    mutation_result = _runtime_result(_execute_variant(seed, mutation, engine), "mutation")
    if mutation_result != mismatch:
        raise OracleError("mutation did not produce the structured oracle mismatch")
    noop_result = _runtime_result(_execute_variant(seed, noop, engine), "semantic no-op")
    if noop_result != expected:
        raise OracleError("semantic no-op changed the oracle observable")
    return (
        _row(seed, "source", source, "consistent", None),
        _row(seed, "mutation", mutation, "inconsistent", seed["mutation"]["id"]),
        _row(seed, "semantic-noop", noop, "consistent", seed["semantic_noop"]["id"]),
    )
