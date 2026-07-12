import subprocess
from contextlib import redirect_stdout
from io import StringIO
from types import SimpleNamespace
import unittest
from unittest import mock

from eval.bench import run_bench


def ok(value):
    return {"status": "ok", "value": value}


class ClaudeJSONTests(unittest.TestCase):
    def test_timeout_abstains_after_two_retries(self):
        calls = []

        def timeout(*args, **kwargs):
            calls.append((args, kwargs))
            raise subprocess.TimeoutExpired(args[0], kwargs["timeout"])

        result = run_bench.claude_json("prompt", "model", runner=timeout)

        self.assertEqual(result["status"], "abstain")
        self.assertIn("timeout", result["reason"])
        self.assertEqual(len(calls), 3)

    def test_malformed_response_abstains_after_two_retries(self):
        calls = []

        def malformed(*args, **kwargs):
            calls.append((args, kwargs))
            return SimpleNamespace(stdout="not json", returncode=0)

        result = run_bench.claude_json("prompt", "model", runner=malformed)

        self.assertEqual(result["status"], "abstain")
        self.assertIn("malformed", result["reason"])
        self.assertEqual(len(calls), 3)

    def test_bounded_retry_can_recover_on_third_attempt(self):
        replies = iter([
            SimpleNamespace(stdout="", returncode=1),
            SimpleNamespace(stdout="still not json", returncode=0),
            SimpleNamespace(stdout='{"verdict":"consistent"}\n', returncode=0),
        ])
        calls = []

        def runner(*args, **kwargs):
            calls.append((args, kwargs))
            return next(replies)

        result = run_bench.claude_json("prompt", "model", runner=runner)

        self.assertEqual(result, {"status": "ok", "value": {"verdict": "consistent"}})
        self.assertEqual(len(calls), 3)

    def test_missing_cli_abstains_instead_of_raising(self):
        def missing(*args, **kwargs):
            raise FileNotFoundError("claude not found")

        result = run_bench.claude_json("prompt", "model", runner=missing)

        self.assertEqual(result["status"], "abstain")
        self.assertIn("claude not found", result["reason"])


