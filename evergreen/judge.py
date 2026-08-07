"""Isolated Codex judge calls for documentation certification."""

import json
import os
from pathlib import Path
import selectors
import shutil
import subprocess
import tempfile
import time
import tomllib
from types import SimpleNamespace

MAX_MODEL_STDOUT_BYTES = 1024 * 1024
MAX_MODEL_STDERR_BYTES = 256 * 1024
CODEX_NO_TOOLS = (
    "Do not call or use any tools. Judge only the evidence embedded in this prompt and return "
    "the requested JSON object. Your final response must be an object with exactly one string "
    'field named "payload"; encode the requested JSON object as that string\'s value.'
)
CODEX_DISABLED_FEATURES = (
    "plugins", "apps", "browser_use", "browser_use_external",
    "browser_use_full_cdp_access", "computer_use", "image_generation", "multi_agent",
    "unified_exec", "shell_tool", "goals", "hooks", "code_mode_host", "tool_suggest",
    "workspace_dependencies", "in_app_browser",
)
JUDGE_RESULT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["findings", "summary"],
    "properties": {
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["verdict", "claim", "evidence"],
                "properties": {
                    "verdict": {"enum": ["certified", "drift", "unverified"]},
                    "claim": {"type": "string"},
                    "evidence": {"type": "string"},
                },
            },
        },
        "summary": {"type": "string"},
    },
}
MODEL_OUTPUT_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["payload"],
    "properties": {"payload": {"type": "string"}},
}


def resolve_judge_model() -> tuple[str | None, str]:
    """Return the configured judge model and its deliberately pinned effort."""
    # Effort is pinned, never read from config.toml: a host configured for ultra is paying that
    # rate to read one document. Model, by contrast, MUST follow the host — hardcoding a name
    # here goes stale the week the next tier ships.
    effort = os.environ.get("EVERGREEN_JUDGE_EFFORT") or "medium"
    override = os.environ.get("EVERGREEN_JUDGE_MODEL")
    if override:
        return override, effort

    config_home = os.environ.get("CODEX_HOME") or str(Path.home() / ".codex")
    try:
        with (Path(config_home) / "config.toml").open("rb") as config_file:
            config = tomllib.load(config_file)
    except (OSError, tomllib.TOMLDecodeError):
        return None, effort
    model = config.get("model")
    return (model if isinstance(model, str) else None), effort


def judge_available() -> bool:
    """Return whether the Codex executable is available."""
    return shutil.which("codex") is not None


def _valid_judge_result(value: object) -> bool:
    """Return whether an unwrapped result has the declared judge shape."""
    if not isinstance(value, dict) or set(value) != {"findings", "summary"}:
        return False
    if not isinstance(value["summary"], str) or not isinstance(value["findings"], list):
        return False
    for finding in value["findings"]:
        if (not isinstance(finding, dict) or set(finding) != {"verdict", "claim", "evidence"}
                or finding["verdict"] not in {"certified", "drift", "unverified"}
                or not isinstance(finding["claim"], str)
                or not isinstance(finding["evidence"], str)):
            return False
    return True


