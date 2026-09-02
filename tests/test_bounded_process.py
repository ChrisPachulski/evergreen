import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

from ci import bounded_process


ROOT = Path(__file__).resolve().parents[1]
RUNNER = ROOT / "ci" / "bounded_process.py"


class BoundedProcessTests(unittest.TestCase):
    def run_runner(self, *options, code, env=None):
        return subprocess.run(
            [
                sys.executable,
                str(RUNNER),
                *options,
                "--",
                sys.executable,
                "-c",
                code,
            ],
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=env,
            check=False,
        )

    def test_preserves_output_and_child_status(self):
        result = self.run_runner(
            "--timeout-seconds", "2",
            "--max-output-bytes", "100",
            code="import sys; print('bounded'); sys.exit(7)",
        )
        self.assertEqual(result.returncode, 7)
        self.assertEqual(result.stdout, b"bounded\n")

    def test_times_out_the_process_group(self):
        started = time.monotonic()
        result = self.run_runner(
            "--timeout-seconds", "0.2",
            "--max-output-bytes", "100",
            code="import subprocess,sys,time; subprocess.Popen([sys.executable,'-c','import time; time.sleep(9)']); time.sleep(9)",
        )
        self.assertEqual(result.returncode, 124)
        self.assertLess(time.monotonic() - started, 2)
        self.assertIn(b"timed out", result.stderr)

    def test_timeout_keeps_the_captured_prefix_when_asked(self):
        code = "import sys,time; sys.stdout.write('partial'); sys.stdout.flush(); time.sleep(9)"
        for preserve, expected in ((True, b"partial"), (False, b"")):
            status, output, reason = bounded_process.run_bounded(
                [sys.executable, "-c", code], timeout_seconds=0.3, max_output_bytes=100,
                clean_env=False, keep_env=[], preserve_partial=preserve)
            self.assertEqual(status, bounded_process.TIMEOUT_EXIT)
            self.assertEqual(output, expected)
            self.assertIn("timed out", reason)

    def test_timeout_kills_inherited_group_descendant_after_parent_has_exited(self):
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "descendant-survived"
            child = (
                "import pathlib,time; time.sleep(.3); "
                f"pathlib.Path({str(marker)!r}).write_text('alive')"
            )
            result = self.run_runner(
                "--timeout-seconds", "0.1",
                "--max-output-bytes", "100",
                code=(
                    "import subprocess,sys; "
                    f"subprocess.Popen([sys.executable, '-c', {child!r}])"
                ),
            )
            time.sleep(0.4)

            self.assertEqual(result.returncode, 124)
            self.assertFalse(marker.exists())

    def test_detached_descendant_limitation_is_explicit_and_truthful(self):
        self.assertIn("deliberately detached descendants", bounded_process.__doc__.casefold())
        with tempfile.TemporaryDirectory() as temporary:
            marker = Path(temporary) / "detached-descendant-survived"
            child = (
                "import pathlib,time; time.sleep(2.5); "
                f"pathlib.Path({str(marker)!r}).write_text('alive')"
            )
            result = self.run_runner(
                "--timeout-seconds", "1",
                "--max-output-bytes", "100",
                code=(
                    "import subprocess,sys; "
                    f"subprocess.Popen([sys.executable, '-c', {child!r}], "
                    "start_new_session=True)"
                ),
            )
            # The runner is killed at 1 s, after the child has had time to spawn the
            # detached grandchild even on a loaded host; the grandchild outlives the kill
            # by holding the pipe for 2.5 s, so the marker appears only if it survived.
            deadline = time.monotonic() + 15
            while not marker.exists() and time.monotonic() < deadline:
                time.sleep(0.05)

            self.assertEqual(result.returncode, 124)
            self.assertTrue(marker.exists())
            marker.unlink()

    def test_stops_when_output_exceeds_the_ceiling(self):
        result = self.run_runner(
            "--timeout-seconds", "2",
            "--max-output-bytes", "32",
            code="import sys; sys.stdout.write('x' * 1000000)",
        )
        self.assertEqual(result.returncode, 125)
        self.assertEqual(result.stdout, b"")
        self.assertIn(b"output exceeded", result.stderr)

    def test_clean_environment_keeps_only_defaults_and_named_values(self):
        env = dict(os.environ, KEEP_ME="yes", DROP_ME="secret")
        result = self.run_runner(
            "--timeout-seconds", "2",
            "--max-output-bytes", "200",
            "--clean-env",
            "--keep-env", "KEEP_ME",
            code=(
                "import os; "
                "print(os.getenv('KEEP_ME')); "
                "print(os.getenv('DROP_ME')); "
                "print('PATH' in os.environ)"
            ),
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.splitlines(), [b"yes", b"None", b"True"])


if __name__ == "__main__":
    unittest.main()