class JudgeAbstentionTests(unittest.TestCase):
    def setUp(self):
        self.pair = {
            "id": "pair-1",
            "func": "f",
            "code": "def f(): return 1",
            "doc": "f returns 1",
            "language": "python",
        }
        self.models = {"strong": "strong", "cheap": "cheap"}
        self.consistent = {"verdict": "consistent", "category": None, "why": "return 1"}

    def test_missing_snap_verdict_abstains_instead_of_defaulting_consistent(self):
        with mock.patch.object(run_bench, "snap_call", return_value=ok({"why": "missing"})), \
             mock.patch.object(run_bench, "challenge_call", return_value=ok({"cracks": False})), \
             mock.patch.object(run_bench, "run_prongs", return_value=[ok(self.consistent)] * 3), \
             mock.patch.object(run_bench, "blindspot_call", return_value=ok({"missed_angle": None})), \
             mock.patch.object(run_bench, "synthesis_call", return_value=ok(self.consistent)):
            result = run_bench.judge(self.pair, self.models)

        self.assertEqual(result["final_status"], "abstain")
        self.assertIsNone(result["final_verdict"])
        self.assertEqual(result["stages"]["snap"]["status"], "abstain")

    def test_missing_prong_verdict_abstains(self):
        snap = mock.patch.object(run_bench, "snap_call", return_value=ok(self.consistent))
        challenge = mock.patch.object(
            run_bench, "challenge_call", return_value=ok({"cracks": False})
        )
        prongs = mock.patch.object(
            run_bench,
            "run_prongs",
            return_value=[
                ok({"verdict": "consistent", "why": "yes"}),
                ok({"why": "missing verdict"}),
                ok({"verdict": "consistent", "why": "yes"}),
            ],
        )
        blindspot = mock.patch.object(
            run_bench, "blindspot_call", return_value=ok({"missed_angle": None})
        )
        synthesis = mock.patch.object(
            run_bench, "synthesis_call", return_value=ok(self.consistent)
        )
        with snap, challenge, prongs, blindspot, synthesis:
            result = run_bench.judge(self.pair, self.models)

        self.assertEqual(result["final_status"], "abstain")
        self.assertEqual(result["stages"]["prongs"][1]["status"], "abstain")

    def test_missing_or_invalid_challenge_cracks_abstains(self):
        for challenge_value in ({}, {"cracks": "false"}, {"cracks": 1}):
            with self.subTest(challenge=challenge_value), \
                 mock.patch.object(run_bench, "snap_call", return_value=ok(self.consistent)), \
                 mock.patch.object(
                     run_bench, "challenge_call", return_value=ok(challenge_value)
                 ):
                result = run_bench.judge(self.pair, self.models)

            self.assertEqual(result["final_status"], "abstain")
            self.assertEqual(result["stages"]["challenge"]["status"], "abstain")

    def test_missing_or_non_string_blindspot_abstains(self):
        for blindspot_value in ({}, {"missed_angle": 42}, {"missed_angle": False}):
            with self.subTest(blindspot=blindspot_value), \
                 mock.patch.object(run_bench, "snap_call", return_value=ok(self.consistent)), \
                 mock.patch.object(
                     run_bench, "challenge_call", return_value=ok({"cracks": False})
                 ), \
                 mock.patch.object(
                     run_bench, "run_prongs", return_value=[ok(self.consistent)] * 3
                 ), \
                 mock.patch.object(
                     run_bench, "blindspot_call", return_value=ok(blindspot_value)
                 ), \
                 mock.patch.object(
                     run_bench, "synthesis_call", return_value=ok(self.consistent)
                 ):
                result = run_bench.judge(self.pair, self.models)

            self.assertEqual(result["final_status"], "abstain")
            self.assertEqual(result["stages"]["blindspot"]["status"], "abstain")

    def test_escalated_prong_abstention_abstains_the_pair(self):
        initial = [
            ok({"verdict": "consistent"}),
            ok({"verdict": "consistent"}),
            ok({"verdict": "inconsistent"}),
        ]
        escalated = [ok(self.consistent), ok({}), ok(self.consistent)]
        inconsistent = {
            "verdict": "inconsistent",
            "category": "direct-mismatch",
            "why": "x",
        }
        with mock.patch.object(run_bench, "snap_call", return_value=ok(inconsistent)), \
             mock.patch.object(run_bench, "challenge_call", return_value=ok({"cracks": False})), \
             mock.patch.object(run_bench, "run_prongs", side_effect=[initial, escalated]):
            result = run_bench.judge(self.pair, self.models)

        self.assertEqual(result["final_status"], "abstain")
        self.assertEqual(result["stages"]["prongs_escalated"][1]["status"], "abstain")

    def test_missing_synthesis_verdict_abstains(self):
        snap = mock.patch.object(run_bench, "snap_call", return_value=ok(self.consistent))
        challenge = mock.patch.object(
            run_bench, "challenge_call", return_value=ok({"cracks": True})
        )
        prongs = mock.patch.object(
            run_bench,
            "run_prongs",
            return_value=[
                ok({"verdict": "consistent", "why": "yes"}),
                ok({"verdict": "inconsistent", "why": "no"}),
                ok({"verdict": "inconsistent", "why": "no"}),
            ],
        )
        blindspot = mock.patch.object(
            run_bench, "blindspot_call", return_value=ok({"missed_angle": None})
        )
        synthesis = mock.patch.object(run_bench, "synthesis_call", return_value=ok({"why": "missing"}))
        with snap, challenge, prongs, blindspot, synthesis:
            result = run_bench.judge(self.pair, self.models)

        self.assertEqual(result["final_status"], "abstain")
        self.assertEqual(result["stages"]["synthesis"]["status"], "abstain")

    def test_unanimous_plurality_path_still_completes_without_synthesis(self):
        inconsistent = {"verdict": "inconsistent", "category": "direct-mismatch", "why": "x"}
        with mock.patch.object(run_bench, "snap_call", return_value=ok(inconsistent)), \
             mock.patch.object(run_bench, "challenge_call", return_value=ok({"cracks": False})), \
             mock.patch.object(run_bench, "run_prongs", return_value=[ok(inconsistent)] * 3), \
             mock.patch.object(run_bench, "blindspot_call", return_value=ok({"missed_angle": None})), \
             mock.patch.object(run_bench, "synthesis_call") as synthesis:
            result = run_bench.judge(self.pair, self.models)

        self.assertEqual(result["final_status"], "complete")
        self.assertEqual(result["final_verdict"], "inconsistent")
        synthesis.assert_not_called()


