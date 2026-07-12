#!/usr/bin/env python3
"""Run one command with wall-clock, output, process-group, and environment bounds."""

import argparse
import os
import selectors
import signal
import subprocess
import sys
import time


TIMEOUT_EXIT = 124
OUTPUT_EXIT = 125
SPAWN_EXIT = 126
DEFAULT_ENV = ("PATH", "HOME", "TMPDIR", "LANG", "LC_ALL")


def _stop(process: subprocess.Popen[bytes]) -> None:
    if os.name == "posix":
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except (PermissionError, ProcessLookupError):
            if process.poll() is None:
                process.kill()
        if process.poll() is None:
            process.wait()
        return
    if process.poll() is not None:
        return
    try:
        process.terminate()
        process.wait(timeout=0.25)
    except (PermissionError, ProcessLookupError, subprocess.TimeoutExpired):
        try:
            if process.poll() is not None:
                return
            process.kill()
        except (PermissionError, ProcessLookupError):
            if process.poll() is None:
                process.kill()
        process.wait()


def run_bounded(
    command: list[str],
    *,
    timeout_seconds: float,
    max_output_bytes: int,
    clean_env: bool,
    keep_env: list[str],
    preserve_partial: bool = False,
) -> tuple[int, bytes, str | None]:
    if clean_env:
        names = set(DEFAULT_ENV) | set(keep_env)
        environment = {name: os.environ[name] for name in names if name in os.environ}
    else:
        environment = os.environ.copy()
    try:
        process = subprocess.Popen(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            env=environment,
            start_new_session=os.name == "posix",
        )
    except OSError as error:
        return SPAWN_EXIT, b"", f"could not start command: {error}"

    assert process.stdout is not None
    descriptor = process.stdout.fileno()
    os.set_blocking(descriptor, False)
    selector = selectors.DefaultSelector()
    selector.register(descriptor, selectors.EVENT_READ)
    output = bytearray()
    deadline = time.monotonic() + timeout_seconds
    failure: tuple[int, str] | None = None
    try:
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                failure = (TIMEOUT_EXIT, f"command timed out after {timeout_seconds:g} seconds")
                break
            for key, _ in selector.select(min(remaining, 0.1)):
                try:
                    chunk = os.read(key.fd, 65_536)
                except BlockingIOError:
                    continue
                if not chunk:
                    selector.unregister(key.fd)
                    continue
                if len(output) + len(chunk) > max_output_bytes:
                    if preserve_partial:
                        output.extend(chunk[:max_output_bytes - len(output)])
                    failure = (
                        OUTPUT_EXIT,
                        f"command output exceeded {max_output_bytes} bytes",
                    )
                    break
                output.extend(chunk)
            if failure:
                break
        if failure:
            _stop(process)
            return failure[0], bytes(output) if preserve_partial else b"", failure[1]
        returncode = process.wait(timeout=max(0.01, deadline - time.monotonic()))
        return returncode, bytes(output), None
    except subprocess.TimeoutExpired:
        _stop(process)
        return TIMEOUT_EXIT, b"", f"command timed out after {timeout_seconds:g} seconds"
    finally:
        selector.close()
        process.stdout.close()


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--timeout-seconds", type=float, required=True)
    parser.add_argument("--max-output-bytes", type=int, required=True)
    parser.add_argument("--clean-env", action="store_true")
    parser.add_argument("--keep-env", action="append", default=[])
    parser.add_argument("command", nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ["--"] else args.command
    if not command:
        parser.error("a command is required after --")
    if not 0 < args.timeout_seconds <= 3600:
        parser.error("--timeout-seconds must be in (0, 3600]")
    if not 0 < args.max_output_bytes <= 16_777_216:
        parser.error("--max-output-bytes must be in (0, 16777216]")
    status, output, error = run_bounded(
        command,
        timeout_seconds=args.timeout_seconds,
        max_output_bytes=args.max_output_bytes,
        clean_env=args.clean_env,
        keep_env=args.keep_env,
    )
    if output:
        sys.stdout.buffer.write(output)
    if error:
        print(f"evergreen: {error}.", file=sys.stderr)
    return status


if __name__ == "__main__":
    raise SystemExit(main())
