import os
from pathlib import Path
import subprocess
import sys
import time
import unittest


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