class ScoringTests(unittest.TestCase):
    def test_abstentions_are_excluded_from_matrix_and_reported_as_coverage(self):
        rows = [
            {"label": "inconsistent", "category": "direct-mismatch", "final_status": "complete", "final_verdict": "inconsistent"},
            {"label": "inconsistent", "category": "over-promise", "final_status": "abstain", "final_verdict": None},
            {"label": "consistent", "category": None, "final_status": "complete", "final_verdict": "consistent"},
        ]

        result = run_bench.score(rows)

        self.assertEqual((result["tp"], result["fp"], result["fn"], result["tn"]), (1, 0, 0, 1))
        self.assertEqual((result["attempted"], result["completed"], result["abstained"]), (3, 2, 1))
        self.assertAlmostEqual(result["completion_rate"], 2 / 3)

    def test_under_promise_reports_its_own_completion_denominator(self):
        rows = [
            {"label": "inconsistent", "category": "under-promise", "final_status": "complete", "final_verdict": "inconsistent"},
            {"label": "inconsistent", "category": "under-promise", "final_status": "complete", "final_verdict": "consistent"},
            {"label": "inconsistent", "category": "under-promise", "final_status": "abstain", "final_verdict": None},
        ]

        result = run_bench.score(rows)

        self.assertEqual(result["under_flagged"], 1)
        self.assertEqual(
            (result["under_attempted"], result["under_completed"], result["under_abstained"]),
            (3, 2, 1),
        )
        self.assertAlmostEqual(result["under_completion_rate"], 2 / 3)

    def test_mixed_language_report_emits_separate_matrices(self):
        rows = []
        for language in ("python", "go"):
            rows.extend([
                {"language": language, "label": "inconsistent", "category": "direct-mismatch", "final_status": "complete", "final_verdict": "inconsistent"},
                {"language": language, "label": "consistent", "category": None, "final_status": "complete", "final_verdict": "consistent"},
            ])
        output = StringIO()

        with redirect_stdout(output):
            run_bench.report(rows)

        text = output.getvalue()
        self.assertIn("language=go", text)
        self.assertIn("language=python", text)
        self.assertEqual(text.count("core set"), 2)
        self.assertNotIn("core set (consistent + direct-mismatch + over-promise), n=4", text)

    def test_direct_and_rescored_rows_have_identical_scores(self):
        transcript = [
            {
                "id": "a",
                "language": "python",
                "label": "inconsistent",
                "category": "direct-mismatch",
                "got": {"final_status": "complete", "final_verdict": "inconsistent"},
            },
            {"id": "b", "language": "python", "label": "consistent", "category": None, "got": {"final_status": "abstain", "final_verdict": None}},
            {"id": "c", "language": "python", "label": "consistent", "category": None, "got": {"verdict": "consistent"}},
            {"id": "d", "language": "python", "label": "consistent", "category": None},
        ]
        direct = [
            {"language": "python", "label": "inconsistent", "category": "direct-mismatch", "final_status": "complete", "final_verdict": "inconsistent"},
            {"language": "python", "label": "consistent", "category": None, "final_status": "abstain", "final_verdict": None},
            {"language": "python", "label": "consistent", "category": None, "final_status": "complete", "final_verdict": "consistent"},
            {"language": "python", "label": "consistent", "category": None, "final_status": "abstain", "final_verdict": None},
        ]

        rescored_rows = run_bench.rows_from_transcript(transcript)

        self.assertEqual(rescored_rows, direct)
        self.assertEqual(run_bench.score(rescored_rows), run_bench.score(direct))


if __name__ == "__main__":
    unittest.main()
