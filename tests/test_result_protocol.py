import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from ci.result_protocol import load_validated_result, parse_result, validate_result


class ResultProtocolTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp_dir.name)
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")
        self.write("docs/usage.md", "# Usage\nRun `shipit --workers 4`.\n")
        self.write("src/cli.py", "def main():\n    workers = 4\n")
        self.head = self.commit("fixture")
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

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

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

    def unverified(self, **updates):
        item = {
            "doc_path": "docs/usage.md",
            "doc_line": 2,
            "claim": "Run `shipit --workers 4`.",
            "reason": "Runtime ordering cannot be proven by reading.",
        }
        item.update(updates)
        return item

    def assert_invalid(self, result, phrase):
        errors = validate_result(result, self.repo, self.base, self.head)
        self.assertTrue(any(phrase in error for error in errors), errors)

    def test_parses_one_whole_object_and_one_fenced_envelope(self):
        result = self.result()
        self.assertEqual(parse_result(json.dumps(result)), result)
        fenced = "analysis before the result\n```evergreen-result\n" + json.dumps(result) + "\n```\n"
        self.assertEqual(parse_result(fenced), result)

    def test_rejects_malformed_prose_jsonl_and_multiple_envelopes(self):
        bad_inputs = [
            "not json",
            '{"schema_version":',
            '{"schema_version": 1}\n{"schema_version": 1}',
            "```evergreen-result\n{}\n```\n```evergreen-result\n{}\n```",
        ]
        for text in bad_inputs:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_result(text)

    def test_accepts_explicit_clean_result(self):
        result = self.result()
        self.assertEqual(validate_result(result, self.repo, self.base, self.head), [])
        self.assertEqual(
            load_validated_result(json.dumps(result), self.repo, self.base, self.head),
            (result, []),
        )

    def test_accepts_findings_and_checks_head_content_not_worktree_content(self):
        finding = self.finding()
        result = self.result(
            claims={"total": 1, "certified": 0, "drift": 1, "unverified": 0},
            findings=[finding],
        )
        self.write("docs/usage.md", "working tree no longer contains the claim\n")
        self.write("src/cli.py", "working tree has only one line\n")
        self.assertEqual(validate_result(result, self.repo, self.base, self.head), [])

    def test_accepts_unverified_claims(self):
        item = self.unverified()
        result = self.result(
            claims={"total": 1, "certified": 0, "drift": 0, "unverified": 1},
            unverified=[item],
        )
        self.assertEqual(validate_result(result, self.repo, self.base, self.head), [])

    def test_load_returns_none_and_parse_error(self):
        result, errors = load_validated_result("prose only", self.repo, self.base, self.head)
        self.assertIsNone(result)
        self.assertTrue(any("result envelope" in error for error in errors), errors)

    def test_rejects_wrong_schema_status_base_and_head(self):
        cases = [
            ({"schema_version": 2}, "schema_version"),
            ({"status": "clean"}, "status"),
            ({"base": "wrong"}, "base"),
            ({"head": "wrong"}, "head"),
        ]
        for update, phrase in cases:
            with self.subTest(update=update):
                self.assert_invalid(self.result(**update), phrase)

    def test_rejects_boolean_schema_and_non_string_status_without_raising(self):
        self.assert_invalid(self.result(schema_version=True), "schema_version")
        self.assert_invalid(self.result(status=["complete"]), "status")

    def test_rejects_missing_or_extra_envelope_fields(self):
        missing = self.result()
        del missing["runtime"]
        self.assert_invalid(missing, "missing result fields")
        self.assert_invalid(self.result(extra="ignored"), "unknown result fields")

    def test_rejects_bad_claim_counts_and_boolean_counts(self):
        cases = [
            {"total": 2, "certified": 1, "drift": 0, "unverified": 0},
            {"total": 1, "certified": 0, "drift": 1, "unverified": 0},
            {"total": 1, "certified": 0, "drift": 0, "unverified": 1},
            {"total": True, "certified": 1, "drift": 0, "unverified": 0},
        ]
        for claims in cases:
            with self.subTest(claims=claims):
                self.assert_invalid(self.result(claims=claims), "claims")

    def test_rejects_complete_result_with_errors(self):
        self.assert_invalid(self.result(errors=["model timed out"]), "complete result")

    def test_rejects_absolute_traversal_and_non_normalized_paths(self):
        for path in [str((self.repo / "docs/usage.md").resolve()), "../outside.md", "./docs/usage.md", "docs\\usage.md"]:
            with self.subTest(path=path):
                finding = self.finding(doc_path=path)
                result = self.result(
                    claims={"total": 1, "certified": 0, "drift": 1, "unverified": 0},
                    findings=[finding],
                )
                self.assert_invalid(result, "doc_path")

    def test_rejects_symlink_escape(self):
        outside = self.repo.parent / "outside-result-protocol.md"
        outside.write_text("Run `shipit --workers 4`.\n", encoding="utf-8")
        try:
            (self.repo / "escape.md").symlink_to(outside)
            self.git("add", "escape.md")
            self.git("commit", "-qm", "symlink")
            self.head = self.git("rev-parse", "HEAD")
            finding = self.finding(doc_path="escape.md", doc_line=1)
            result = self.result(
                head=self.head,
                claims={"total": 1, "certified": 0, "drift": 1, "unverified": 0},
                findings=[finding],
            )
            self.assert_invalid(result, "escapes repository")
        finally:
            outside.unlink(missing_ok=True)

    def test_rejects_invalid_or_missing_citation_lines(self):
        for updates in [{"doc_line": 0}, {"doc_line": 99}, {"code_line": -1}, {"code_line": 99}]:
            with self.subTest(updates=updates):
                finding = self.finding(**updates)
                result = self.result(
                    claims={"total": 1, "certified": 0, "drift": 1, "unverified": 0},
                    findings=[finding],
                )
                self.assert_invalid(result, "line")

    def test_rejects_claim_not_found_on_cited_documentation_line(self):
        finding = self.finding(claim="A different claim")
        result = self.result(
            claims={"total": 1, "certified": 0, "drift": 1, "unverified": 0},
            findings=[finding],
        )
        self.assert_invalid(result, "claim does not occur")

    def test_rejects_missing_empty_or_multiline_claims(self):
        for claim in [None, "", "Run `shipit`\nsecond line"]:
            with self.subTest(claim=claim):
                finding = self.finding()
                if claim is None:
                    del finding["claim"]
                else:
                    finding["claim"] = claim
                result = self.result(
                    claims={"total": 1, "certified": 0, "drift": 1, "unverified": 0},
                    findings=[finding],
                )
                self.assert_invalid(result, "claim")

    def test_rejects_invalid_finding_enums(self):
        cases = [
            ({"severity": "critical"}, "severity"),
            ({"category": "UNVERIFIABLE"}, "category"),
            ({"fix_or_flag": "rewrite"}, "fix_or_flag"),
        ]
        for updates, phrase in cases:
            with self.subTest(updates=updates):
                finding = self.finding(**updates)
                result = self.result(
                    claims={"total": 1, "certified": 0, "drift": 1, "unverified": 0},
                    findings=[finding],
                )
                self.assert_invalid(result, phrase)

    def test_rejects_non_string_finding_enums_without_raising(self):
        for field in ["severity", "category", "fix_or_flag"]:
            with self.subTest(field=field):
                finding = self.finding(**{field: ["high"]})
                result = self.result(
                    claims={"total": 1, "certified": 0, "drift": 1, "unverified": 0},
                    findings=[finding],
                )
                self.assert_invalid(result, field)

    def test_rejects_unknown_finding_and_unverified_fields(self):
        finding = self.finding(extra="data")
        result = self.result(
            claims={"total": 1, "certified": 0, "drift": 1, "unverified": 0},
            findings=[finding],
        )
        self.assert_invalid(result, "unknown findings[0] fields")

        item = self.unverified(extra="data")
        result = self.result(
            claims={"total": 1, "certified": 0, "drift": 0, "unverified": 1},
            unverified=[item],
        )
        self.assert_invalid(result, "unknown unverified[0] fields")

    def test_rejects_oversized_text_and_collection_fields(self):
        finding = self.finding(why="x" * 5000)
        result = self.result(
            claims={"total": 1, "certified": 0, "drift": 1, "unverified": 0},
            findings=[finding],
        )
        self.assert_invalid(result, "why")

        self.assert_invalid(self.result(errors=["x"] * 101), "errors")


if __name__ == "__main__":
    unittest.main()
