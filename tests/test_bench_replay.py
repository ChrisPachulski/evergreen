import copy
import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest import mock


from eval.bench.replay import replay_rows, select_split


def ok(value):
    return {"status": "ok", "value": value}


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
                ValueError, "org/repo/f#1: stored=inconsistent replayed=consistent"):
            replay_rows([self.row("inconsistent")], "v1", expect_stored=True)

    def test_replay_never_calls_model_boundary(self):
        with mock.patch("eval.bench.trial.model_json", side_effect=AssertionError("paid path")):
            self.assertEqual(replay_rows([self.row()], "v1")[0]["got"]["final_verdict"],
                             "consistent")


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
