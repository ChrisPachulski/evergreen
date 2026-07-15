import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest


class PythonPilotTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def commit(self, source, test, *, extra=None):
        (self.repo / "source.py").write_text(source)
        (self.repo / "test_source.py").write_text(test)
        for name, contents in (extra or {}).items():
            (self.repo / name).write_text(contents)
        subprocess.run(["git", "init", "-q"], cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.email", "pilot@example.invalid"],
                       cwd=self.repo, check=True)
        subprocess.run(["git", "config", "user.name", "Pilot"], cwd=self.repo, check=True)
        subprocess.run(["git", "add", "."], cwd=self.repo, check=True)
        subprocess.run(["git", "commit", "-qm", "fixture"], cwd=self.repo, check=True)

    def run_pilot(self):
        from eval.oracle.python_pilot import run_pilot

        return run_pilot(
            self.repo,
            "source.py",
            "test_source.py",
        )

    def test_runs_exact_git_project_and_emits_bounded_ungraded_report(self):
        self.commit(
            'DECOY = "return 1; self.assertEqual(value(), 1)"\n'
            "# return 1\n"
            "def value():\n"
            "    return 1\n",
            "import unittest\n"
            "from source import value\n\n"
            "class ValueTests(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        # self.assertEqual(value(), 1)\n"
            "        decoy = 'self.assertEqual(value(), 1)'\n"
            "        self.assertEqual(value(), 1)\n",
        )

        evidence = self.run_pilot()

        self.assertEqual(set(evidence), {
            "schema_version", "protocol", "fixture_commit", "binding",
            "pristine", "noop", "mutant",
        })
        self.assertEqual(evidence["schema_version"], 1)
        self.assertEqual(evidence["protocol"], "evergreen-python-in-situ-pilot-v1")
        self.assertEqual(evidence["fixture_commit"], subprocess.run(
            ["git", "rev-parse", "HEAD"], cwd=self.repo, check=True,
            capture_output=True, text=True,
        ).stdout.strip())
        self.assertEqual(set(evidence["binding"]), {"source", "assertion"})

        adapter_keys = {
            "schema_version", "protocol", "control_sha256", "assertion_id_sha256",
            "source_sha256", "phases",
        }
        phase_names = [
            "materialize", "dependency-verify", "compile", "selected-test", "bound-assertion",
        ]
        for variant in ("pristine", "noop", "mutant"):
            result = evidence[variant]
            self.assertEqual(set(result), adapter_keys)
            self.assertEqual(result["protocol"], "evergreen-oracle-pilot-result-v1")
            self.assertEqual([phase["name"] for phase in result["phases"]], phase_names)
            for phase in result["phases"]:
                self.assertEqual(set(phase), {"name", "exit_code", "stdout", "stderr"})
            self.assertEqual([phase["exit_code"] for phase in result["phases"][:3]], [0, 0, 0])

        pristine_test = evidence["pristine"]["phases"][3]
        noop_test = evidence["noop"]["phases"][3]
        mutant_test = evidence["mutant"]["phases"][3]
        self.assertEqual(pristine_test["exit_code"], 0)
        self.assertEqual(noop_test["exit_code"], 0)
        self.assertNotEqual(mutant_test["exit_code"], 0)

        observations = [
            json.loads(evidence[name]["phases"][4]["stdout"])
            for name in ("pristine", "noop", "mutant")
        ]
        self.assertEqual(observations[0], observations[1])
        self.assertEqual(observations[0]["outcome"], "pass")
        self.assertEqual(observations[2]["outcome"], "fail")
        self.assertIsNone(observations[0]["error_class"])
        self.assertEqual(observations[2]["error_class"], "AssertionError")
        self.assertEqual(observations[0]["assertion_id_sha256"],
                         observations[2]["assertion_id_sha256"])
        self.assertNotEqual(observations[0]["actual_sha256"],
                            observations[2]["actual_sha256"])
        self.assertEqual(set(observations[0]), {
            "schema_version", "kind", "assertion_id_sha256", "outcome",
            "actual_sha256", "error_class",
        })
        self.assertNotIn("grade", json.dumps(evidence))
        self.assertNotIn("corpus", json.dumps(evidence))
        self.assertEqual(
            evidence["pristine"]["source_sha256"],
            hashlib.sha256((self.repo / "source.py").read_bytes()).hexdigest(),
        )

    def test_rejects_comment_and_string_decoys_without_ast_sites(self):
        from eval.oracle.python_pilot import PilotError

        self.commit(
            'TEXT = "def value(): return 1"\n# def value(): return 1\n',
            "import unittest\n"
            "class ValueTests(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        text = 'self.assertEqual(value(), 1)'\n"
            "        # self.assertEqual(value(), 1)\n",
        )

        with self.assertRaisesRegex(PilotError, "exactly one bound unittest assertion"):
            self.run_pilot()

    def test_rejects_project_compile_failure(self):
        from eval.oracle.python_pilot import PilotError

        self.commit(
            "def value():\n    return 1\n",
            "import unittest\nfrom source import value\n"
            "class ValueTests(unittest.TestCase):\n"
            "    def test_value(self):\n        self.assertEqual(value(), 1)\n",
            extra={"broken.py": "def broken(:\n"},
        )

        with self.assertRaisesRegex(PilotError, "pristine compile phase failed"):
            self.run_pilot()

    def test_rejects_assertion_that_does_not_match_production_return(self):
        from eval.oracle.python_pilot import PilotError

        self.commit(
            "def value():\n    return 1\n",
            "import unittest\nfrom source import value\n"
            "class ValueTests(unittest.TestCase):\n"
            "    def test_value(self):\n        self.assertEqual(value(), 2)\n",
        )

        with self.assertRaisesRegex(PilotError, "assertion does not bind the production return"):
            self.run_pilot()

    def test_rejects_ambiguous_production_sites(self):
        from eval.oracle.python_pilot import PilotError

        self.commit(
            "def value(flag=False):\n"
            "    if flag:\n        return 1\n"
            "    return 1\n",
            "import unittest\nfrom source import value\n"
            "class ValueTests(unittest.TestCase):\n"
            "    def test_value(self):\n        self.assertEqual(value(), 1)\n",
        )

        with self.assertRaisesRegex(PilotError, "exactly one production mutation site"):
            self.run_pilot()

    def test_rejects_ambiguous_assertion_sites(self):
        from eval.oracle.python_pilot import PilotError

        self.commit(
            "def value():\n    return 1\n",
            "import unittest\nfrom source import value\n"
            "class ValueTests(unittest.TestCase):\n"
            "    def test_value(self):\n"
            "        self.assertEqual(value(), 1)\n"
            "        self.assertEqual(value(), 1)\n",
        )

        with self.assertRaisesRegex(PilotError, "exactly one bound unittest assertion"):
            self.run_pilot()

    def test_ast_spans_use_utf8_byte_offsets(self):
        self.commit(
            'def value():\n    label = "é"; return 1\n',
            "import unittest\nfrom source import value\n"
            "class ValueTests(unittest.TestCase):\n"
            "    def test_value(self):\n        self.assertEqual(value(), 1)\n",
        )

        evidence = self.run_pilot()

        binding = evidence["binding"]["source"]
        raw = (self.repo / "source.py").read_bytes()
        self.assertEqual(raw[binding["start"]:binding["end"]], b"1")
        self.assertEqual(binding["sha256"], hashlib.sha256(b"1").hexdigest())


if __name__ == "__main__":
    unittest.main()