def _bounded_cli_run(
    command: list[str], timeout: int, input: str,
) -> SimpleNamespace:
    """Capture Codex output with independent stdout and stderr byte ceilings.

    ponytail: a deliberate near-copy of eval/bench/trial.py's bounded_cli_run, not an oversight.
    That file's SHA-256 is stamped into every benchmark artifact (eval/bench/artifact.py names it
    in the judge source set), and replay.py --expect-stored demands byte parity, so refactoring a
    shared helper out of it would invalidate replay on every stored artifact. ci/bounded_process.py
    is the other sibling and is not a substitute: it merges stdout and stderr into one buffer,
    while parsing Codex's JSONL needs them bounded independently. Consolidate only if the bench
    lane is ever re-frozen.
    """
    prompt_file = tempfile.TemporaryFile()
    prompt_file.write(input.encode())
    prompt_file.seek(0)
    process = subprocess.Popen(
        command, stdin=prompt_file, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    selector = selectors.DefaultSelector()
    streams = {
        process.stdout: (bytearray(), MAX_MODEL_STDOUT_BYTES),
        process.stderr: (bytearray(), MAX_MODEL_STDERR_BYTES),
    }
    for stream in streams:
        selector.register(stream, selectors.EVENT_READ)
    deadline = time.monotonic() + timeout
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            events = selector.select(max(0, remaining))
            if remaining <= 0 or not events:
                raise subprocess.TimeoutExpired(command, timeout)
            for key, _mask in events:
                stream = key.fileobj
                output, limit = streams[stream]
                chunk = os.read(stream.fileno(), min(64 * 1024, limit + 1 - len(output)))
                if not chunk:
                    selector.unregister(stream)
                    continue
                output.extend(chunk)
                if len(output) > limit:
                    raise OSError("model CLI output limit exceeded")
        return_code = process.wait(timeout=max(0, deadline - time.monotonic()))
    except Exception:
        if process.poll() is None:
            process.kill()
        process.wait()
        raise
    finally:
        selector.close()
        process.stdout.close()
        process.stderr.close()
        prompt_file.close()
    stdout = bytes(streams[process.stdout][0]).decode("utf-8", "replace")
    stderr = bytes(streams[process.stderr][0]).decode("utf-8", "replace")
    return SimpleNamespace(returncode=return_code, stdout=stdout, stderr=stderr)


def judge_json(prompt: str, timeout: int = 300, max_retries: int = 2) -> dict:
    """Run one isolated Codex certification call with bounded retries."""
    model, effort = resolve_judge_model()
    command = [
        "codex", "exec", "--strict-config", "--ephemeral", "--ignore-user-config",
        "--ignore-rules", "--skip-git-repo-check", "-c", 'approval_policy="never"',
        "-c", "skills.include_instructions=false", "-c",
        f'model_reasoning_effort="{effort}"', "--sandbox", "read-only",
    ]
    for feature in CODEX_DISABLED_FEATURES:
        command += ["--disable", feature]
    if model is not None:
        command += ["--model", model]

    reason = "malformed response"
    attempts = 0
    with tempfile.TemporaryDirectory(prefix="evergreen-codex-") as empty_cwd, \
            tempfile.NamedTemporaryFile(mode="w", suffix=".json", encoding="utf-8") as schema_file:
        json.dump(MODEL_OUTPUT_SCHEMA, schema_file)
        schema_file.flush()
        isolated_command = [
            *command, "--output-schema", schema_file.name, "--color", "never", "--json",
            "-C", empty_cwd, "-",
        ]
        for _ in range(max_retries + 1):
            attempts += 1
            try:
                completed = _bounded_cli_run(
                    isolated_command,
                    timeout,
                    f"{CODEX_NO_TOOLS}\nThe payload must satisfy this JSON schema: "
                    f"{json.dumps(JUDGE_RESULT_SCHEMA, separators=(',', ':'))}\n\n{prompt}",
                )
            except subprocess.TimeoutExpired:
                reason = "timeout"
                continue
            except OSError as error:
                return {"status": "abstain", "reason": str(error), "attempts": attempts}
            if (len(completed.stdout.encode()) > MAX_MODEL_STDOUT_BYTES or
                    len(completed.stderr.encode()) > MAX_MODEL_STDERR_BYTES):
                return {
                    "status": "abstain", "reason": "model CLI output limit exceeded",
                    "attempts": attempts,
                }
            if completed.returncode:
                reason = f"CLI exited {completed.returncode}"
                continue

            agent_message = None
            turn_completed = False
            used_tool = False
            malformed = False
            for line in completed.stdout.splitlines():
                try:
                    event = json.loads(line)
                except json.JSONDecodeError:
                    malformed = True
                    continue
                if not isinstance(event, dict):
                    malformed = True
                    continue
                if event.get("type") == "turn.completed":
                    turn_completed = True
                if event.get("type", "").startswith("item."):
                    item = event.get("item")
                    if isinstance(item, dict):
                        item_type = item.get("type")
                        if item_type == "agent_message" and event["type"] == "item.completed":
                            agent_message = item.get("text")
                        elif item_type not in ("reasoning", "error"):
                            used_tool = True
            if used_tool:
                return {
                    "status": "abstain", "reason": "Codex attempted a tool call",
                    "attempts": attempts,
                }
            if turn_completed and isinstance(agent_message, str) and not malformed:
                try:
                    wrapper = json.loads(agent_message)
                    value = json.loads(wrapper["payload"]) if set(wrapper) == {"payload"} else None
                except (json.JSONDecodeError, KeyError, TypeError):
                    value = None
                if _valid_judge_result(value):
                    return {"status": "ok", "value": value, "attempts": attempts}
            reason = "malformed or incomplete Codex response"
    return {"status": "abstain", "reason": reason, "attempts": attempts}
