import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ci import pr_comment
from ci.review_context import claim_id


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ci" / "pr_comment.py"
CLAIM_TEXT = "Run `shipit --workers 4`."
CLAIM = claim_id("docs/usage.md", "2", CLAIM_TEXT)
NEGATIVE, POSITIVE = "a" * 16, "b" * 16


class PRCommentTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp_dir.name)
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        self.write("docs/usage.md", "# Usage\nRun `shipit --workers 4`.\n")
        self.write("src/cli.py", "def main():\n    workers = 4\n")
        self.git("add", "-A")
        self.git("commit", "-qm", "fixture")
        self.head = self.git("rev-parse", "HEAD")
        self.base = self.head
        self.claims = [{"id": CLAIM, "path": "docs/usage.md", "line": 2, "text": CLAIM_TEXT}]

    def tearDown(self):
        self.temp_dir.cleanup()

    def git(self, *args):
        return subprocess.run(
            ["git", *args],
            cwd=self.repo,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()

    def write(self, name, content):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def result(self, **updates):
        result = {
            "schema_version": 2,
            "status": "complete",
            "base": self.base,
            "head": self.head,
            "claims": {"total": 1, "certified": 1, "drift": 0, "unverified": 0},
            "findings": [],
            "unverified": [],
            "errors": [],
            "runtime": {"provider": "anthropic", "model": "test-model", "cli_version": "1.2.3"},
        }
        result.update(updates)
        return result

    def envelope(self, **updates):
        """Return a schema 3 model envelope: verdicts in, no model-reported counts."""
        envelope = self.result()
        del envelope["claims"]
        envelope.update(schema_version=3, verdicts=[{"id": CLAIM, "verdict": "certified"}])
        envelope.update(updates)
        return envelope

    def write_inventory(self, controls=False, head=None):
        claims = list(self.claims)
        if controls:
            claims += [
                {"id": NEGATIVE, "path": "docs/usage.md", "line": 2,
                 "text": "docs/usage.md line 2 reads exactly: " + CLAIM_TEXT},
                {"id": POSITIVE, "path": "docs/usage.md", "line": 2,
                 "text": "docs/usage.md line 2 reads exactly: # Usage"},
            ]
            key = Path(self.temp_dir.name) / "controls.json"
            key.write_text(json.dumps({"negative": NEGATIVE, "positive": POSITIVE}), encoding="utf-8")
        context = Path(self.temp_dir.name) / "context.json"
        context.write_text(
            json.dumps({"schema_version": 2, "head": head or self.head, "claims": claims}),
            encoding="utf-8",
        )
        return ["--context", str(context)] + (["--controls", str(key)] if controls else [])

    def finding(self, **updates):
        finding = {
            "id": CLAIM,
            "severity": "high",
            "category": "in_docs_not_code",
            "doc_path": "docs/usage.md",
            "doc_line": 2,
            "claim": "Run `shipit --workers 4`.",
            "code_path": "src/cli.py",
            "code_line": 2,
            "why": "The implementation no longer exposes the documented flag.",
            "rung": "contract",
            "fix_or_flag": "fix",
        }
        finding.update(updates)
        return finding

    def run_cli(self, text, inventory=None):
        if inventory is None:
            inventory = self.write_inventory()
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--repo",
                str(self.repo),
                "--base",
                self.base,
                "--head",
                self.head,
                "--provider",
                "anthropic",
                "--model",
                "test-model",
                "--cli-version",
                "1.2.3",
                *inventory,
            ],
            input=text,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

    def test_renders_explicit_clean_with_range_counts_and_runtime(self):
        output = pr_comment.render_result(self.result(), [])

        self.assertTrue(output.startswith("<!-- evergreen-report -->"))
        self.assertIn("docs still match", output)
        self.assertIn(self.base, output)
        self.assertIn("certified", output)
        self.assertIn("1", output)
        self.assertIn("test-model", output)

    def test_renders_drift_with_verified_doc_and_code_citations(self):
        result = self.result(
            claims={"total": 1, "certified": 0, "drift": 1, "unverified": 0},
            findings=[self.finding()],
        )

        output = pr_comment.render_result(result, [])

        self.assertNotIn("docs still match", output)
        self.assertIn("docs/usage.md:2", output)
        self.assertIn("src/cli.py:2", output)
        self.assertIn("The implementation", output)

    def test_renders_unverified_without_clean_language(self):
        result = self.result(
            claims={"total": 1, "certified": 0, "drift": 0, "unverified": 1},
            unverified=[{
                "doc_path": "docs/usage.md",
                "doc_line": 2,
                "claim": "Run `shipit --workers 4`.",
                "reason": "Runtime ordering cannot be proven by reading.",
            }],
        )

        output = pr_comment.render_result(result, [])

        self.assertNotIn("docs still match", output)
        self.assertIn("Unverified", output)
        self.assertIn("Runtime ordering", output)

    def test_errors_and_inconclusive_status_never_render_clean(self):
        for result, errors in [
            (None, ["model timed out"]),
            (self.result(status="inconclusive", errors=["model refused"]), []),
        ]:
            with self.subTest(result=result, errors=errors):
                output = pr_comment.render_result(
                    result,
                    errors,
                    expected_base=self.base,
                    expected_head=self.head,
                )
                self.assertIn("inconclusive", output.lower())
                self.assertNotIn("docs still match", output)
                self.assertIn(self.base, output)
                self.assertIn(self.head, output)
                self.assertIn("certified", output)
                if result is None:
                    self.assertIn("unknown", output)

    def test_escapes_markdown_html_pipes_newlines_and_bounds_fields(self):
        unsafe = "<script>*bold* [link](https://example.test) | break\nnext " + "x" * 5000
        result = self.result(
            claims={"total": 1, "certified": 0, "drift": 1, "unverified": 0},
            findings=[self.finding(claim=unsafe, why=unsafe)],
        )

        output = pr_comment.render_result(result, [])

        self.assertNotIn("<script>", output)
        self.assertIn("&lt;script&gt;", output)
        self.assertIn(r"\*bold\*", output)
        self.assertIn(r"\[link\]", output)
        self.assertIn(r"\| break next", output)
        self.assertNotIn("break\nnext", output)
        self.assertLess(len(output), 5000)

    def test_neutralizes_mentions_bidi_tabs_and_unsafe_controls(self):
        unsafe = "@octocat\talert\x07safe\u202eesrever"
        result = self.result(
            claims={"total": 1, "certified": 0, "drift": 1, "unverified": 0},
            findings=[self.finding(claim=unsafe, why=unsafe)],
        )

        output = pr_comment.render_result(result, [])

        self.assertNotIn("@octocat", output)
        self.assertIn("&#64;octocat", output)
        self.assertNotIn("\t", output)
        self.assertNotIn("\x07", output)
        self.assertNotIn("\u202e", output)
        self.assertIn("u202e", output)
        self.assertIn("x07", output)

    def test_cli_prose_only_is_inconclusive(self):
        result = self.run_cli("the docs look fine")

        self.assertEqual(result.returncode, 2)
        self.assertIn("inconclusive", result.stdout.lower())
        self.assertNotIn("docs still match", result.stdout)
        self.assertIn(self.base, result.stdout)
        self.assertIn(self.head, result.stdout)
        self.assertIn("| unknown | unknown | unknown | unknown |", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_cli_returns_zero_for_valid_complete_result(self):
        result = self.run_cli(json.dumps(self.envelope()))

        self.assertEqual(result.returncode, 0, result.stdout)
        self.assertIn("docs still match", result.stdout)
        self.assertIn("| 1 | 0 | 0 | 1 |", result.stdout)
        self.assertNotIn("controls:", result.stdout)

    def test_cli_renders_counts_derived_from_verdicts_not_the_model(self):
        drift = self.run_cli(json.dumps(self.envelope(
            verdicts=[{"id": CLAIM, "verdict": "drift"}], findings=[self.finding()],
        )))
        counted = self.envelope(claims={"total": 9, "certified": 9, "drift": 0, "unverified": 0})
        forged = self.run_cli(json.dumps(counted))
        missing = self.run_cli(json.dumps(self.envelope(verdicts=[])))

        self.assertEqual(drift.returncode, 0, drift.stdout)
        self.assertIn("| 0 | 1 | 0 | 1 |", drift.stdout)
        self.assertEqual(forged.returncode, 2)
        self.assertIn("unknown result fields: claims", forged.stdout)
        self.assertNotIn("| 9 |", forged.stdout)
        self.assertEqual(missing.returncode, 2)
        self.assertIn("missing claim id", missing.stdout)

    def test_cli_requires_the_trusted_inventory_for_a_complete_result(self):
        absent = self.run_cli(json.dumps(self.envelope(verdicts=[])), inventory=[])
        unbound = self.run_cli(json.dumps(self.envelope()), inventory=self.write_inventory(head="0" * 40))
        fallback = self.run_cli(
            json.dumps(self.envelope(verdicts=[], status="inconclusive", errors=["no key"])),
            inventory=[],
        )

        self.assertEqual(absent.returncode, 2)
        self.assertIn("requires the trusted claim inventory", absent.stdout)
        self.assertEqual(unbound.returncode, 2)
        self.assertIn("trusted review context is unreadable", unbound.stdout)
        self.assertEqual(fallback.returncode, 2)
        self.assertIn("no key", fallback.stdout)
        self.assertIn("| 0 | 0 | 0 | 0 |", fallback.stdout)

    def controlled_envelope(self, negative="certified", positive="drift"):
        return self.envelope(verdicts=[
            {"id": POSITIVE, "verdict": positive},
            {"id": CLAIM, "verdict": "certified"},
            {"id": NEGATIVE, "verdict": negative},
        ])

    def test_cli_renders_control_grades_without_synthetic_text(self):
        passed = self.run_cli(json.dumps(self.controlled_envelope()), self.write_inventory(controls=True))
        missed = self.run_cli(
            json.dumps(self.controlled_envelope(positive="certified")), self.write_inventory(controls=True)
        )
        flagged = self.run_cli(
            json.dumps(self.controlled_envelope(negative="drift")), self.write_inventory(controls=True)
        )

        self.assertEqual(passed.returncode, 0, passed.stdout)
        self.assertIn("controls: positive pass, negative pass", passed.stdout)
        self.assertIn("docs still match", passed.stdout)
        self.assertIn("| 1 | 0 | 0 | 1 |", passed.stdout)
        for output, line in [(missed, "controls: positive fail, negative pass"),
                             (flagged, "controls: positive pass, negative fail")]:
            self.assertEqual(output.returncode, 2)
            self.assertIn(line, output.stdout)
            self.assertIn("inconclusive", output.stdout.lower())
            self.assertNotIn("docs still match", output.stdout)
        for output in (passed, missed, flagged):
            self.assertNotIn("reads exactly", output.stdout)
            self.assertNotIn(POSITIVE, output.stdout)
            self.assertNotIn(NEGATIVE, output.stdout)

    def test_render_without_controls_has_no_controls_line(self):
        plain = pr_comment.render_result(self.result(), [])
        graded = pr_comment.render_result(
            self.result(controls={"negative": "pass", "positive": "fail"}), []
        )

        self.assertNotIn("controls:", plain)
        self.assertEqual(graded.replace("controls: positive fail, negative pass\n\n", ""), plain)

    def test_cli_rejects_and_replaces_model_controlled_runtime_identity(self):
        envelope = self.envelope(
            runtime={"provider": "attacker", "model": "forged", "cli_version": "0.0.0"}
        )

        result = self.run_cli(json.dumps(envelope))

        self.assertEqual(result.returncode, 2)
        self.assertIn("inconclusive", result.stdout.lower())
        self.assertIn("model: test-model", result.stdout)
        self.assertIn("CLI: 1.2.3", result.stdout)
        self.assertNotIn("forged", result.stdout)

    def test_cli_rejects_invalid_citations_as_inconclusive(self):
        envelope = self.envelope(
            verdicts=[{"id": CLAIM, "verdict": "drift"}],
            findings=[self.finding(doc_line=1)],
        )

        result = self.run_cli(json.dumps(envelope))

        self.assertEqual(result.returncode, 2)
        self.assertIn("inconclusive", result.stdout.lower())
        self.assertIn("claim does not occur", result.stdout)
        self.assertNotIn("docs still match", result.stdout)
        self.assertIn(self.base, result.stdout)
        self.assertIn(self.head, result.stdout)
        self.assertIn("| unknown | unknown | unknown | unknown |", result.stdout)


if __name__ == "__main__":
    unittest.main()
