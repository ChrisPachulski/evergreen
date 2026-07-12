import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from ci import pr_comment


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ci" / "pr_comment.py"


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
            "schema_version": 1,
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

    def finding(self, **updates):
        finding = {
            "severity": "high",
            "category": "in_docs_not_code",
            "doc_path": "docs/usage.md",
            "doc_line": 2,
            "claim": "Run `shipit --workers 4`.",
            "code_path": "src/cli.py",
            "code_line": 2,
            "why": "The implementation no longer exposes the documented flag.",
            "fix_or_flag": "fix",
        }
        finding.update(updates)
        return finding

    def run_cli(self, text):
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
                output = pr_comment.render_result(result, errors)
                self.assertIn("inconclusive", output.lower())
                self.assertNotIn("docs still match", output)

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

    def test_cli_prose_only_is_inconclusive(self):
        result = self.run_cli("the docs look fine")

        self.assertEqual(result.returncode, 2)
        self.assertIn("inconclusive", result.stdout.lower())
        self.assertNotIn("docs still match", result.stdout)
        self.assertEqual(result.stderr, "")

    def test_cli_returns_zero_for_valid_complete_result(self):
        result = self.run_cli(json.dumps(self.result()))

        self.assertEqual(result.returncode, 0)
        self.assertIn("docs still match", result.stdout)

    def test_cli_rejects_invalid_citations_as_inconclusive(self):
        envelope = self.result(
            claims={"total": 1, "certified": 0, "drift": 1, "unverified": 0},
            findings=[self.finding(doc_line=1)],
        )

        result = self.run_cli(json.dumps(envelope))

        self.assertEqual(result.returncode, 2)
        self.assertIn("inconclusive", result.stdout.lower())
        self.assertIn("claim does not occur", result.stdout)
        self.assertNotIn("docs still match", result.stdout)


if __name__ == "__main__":
    unittest.main()
