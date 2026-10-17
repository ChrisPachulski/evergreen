import json
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest import mock

from ci import result_protocol
from ci.result_protocol import load_validated_result, parse_result, validate_result
from ci.review_context import claim_id


CLAIM_TEXT = "Run `shipit --workers 4`."
CLAIM = claim_id("docs/usage.md", "2", CLAIM_TEXT)


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

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def result(self, **updates):
        result = {
            "schema_version": 3,
            "status": "complete",
            "base": self.base,
            "head": self.head,
            "verdicts": self.verdicts("certified"),
            "findings": [],
            "unverified": [],
            "errors": [],
            "runtime": {"provider": "anthropic", "model": "test-model", "cli_version": "1.2.3"},
        }
        result.update(updates)
        return result

    def verdicts(self, verdict, claim=CLAIM):
        return [{"id": claim, "verdict": verdict}]

    def validate(self, result, claims=None, controls=None):
        return validate_result(
            result, self.repo, self.base, self.head,
            claims=self.claims if claims is None else claims, controls=controls,
        )

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

    def unverified(self, **updates):
        item = {
            "id": CLAIM,
            "doc_path": "docs/usage.md",
            "doc_line": 2,
            "claim": "Run `shipit --workers 4`.",
            "reason": "Runtime ordering cannot be proven by reading.",
        }
        item.update(updates)
        return item

    def assert_invalid(self, result, phrase):
        errors = self.validate(result)
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
            '{"schema_version": 2}\n{"schema_version": 2}',
            "```evergreen-result\n{}\n```\n```evergreen-result\n{}\n```",
        ]
        for text in bad_inputs:
            with self.subTest(text=text):
                with self.assertRaises(ValueError):
                    parse_result(text)

    def test_accepts_explicit_clean_result(self):
        text = json.dumps(self.result())
        self.assertEqual(self.validate(self.result()), [])
        loaded, errors = load_validated_result(
            text, self.repo, self.base, self.head, claims=self.claims
        )
        self.assertEqual(errors, [])
        self.assertEqual(loaded["claims"], {"total": 1, "certified": 1, "drift": 0, "unverified": 0})
        self.assertNotIn("controls", loaded)

    def test_accepts_findings_and_checks_head_content_not_worktree_content(self):
        finding = self.finding()
        result = self.result(
            verdicts=self.verdicts("drift"),
            findings=[finding],
        )
        self.write("docs/usage.md", "working tree no longer contains the claim\n")
        self.write("src/cli.py", "working tree has only one line\n")
        self.assertEqual(self.validate(result), [])

    def test_hanging_citation_git_is_a_bounded_validation_error(self):
        finding = self.finding()
        result = self.result(
            verdicts=self.verdicts("drift"),
            findings=[finding],
        )
        with mock.patch.object(
            result_protocol, "run_bounded",
            return_value=(124, b"", "command timed out after 0.1 seconds"),
            create=True,
        ) as run:
            errors = self.validate(result)

        self.assertTrue(any("timed out" in error for error in errors))
        self.assertTrue(run.called)

    def test_citation_validation_has_one_aggregate_deadline(self):
        result = self.result(
            verdicts=self.verdicts("drift"),
            findings=[self.finding()],
        )
        with mock.patch.object(result_protocol, "CITATION_TIMEOUT_SECONDS", 1), \
             mock.patch("time.monotonic", side_effect=[0, 2]), \
             mock.patch.object(result_protocol, "run_bounded",
                               return_value=(0, b"", None)) as run:
            errors = self.validate(result)

        self.assertTrue(any("citation validation" in error and "wall-clock limit" in error
                            for error in errors), errors)
        run.assert_not_called()

    def test_citation_validation_stops_at_one_git_call_budget(self):
        result = self.result(
            verdicts=self.verdicts("drift"),
            findings=[self.finding()],
        )
        entry = b"100644 blob " + b"a" * 40 + b"\tdocs/usage.md\0"
        with mock.patch.object(result_protocol, "MAX_CITATION_GIT_CALLS", 1, create=True), \
             mock.patch.object(result_protocol, "run_bounded",
                               return_value=(0, entry, None)) as run:
            errors = self.validate(result)

        self.assertTrue(any("citation Git call limit" in error for error in errors), errors)
        self.assertEqual(run.call_count, 1)

    def test_accepts_release_identity_drift_findings(self):
        finding = self.finding(category="release_identity_drift")
        result = self.result(
            verdicts=self.verdicts("drift"),
            findings=[finding],
        )

        self.assertEqual(self.validate(result), [])

    def test_protocol_docs_list_every_accepted_finding_category(self):
        root = Path(__file__).parents[1]
        expected = {
            "in_code_not_docs", "in_docs_not_code", "name_mismatch",
            "release_identity_drift",
        }
        self.assertEqual(result_protocol.CATEGORIES, expected)
        for relative in ("commands/winnow.md", "docs/DESIGN.md"):
            text = (root / relative).read_text(encoding="utf-8")
            with self.subTest(relative=relative):
                for category in expected:
                    self.assertIn(category, text)

    def test_accepts_unverified_claims(self):
        item = self.unverified()
        result = self.result(
            verdicts=self.verdicts("unverified"),
            unverified=[item],
        )
        self.assertEqual(self.validate(result), [])

    def test_load_returns_none_and_parse_error(self):
        result, errors = load_validated_result(
            "prose only", self.repo, self.base, self.head, claims=self.claims
        )
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
        self.assert_invalid(self.result(schema_version=3.0), "schema_version")
        self.assert_invalid(self.result(status=["complete"]), "status")

    def test_rejects_missing_or_extra_envelope_fields(self):
        missing = self.result()
        del missing["runtime"]
        self.assert_invalid(missing, "missing result fields")
        self.assert_invalid(self.result(extra="ignored"), "unknown result fields")

    def test_rejects_model_reported_claim_counts(self):
        counts = {"total": 1, "certified": 1, "drift": 0, "unverified": 0}
        self.assert_invalid(self.result(claims=counts), "unknown result fields: claims")

    def test_rejects_missing_duplicate_and_unknown_claim_ids(self):
        cases = [
            ([], "1 missing claim id(s): " + CLAIM),
            (self.verdicts("certified") * 2, "1 duplicate claim id(s): " + CLAIM),
            (self.verdicts("certified") + self.verdicts("drift"), "1 duplicate claim id(s)"),
            (self.verdicts("certified") + self.verdicts("certified", "f" * 16),
             "1 unknown claim id(s): " + "f" * 16),
            (self.verdicts("certified", "f" * 16), "1 missing claim id(s)"),
        ]
        for verdicts, phrase in cases:
            with self.subTest(verdicts=verdicts):
                self.assert_invalid(self.result(verdicts=verdicts), phrase)

    def test_rejects_malformed_verdict_entries_without_raising(self):
        cases = [
            ({"verdicts": {"id": CLAIM}}, "verdicts must be an array"),
            ({"verdicts": [{"id": CLAIM, "verdict": "clean"}]}, "verdicts[0].verdict"),
            ({"verdicts": [{"id": [CLAIM], "verdict": "certified"}]}, "verdicts[0].id"),
            ({"verdicts": [{"id": CLAIM}]}, "missing verdicts[0] fields"),
            ({"verdicts": self.verdicts("certified") * (result_protocol.MAX_ITEMS + 2)},
             "entries for 1 supplied claims"),
        ]
        for updates, phrase in cases:
            with self.subTest(updates=updates):
                self.assert_invalid(self.result(**updates), phrase)

    def test_derives_claim_totals_from_trusted_verdicts(self):
        second = claim_id("docs/usage.md", "1", "# Usage")
        third = claim_id("src/cli.py", "1", "def main():")
        claims = [
            *self.claims,
            {"id": second, "path": "docs/usage.md", "line": 1, "text": "# Usage"},
            {"id": third, "path": "src/cli.py", "line": 1, "text": "def main():"},
        ]
        result = self.result(
            verdicts=[
                {"id": third, "verdict": "certified"},
                {"id": CLAIM, "verdict": "drift"},
                {"id": second, "verdict": "unverified"},
            ],
            findings=[self.finding()],
            unverified=[self.unverified(id=second, doc_line=1, claim="# Usage")],
        )

        self.assertEqual(self.validate(result, claims=claims), [])
        self.assertEqual(
            result["claims"], {"total": 3, "certified": 1, "drift": 1, "unverified": 1}
        )

        empty = self.result(verdicts=[])
        self.assertEqual(self.validate(empty, claims=[]), [])
        self.assertEqual(empty["claims"], {"total": 0, "certified": 0, "drift": 0, "unverified": 0})

    def test_binds_every_finding_and_unverified_entry_to_its_claim(self):
        cases = [
            (self.result(verdicts=self.verdicts("drift")), "1 drift verdict(s) have no matching findings"),
            (self.result(verdicts=self.verdicts("unverified")),
             "1 unverified verdict(s) have no matching unverified"),
            (self.result(findings=[self.finding()]), "findings[0].id is not a supplied claim with verdict drift"),
            (self.result(unverified=[self.unverified()]),
             "unverified[0].id is not a supplied claim with verdict unverified"),
            (self.result(verdicts=self.verdicts("drift"), findings=[self.finding()] * 2),
             "findings repeat a claim id"),
            (self.result(verdicts=self.verdicts("drift"), findings=[self.finding(doc_line=1, claim="# Usage")]),
             "findings[0] does not cite its claim's documentation line"),
            (self.result(verdicts=self.verdicts("drift"), findings=[self.finding(id="f" * 16)]),
             "findings[0].id is not a supplied claim"),
        ]
        for result, phrase in cases:
            with self.subTest(phrase=phrase):
                self.assert_invalid(result, phrase)
        missing_id = self.finding()
        del missing_id["id"]
        self.assert_invalid(
            self.result(verdicts=self.verdicts("drift"), findings=[missing_id]),
            "missing findings[0] fields: id",
        )

    def controlled(self, negative="certified", positive="drift", **updates):
        controls = {"negative": "a" * 16, "positive": "b" * 16}
        claims = [
            *self.claims,
            {"id": controls["negative"], "path": "docs/usage.md", "line": 2,
             "text": "docs/usage.md line 2 reads exactly: " + CLAIM_TEXT},
            {"id": controls["positive"], "path": "docs/usage.md", "line": 2,
             "text": "docs/usage.md line 2 reads exactly: # Usage"},
        ]
        verdicts = [
            {"id": controls["positive"], "verdict": positive},
            *self.verdicts("certified"),
            {"id": controls["negative"], "verdict": negative},
        ]
        result = self.result(verdicts=verdicts, **updates)
        return result, claims, controls

    def test_controls_pass_and_leave_no_trace_in_counts(self):
        result, claims, controls = self.controlled()

        self.assertEqual(self.validate(result, claims=claims, controls=controls), [])
        self.assertEqual(result["status"], "complete")
        self.assertEqual(result["controls"], {"negative": "pass", "positive": "pass"})
        self.assertEqual(result["claims"], {"total": 1, "certified": 1, "drift": 0, "unverified": 0})
        self.assertEqual(result["verdicts"], self.verdicts("certified"))
        self.assertEqual(result["errors"], [])

    def test_a_wrong_control_answer_makes_the_run_inconclusive(self):
        cases = [
            ({"positive": "certified"}, {"negative": "pass", "positive": "fail"}),
            ({"positive": "unverified"}, {"negative": "pass", "positive": "fail"}),
            ({"negative": "drift"}, {"negative": "fail", "positive": "pass"}),
            ({"negative": "drift", "positive": "certified"}, {"negative": "fail", "positive": "fail"}),
        ]
        for answers, grades in cases:
            with self.subTest(answers=answers):
                result, claims, controls = self.controlled(**answers)
                self.assertEqual(self.validate(result, claims=claims, controls=controls), [])
                self.assertEqual(result["controls"], grades)
                self.assertEqual(result["status"], "inconclusive")
                self.assertTrue(any("calibration control" in error for error in result["errors"]))
                self.assertFalse(any("reads exactly" in error for error in result["errors"]))
                self.assertEqual(result["claims"]["total"], 1)

    def test_control_findings_and_unverified_entries_are_stripped_before_citation(self):
        positive_finding = self.finding(id="b" * 16, claim="docs/usage.md line 2 reads exactly: # Usage",
                                        code_path="missing/at/head.py")
        negative_note = self.unverified(id="a" * 16, doc_path="gone.md", claim="anything")
        result, claims, controls = self.controlled(
            findings=[positive_finding], unverified=[negative_note]
        )

        self.assertEqual(self.validate(result, claims=claims, controls=controls), [])
        self.assertEqual(result["findings"], [])
        self.assertEqual(result["unverified"], [])
        self.assertEqual(result["controls"], {"negative": "pass", "positive": "pass"})

    def test_control_entries_still_meet_collection_bounds_and_shape(self):
        # The input that used to validate as complete: control entries were dropped before the
        # 100-item bound and the shape checks ran.
        flood, claims, controls = self.controlled(
            findings=[{"id": "b" * 16}] * 1000,
            unverified=[{"id": "a" * 16, "reason": "x" * 5000}],
        )
        errors = self.validate(flood, claims=claims, controls=controls)
        self.assertIn(f"findings exceeds {result_protocol.MAX_ITEMS} items", errors)
        self.assertTrue(any("missing unverified[0] fields" in error for error in errors), errors)

        malformed, claims, controls = self.controlled(
            unverified=[self.unverified(id="a" * 16, reason="x" * 5000)]
        )
        errors = self.validate(malformed, claims=claims, controls=controls)
        self.assertTrue(any("unverified[0].reason exceeds" in error for error in errors), errors)

    def test_control_ids_still_need_exactly_one_verdict(self):
        result, claims, controls = self.controlled()
        result["verdicts"] = [item for item in result["verdicts"] if item["id"] != "b" * 16]
        self.assertTrue(any(
            "1 missing claim id(s): " + "b" * 16 in error
            for error in self.validate(result, claims=claims, controls=controls)
        ))

    def test_rejects_an_answer_key_that_does_not_match_the_inventory(self):
        result, claims, _controls = self.controlled()
        for controls in ({"negative": "a" * 16}, {"negative": "a" * 16, "positive": "c" * 16},
                         {"negative": "a" * 16, "positive": "b" * 16, "extra": CLAIM}):
            with self.subTest(controls=controls):
                errors = self.validate(result, claims=claims, controls=controls)
                self.assertTrue(any("answer key" in error for error in errors), errors)

    def test_rejects_complete_result_with_errors(self):
        self.assert_invalid(self.result(errors=["model timed out"]), "complete result")

    def test_rejects_absolute_traversal_and_non_normalized_paths(self):
        for path in [str((self.repo / "docs/usage.md").resolve()), "../outside.md", "./docs/usage.md", "docs\\usage.md"]:
            with self.subTest(path=path):
                finding = self.finding(doc_path=path)
                result = self.result(
                    verdicts=self.verdicts("drift"),
                    findings=[finding],
                )
                self.assert_invalid(result, "doc_path")

    def test_rejects_nul_and_lone_surrogate_paths_without_raising(self):
        for path in ["docs/\0usage.md", "docs/\ud800.md"]:
            with self.subTest(path=repr(path)):
                finding = self.finding(doc_path=path)
                result = self.result(
                    verdicts=self.verdicts("drift"),
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
                verdicts=self.verdicts("drift"),
                findings=[finding],
            )
            self.assert_invalid(result, "symlink")
        finally:
            outside.unlink(missing_ok=True)

    def test_rejects_git_tree_symlink_even_if_worktree_is_regular(self):
        link = self.repo / "linked.md"
        link.symlink_to("docs/usage.md")
        self.git("add", "linked.md")
        self.head = self.commit("tree symlink")
        link.unlink()
        link.write_text("Run `shipit --workers 4`.\n", encoding="utf-8")

        finding = self.finding(doc_path="linked.md", doc_line=1)
        result = self.result(
            head=self.head,
            verdicts=self.verdicts("drift"),
            findings=[finding],
        )
        self.assert_invalid(result, "symlink")

    def test_uses_expected_tree_when_worktree_file_becomes_escape_symlink(self):
        outside = self.repo.parent / "outside-mutable-worktree.md"
        outside.write_text("untrusted mutable content\n", encoding="utf-8")
        doc = self.repo / "docs/usage.md"
        doc.unlink()
        doc.symlink_to(outside)
        try:
            finding = self.finding()
            result = self.result(
                verdicts=self.verdicts("drift"),
                findings=[finding],
            )
            self.assertEqual(self.validate(result), [])
        finally:
            outside.unlink(missing_ok=True)

    def test_rejects_symlink_loop_without_raising(self):
        loop = self.repo / "loop.md"
        loop.symlink_to("loop.md")
        self.git("add", "loop.md")
        self.head = self.commit("symlink loop")
        finding = self.finding(doc_path="loop.md", doc_line=1)
        result = self.result(
            head=self.head,
            verdicts=self.verdicts("drift"),
            findings=[finding],
        )
        self.assert_invalid(result, "symlink")

    def test_rejects_invalid_or_missing_citation_lines(self):
        for updates in [{"doc_line": 0}, {"doc_line": 99}, {"code_line": -1}, {"code_line": 99}]:
            with self.subTest(updates=updates):
                finding = self.finding(**updates)
                result = self.result(
                    verdicts=self.verdicts("drift"),
                    findings=[finding],
                )
                self.assert_invalid(result, "line")

    def test_rejects_claim_not_found_on_cited_documentation_line(self):
        finding = self.finding(claim="A different claim")
        result = self.result(
            verdicts=self.verdicts("drift"),
            findings=[finding],
        )
        self.assert_invalid(result, "claim does not occur")

    def test_counts_only_lf_as_a_repository_citation_line(self):
        separators = ["\v", "\f", "\x85", "\u2028", "\u2029"]
        for index, separator in enumerate(separators):
            self.write(f"docs/separator-{index}.md", f"prefix{separator}Claim\n")
        self.head = self.commit("non-LF separators")

        for index, separator in enumerate(separators):
            with self.subTest(separator=repr(separator)):
                finding = self.finding(
                    doc_path=f"docs/separator-{index}.md",
                    doc_line=2,
                    claim="Claim",
                )
                result = self.result(
                    head=self.head,
                    verdicts=self.verdicts("drift"),
                    findings=[finding],
                )
                self.assert_invalid(result, "line")

    def test_rejects_missing_empty_or_multiline_claims(self):
        for claim in [None, "", "Run `shipit`\nsecond line"]:
            with self.subTest(claim=claim):
                finding = self.finding()
                if claim is None:
                    del finding["claim"]
                else:
                    finding["claim"] = claim
                result = self.result(
                    verdicts=self.verdicts("drift"),
                    findings=[finding],
                )
                self.assert_invalid(result, "claim")

    def test_rejects_whitespace_only_claim_and_why(self):
        for updates, phrase in [({"claim": " \t"}, "claim"), ({"why": " \t"}, "why")]:
            with self.subTest(updates=updates):
                finding = self.finding(**updates)
                result = self.result(
                    verdicts=self.verdicts("drift"),
                    findings=[finding],
                )
                errors = self.validate(result)
                self.assertTrue(
                    any(phrase in error and "non-whitespace" in error for error in errors),
                    errors,
                )

    def test_rejects_invalid_finding_enums(self):
        cases = [
            ({"severity": "critical"}, "severity"),
            ({"category": "UNVERIFIABLE"}, "category"),
            ({"fix_or_flag": "rewrite"}, "fix_or_flag"),
            ({"rung": "vibes"}, "rung"),
        ]
        for updates, phrase in cases:
            with self.subTest(updates=updates):
                finding = self.finding(**updates)
                result = self.result(
                    verdicts=self.verdicts("drift"),
                    findings=[finding],
                )
                self.assert_invalid(result, phrase)

    def test_rejects_non_string_finding_enums_without_raising(self):
        for field in ["severity", "category", "rung", "fix_or_flag"]:
            with self.subTest(field=field):
                finding = self.finding(**{field: ["high"]})
                result = self.result(
                    verdicts=self.verdicts("drift"),
                    findings=[finding],
                )
                self.assert_invalid(result, field)

    def test_rejects_unknown_finding_and_unverified_fields(self):
        finding = self.finding(extra="data")
        result = self.result(
            verdicts=self.verdicts("drift"),
            findings=[finding],
        )
        self.assert_invalid(result, "unknown findings[0] fields")

        item = self.unverified(extra="data")
        result = self.result(
            verdicts=self.verdicts("unverified"),
            unverified=[item],
        )
        self.assert_invalid(result, "unknown unverified[0] fields")

    def test_rejects_oversized_text_and_collection_fields(self):
        finding = self.finding(why="x" * 5000)
        result = self.result(
            verdicts=self.verdicts("drift"),
            findings=[finding],
        )
        self.assert_invalid(result, "why")

        self.assert_invalid(self.result(errors=["x"] * 101), "errors")

    def test_bounds_head_blob_reads(self):
        self.write(
            "docs/huge.md",
            "Documented claim\n" + "x" * (result_protocol.MAX_CITATION_BYTES + 1),
        )
        self.head = self.commit("huge citation")
        finding = self.finding(
            doc_path="docs/huge.md",
            doc_line=1,
            claim="Documented claim",
        )
        result = self.result(
            head=self.head,
            verdicts=self.verdicts("drift"),
            findings=[finding],
        )
        self.assert_invalid(result, "citation exceeds")

    def test_caches_head_blob_reads_by_path(self):
        heading = claim_id("docs/usage.md", "1", "# Usage")
        claims = [*self.claims, {"id": heading, "path": "docs/usage.md", "line": 1, "text": "# Usage"}]
        findings = [self.finding(), self.finding(id=heading, doc_line=1, claim="# Usage")]
        result = self.result(
            verdicts=self.verdicts("drift") + self.verdicts("drift", heading),
            findings=findings,
        )
        real_run = result_protocol.run_bounded
        with mock.patch.object(result_protocol, "run_bounded", wraps=real_run) as run:
            self.assertEqual(self.validate(result, claims=claims), [])
        cat_file_calls = [
            call for call in run.call_args_list
            if "cat-file" in call.args[0]
        ]
        self.assertEqual(len(cat_file_calls), 2)


if __name__ == "__main__":
    unittest.main()
