import copy
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest import mock


from eval.bench import trial
from eval.bench.replay import replay_rows, select_split


def ok(value):
    return {"status": "ok", "value": value}


def screen_verdict(value, proof="direct", category=None, uncertain=False,
                    uncertainty_reason=None):
    return {
        "verdict": value, "proof": proof, "category": category,
        "claim": "the documentation claim", "evidence": "return 1",
        "uncertain": uncertain, "uncertainty_reason": uncertainty_reason,
    }


def stages(verdict="consistent"):
    category = "direct-mismatch" if verdict == "inconsistent" else None
    return {
        "snap": ok({"verdict": verdict, "category": category, "why": "evidence"}),
        "challenge": ok({"cracks": False, "why": "no crack"}),
        "prongs": [ok({"role": role, "verdict": verdict, "why": "evidence"})
                   for role in ("defend", "prove-wrong", "hardest-broken")],
        "blindspot": ok({"missed_angle": None}),
    }


def source_row(pair_id, language="Python"):
    return {"id": pair_id, "func": "f", "code": "return 1", "doc": "returns one",
            "label": "consistent", "category": None, "language": language}


class ReplayTests(unittest.TestCase):
    def row(self, stored="consistent"):
        return {**source_row("org/repo/f#1"), "got": {
            "final_status": "complete", "final_verdict": stored, "verdict": stored,
            "category": None, "why": "evidence", "contested": False,
            "stages": stages("consistent"),
        }}

    def test_v1_replay_reproduces_stored_final_without_mutation(self):
        row = self.row()
        before = copy.deepcopy(row)
        replayed = replay_rows([row], "v1", expect_stored=True)
        self.assertEqual(replayed[0]["got"]["final_verdict"], "consistent")
        self.assertEqual(row, before)

    def test_expect_stored_reports_mismatch_by_id(self):
        with self.assertRaisesRegex(
                ValueError,
                "org/repo/f#1: final_verdict stored=inconsistent replayed=consistent"):
            replay_rows([self.row("inconsistent")], "v1", expect_stored=True)

    def test_expect_stored_compares_complete_declared_decision_shape(self):
        for field, value in (
            ("category", "over-promise"), ("why", "different"),
            ("contested", True), ("semantic_status", "decided"),
        ):
            row = self.row()
            row["got"][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(ValueError, field):
                replay_rows([row], "v1", expect_stored=True)

    def test_artifact_hash_uses_the_same_bounded_snapshot_that_is_parsed(self):
        from eval.bench import replay

        document = {"rows": [self.row()]}
        payload = json.dumps(document).encode()
        with mock.patch("eval.bench.replay.read_bytes", return_value=payload) as bounded, \
             mock.patch.object(Path, "read_bytes", side_effect=AssertionError("unbounded")):
            loaded, digest = replay.artifact_snapshot(Path("artifact.json"))

        self.assertEqual(loaded, document)
        self.assertEqual(digest, hashlib.sha256(payload).hexdigest())
        bounded.assert_called_once()

    def test_generated_split_hash_uses_bounded_snapshot(self):
        from eval.bench import replay

        path = Path("out/Python.jsonl")
        payload = b'{"id":"one"}\n'
        with mock.patch.object(replay, "select_split", return_value={"Python": path}), \
             mock.patch.object(replay, "read_bytes", return_value=payload) as bounded, \
             mock.patch.object(Path, "read_bytes", side_effect=AssertionError("unbounded")), \
             mock.patch("builtins.print") as printed:
            status = replay._select_main([
                "--manifest", "manifest.json", "--split", "dev",
                "--labels", "labels.jsonl", "--dataset", "data.jsonl",
                "--output-dir", "out",
            ])

        self.assertEqual(status, 0)
        bounded.assert_called_once_with(
            path, replay.MAX_DATASET_BYTES, label="generated split output"
        )
        printed.assert_called_once_with(
            f"Python\t{hashlib.sha256(payload).hexdigest()}\t{path}"
        )

    def test_replay_never_calls_model_boundary(self):
        with mock.patch("eval.bench.trial.model_json", side_effect=AssertionError("paid path")):
            self.assertEqual(replay_rows([self.row()], "v1")[0]["got"]["final_verdict"],
                             "consistent")


class V3ReplayTests(unittest.TestCase):
    """A real trial.judge(resolver="v3") decision, packaged the way runner.py persists it
    (source row + "got"), must replay cleanly — this is what the trial.py stages-persistence
    fix (see _judge_cascade_v3) buys: without it, a jury row's got["stages"] would be
    resolve_v2's inner trail alone, replay would find no "screen" key, misroute, and abstain."""

    def setUp(self):
        self.pair = {
            "id": "org/repo/f#1", "func": "f", "code": "def f(): return 1",
            "doc": "f returns 1", "language": "python",
        }
        self.models = {"strong": "strong", "cheap": "cheap", "resolver": "v3"}

    def persisted_row(self, run_test, label="consistent", category=None):
        decision = trial.judge(self.pair, self.models, run_test=run_test)
        return {**source_row(self.pair["id"], "python"), "label": label,
                "category": category, "got": decision}

    def clear_row(self):
        def stub(stage, *_args):
            if stage != "screen":
                raise AssertionError(f"jury stage invoked on a clear route: {stage}")
            return ok(screen_verdict("consistent"))
        return self.persisted_row(stub)

    def jury_row(self):
        jury_record = {
            "verdict": "inconsistent", "proof": "direct", "category": "direct-mismatch",
            "claim": "claim", "evidence": "does not return 1",
        }
        jury = {
            "snap": ok(jury_record),
            "challenge": ok({"cracks": False, "why": "held"}),
            "prongs": [ok({**jury_record, "role": role, "cleared_bar": True})
                       for role in ("defend", "prove-wrong", "evidence-auditor")],
            "blindspot": ok({"missed_angle": None}),
        }
        screen_result = ok(screen_verdict("inconsistent"))

        def stub(stage, *_args):
            return screen_result if stage == "screen" else jury[stage]

        return self.persisted_row(stub, label="inconsistent", category="direct-mismatch")

    def test_v3_replay_round_trips_a_persisted_clear_row(self):
        row = self.clear_row()

        with mock.patch("eval.bench.trial.model_json", side_effect=AssertionError("paid path")):
            replayed = replay_rows([row], "v3", expect_stored=True)

        self.assertEqual(replayed[0]["got"]["final_verdict"], "consistent")
        self.assertEqual(replayed[0]["got"]["execution"], row["got"]["execution"])

    def test_v3_replay_round_trips_a_persisted_jury_row(self):
        row = self.jury_row()
        # Prove the persistence fix actually took: the full cascade trail, not just the inner
        # v2 jury trail, is what got persisted.
        self.assertIn("screen", row["got"]["stages"])
        self.assertIn("route", row["got"]["stages"])
        self.assertIn("jury", row["got"]["stages"])

        with mock.patch("eval.bench.trial.model_json", side_effect=AssertionError("paid path")):
            replayed = replay_rows([row], "v3", expect_stored=True)

        self.assertEqual(replayed[0]["got"]["final_verdict"], "inconsistent")
        self.assertEqual(replayed[0]["got"]["category"], "direct-mismatch")
        self.assertEqual(replayed[0]["got"]["execution"], row["got"]["execution"])

    def test_v3_replay_detects_a_tampered_execution_ledger(self):
        row = self.clear_row()
        row["got"]["execution"]["provider_attempts"] += 1

        with self.assertRaisesRegex(ValueError, "execution"):
            replay_rows([row], "v3", expect_stored=True)

    def test_v3_replay_never_calls_model_boundary(self):
        row = self.jury_row()
        with mock.patch("eval.bench.trial.model_json", side_effect=AssertionError("paid path")):
            self.assertEqual(
                replay_rows([row], "v3")[0]["got"]["final_verdict"], "inconsistent"
            )


class SelectSplitTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.rows = [source_row("org/a/f#1"), source_row("org/b/g#1", "Java")]
        self.dataset = self.root / "data.jsonl"
        payload = "".join(json.dumps(row, sort_keys=True) + "\n" for row in self.rows)
        self.dataset.write_text(payload)
        digest = hashlib.sha256(payload.encode()).hexdigest()
        self.manifest = self.root / "split.json"
        self.manifest.write_text(json.dumps({
            "schema_version": 1,
            "datasets": [
                # One-language datasets are required, so tests overwrite this combined fixture.
            ],
            "rows": [],
        }))
        self.python = self.write_dataset("python.jsonl", [self.rows[0]])
        self.java = self.write_dataset("java.jsonl", [self.rows[1]])
        declarations = []
        split_rows = []
        for path, row, split in ((self.python, self.rows[0], "dev"),
                                 (self.java, self.rows[1], "holdout")):
            sha = hashlib.sha256(path.read_bytes()).hexdigest()
            declarations.append({"sha256": sha, "language": row["language"]})
            split_rows.append({"id": row["id"], "dataset_sha256": sha,
                               "project": "/".join(row["id"].split("/")[:2]),
                               "split": split})
        self.manifest.write_text(json.dumps({"schema_version": 1,
                                             "datasets": declarations, "rows": split_rows}))

    def tearDown(self):
        self.directory.cleanup()

    def write_dataset(self, name, rows):
        path = self.root / name
        path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
        return path

    def write_labels(self, rows):
        path = self.root / "labels.jsonl"
        path.write_text("".join(json.dumps(row) + "\n" for row in rows))
        return path

    def test_selects_ids_before_joining_exact_private_labels(self):
        labels = self.write_labels([{"id": "org/a/f#1", "label": "inconsistent",
                                     "category": "direct-mismatch"}])
        output = self.root / "out"
        paths = select_split(self.manifest, "dev", labels, [self.python, self.java], output)
        self.assertEqual(set(paths), {"Python"})
        selected = json.loads(paths["Python"].read_text())
        self.assertEqual(selected["id"], "org/a/f#1")
        self.assertEqual(selected["label"], "inconsistent")
        self.assertNotIn("org/b/g#1", paths["Python"].read_text())

    def test_rejects_label_outside_selected_split_and_missing_label(self):
        outside = self.write_labels([{"id": "org/b/g#1", "label": "consistent",
                                      "category": None}])
        with self.assertRaisesRegex(ValueError, "labels do not exactly cover selected split"):
            select_split(self.manifest, "dev", outside, [self.python, self.java],
                         self.root / "outside")
        missing = self.write_labels([])
        with self.assertRaisesRegex(ValueError, "labels do not exactly cover selected split"):
            select_split(self.manifest, "dev", missing, [self.python, self.java],
                         self.root / "missing")

    def test_context_derivative_may_change_only_context(self):
        labels = self.write_labels([{"id": "org/b/g#1", "label": "consistent",
                                     "category": None}])
        contextual = [{**self.rows[1], "context": {"status": "available", "snippets": []}}]
        context_path = self.write_dataset("java-context.jsonl", contextual)
        paths = select_split(
            self.manifest, "holdout", labels, [self.python, self.java], self.root / "context-out",
            context_datasets={"Java": context_path},
        )
        selected = json.loads(paths["Java"].read_text())
        self.assertEqual(selected["context"]["status"], "available")

        bad = [{**contextual[0], "doc": "changed"}]
        bad_path = self.write_dataset("java-bad.jsonl", bad)
        with self.assertRaisesRegex(ValueError, "non-context fields"):
            select_split(
                self.manifest, "holdout", labels, [self.python, self.java], self.root / "bad-out",
                context_datasets={"Java": bad_path},
            )

    def test_invalid_manifest_is_checked_before_labels_are_opened(self):
        with self.assertRaisesRegex(ValueError, "dataset declarations"):
            select_split(self.manifest, "dev", self.root / "does-not-exist.labels",
                         [self.python], self.root / "out")


if __name__ == "__main__":
    unittest.main()
