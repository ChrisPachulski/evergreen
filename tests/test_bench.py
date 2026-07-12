import subprocess
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

    def test_transcript_rescore_matches_direct_rows_and_legacy_complete_rows(self):
        transcript = [
            {
                "id": "a",
                "label": "inconsistent",
                "category": "direct-mismatch",
                "got": {"final_status": "complete", "final_verdict": "inconsistent"},
            },
            {"id": "b", "label": "consistent", "category": None, "got": {"final_status": "abstain", "final_verdict": None}},
            {"id": "c", "label": "consistent", "category": None, "got": {"verdict": "consistent"}},
            {"id": "d", "label": "consistent", "category": None},
        ]

        rescored = run_bench.score(run_bench.rows_from_transcript(transcript))

        self.assertEqual((rescored["tp"], rescored["tn"]), (1, 1))
        self.assertEqual((rescored["attempted"], rescored["completed"], rescored["abstained"]), (4, 2, 2))
        self.assertEqual(rescored["completion_rate"], 0.5)


if __name__ == "__main__":
    unittest.main()
