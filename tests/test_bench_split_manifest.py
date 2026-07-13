import hashlib
import json
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest


from eval.bench.split_manifest import load_split_assignments, load_split_manifest


def dataset_row(pair_id, language="Java", label="consistent"):
    return {
        "id": pair_id,
        "func": "f",
        "code": "return 1",
        "doc": "returns one",
        "label": label,
        "category": None,
        "language": language,
    }


class SplitManifestTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)
        self.rows = [dataset_row("org/a/f#1"), dataset_row("org/b/g#1", label="inconsistent")]
        self.dataset = self.root / "java.jsonl"
        payload = "".join(json.dumps(row, sort_keys=True) + "\n" for row in self.rows)
        self.dataset.write_text(payload)
        self.digest = hashlib.sha256(payload.encode()).hexdigest()

    def tearDown(self):
        self.directory.cleanup()

    def write_manifest(self, rows=None, **changes):
        document = {
            "schema_version": 1,
            "datasets": [{"sha256": self.digest, "language": "Java"}],
            "rows": rows or [
                {"id": "org/a/f#1", "dataset_sha256": self.digest,
                 "project": "org/a", "split": "dev"},
                {"id": "org/b/g#1", "dataset_sha256": self.digest,
                 "project": "org/b", "split": "holdout"},
            ],
        }
        document.update(changes)
        path = self.root / "split.json"
        path.write_text(json.dumps(document))
        return path

    def test_accepts_complete_project_grouped_id_only_manifest(self):
        manifest = self.write_manifest()
        result = load_split_manifest(manifest, [self.dataset])
        self.assertEqual(result, {"org/a/f#1": "dev", "org/b/g#1": "holdout"})
        self.assertEqual(load_split_assignments(manifest), result)

    def test_rejects_project_leakage_between_splits(self):
        rows = [
            {"id": "org/a/f#1", "dataset_sha256": self.digest,
             "project": "org/a", "split": "dev"},
            {"id": "org/b/g#1", "dataset_sha256": self.digest,
             "project": "org/a", "split": "holdout"},
        ]
        with self.assertRaisesRegex(ValueError, "invalid.*project"):
            load_split_manifest(self.write_manifest(rows), [self.dataset])

    def test_rejects_incomplete_unknown_and_duplicate_rows(self):
        valid = {"id": "org/a/f#1", "dataset_sha256": self.digest,
                 "project": "org/a", "split": "dev"}
        cases = {
            "incomplete": [valid],
            "unknown": [valid, {"id": "org/x/z#1", "dataset_sha256": self.digest,
                                 "project": "org/x", "split": "holdout"}],
            "duplicate": [valid, valid],
        }
        for name, rows in cases.items():
            with self.subTest(name=name), self.assertRaises(ValueError):
                load_split_manifest(self.write_manifest(rows), [self.dataset])

    def test_rejects_wrong_hash_unknown_split_and_forbidden_fields(self):
        base = [
            {"id": "org/a/f#1", "dataset_sha256": self.digest,
             "project": "org/a", "split": "dev"},
            {"id": "org/b/g#1", "dataset_sha256": self.digest,
             "project": "org/b", "split": "holdout"},
        ]
        variants = []
        wrong_hash = [dict(item) for item in base]
        wrong_hash[0]["dataset_sha256"] = "0" * 64
        variants.append(wrong_hash)
        wrong_split = [dict(item) for item in base]
        wrong_split[0]["split"] = "test"
        variants.append(wrong_split)
        forbidden = [dict(item) for item in base]
        forbidden[0]["label"] = "consistent"
        variants.append(forbidden)
        for rows in variants:
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                load_split_manifest(self.write_manifest(rows), [self.dataset])

    def test_rejects_manifest_dataset_hash_or_language_mismatch(self):
        with self.assertRaisesRegex(ValueError, "dataset declarations"):
            load_split_manifest(
                self.write_manifest(datasets=[{"sha256": "0" * 64, "language": "Java"}]),
                [self.dataset],
            )
        with self.assertRaisesRegex(ValueError, "dataset declarations"):
            load_split_manifest(
                self.write_manifest(datasets=[{"sha256": self.digest, "language": "Python"}]),
                [self.dataset],
            )

    def test_cli_reports_only_counts(self):
        completed = subprocess.run(
            [sys.executable, "-m", "eval.bench.split_manifest",
             str(self.write_manifest()), str(self.dataset)],
            cwd=Path(__file__).parents[1], capture_output=True, text=True, check=True,
        )
        self.assertEqual(
            completed.stdout,
            "split manifest valid: 2 rows; 2 projects do not cross dev/holdout\n",
        )
        self.assertNotIn("returns one", completed.stdout)


if __name__ == "__main__":
    unittest.main()
