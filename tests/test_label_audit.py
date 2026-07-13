import hashlib
import json
import tempfile
import unittest
from pathlib import Path

from eval.bench import label_audit_core as core


def result_row(identifier, language="Python", label="consistent", status="complete",
               verdict="consistent"):
    return {
        "id": identifier,
        "func": "f",
        "code": "def f():\n    return 1",
        "doc": "Returns one.",
        "label": label,
        "category": None,
        "language": language,
        "got": {"final_status": status, "final_verdict": verdict},
    }


class LabelAuditInputTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)

    def tearDown(self):
        self.temporary.cleanup()

    def artifact(self, rows, name="artifact.json"):
        path = self.root / name
        path.write_text(json.dumps({
            "schema_version": 2,
            "metadata": {"dataset": {"sha256": "d" * 64}},
            "rows": rows,
            "timing": {},
        }))
        return path

    def test_load_artifact_normalizes_language_and_binds_hash(self):
        path = self.artifact([
            result_row("a", label="inconsistent", verdict="inconsistent"),
            result_row("b"),
        ])
        loaded = core.load_artifact(path)
        self.assertEqual(loaded.language, "python")
        self.assertEqual(loaded.row_count, 2)
        self.assertEqual(loaded.sha256, hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertEqual(loaded.items[0].key, ("python", "a"))

    def test_load_artifact_rejects_duplicates_mixed_languages_and_bad_results(self):
        cases = (
            ([result_row("a"), result_row("a")], "duplicate"),
            ([result_row("a"), result_row("b", language="Go")], "one language"),
            ([{k: v for k, v in result_row("a").items() if k != "got"}], "result"),
            ([result_row("a", status="complete", verdict=None)], "verdict"),
            ([result_row("a", status="abstain", verdict="consistent")], "abstain"),
        )
        for rows, message in cases:
            with self.subTest(message=message), self.assertRaisesRegex(ValueError, message):
                core.load_artifact(self.artifact(rows))

    def test_load_artifact_rejects_empty_and_symlink(self):
        with self.assertRaisesRegex(ValueError, "empty"):
            core.load_artifact(self.artifact([]))
        target = self.artifact([result_row("a")], "target.json")
        link = self.root / "link.json"
        link.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "regular file"):
            core.load_artifact(link)

    def test_source_pool_records_incomplete_provenance(self):
        path = self.root / "source.jsonl"
        path.write_text(json.dumps({
            **result_row("a#0-old", language="typescript", label="inconsistent",
                         verdict="inconsistent"),
            "source": None,
        }) + "\n")
        loaded = core.load_source_pool(path, "typescript")
        self.assertEqual(loaded.provenance_status, "unverified")
        self.assertEqual(loaded.rows[0]["source_status"], "missing")

    def test_canonical_language_is_closed(self):
        self.assertEqual(core.canonical_language("TS"), "typescript")
        self.assertEqual(core.canonical_language("Java"), "java")
        with self.assertRaisesRegex(ValueError, "language"):
            core.canonical_language("ruby")


if __name__ == "__main__":
    unittest.main()
