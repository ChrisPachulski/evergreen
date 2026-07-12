import json
import tempfile
import unittest
from pathlib import Path
from unittest import mock


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.repo = Path(self.temporary.name) / "repo"
        self.repo.mkdir()
        (self.repo / "src").mkdir()
        (self.repo / "src" / "api.py").write_text("def api(): pass\n")

    def tearDown(self):
        self.temporary.cleanup()

    def record(self, **changes):
        value = {
            "provider": "drift-shape",
            "version": "1.2.0",
            "type": "signature-changed",
            "path": "src/api.py",
            "line": 1,
            "span": 1,
            "symbol": "api",
            "old": "api()",
            "current": "api(value)",
            "confidence": "deterministic",
            "metadata": {"source": "ast", "tags": ["public"]},
        }
        value.update(changes)
        return value

    def load(self, records):
        from evergreen.evidence import load_evidence

        path = Path(self.temporary.name) / "evidence.json"
        path.write_text(json.dumps(records))
        return load_evidence(path, self.repo)

    def test_loads_sorted_deterministic_and_advisory_facts_as_immutable_values(self):
        advisory = self.record(
            provider="heuristic", confidence="advisory", type="possible-removal",
            path="src/missing.py", line=4, span=None, symbol=None, old="present",
            current=None, metadata={"score": 0.7},
        )
        evidence, warnings = self.load([advisory, self.record()])

        self.assertEqual(warnings, [])
        self.assertEqual([item.provider for item in evidence], ["drift-shape", "heuristic"])
        self.assertEqual(evidence[0].confidence, "deterministic")
        self.assertEqual(evidence[1].confidence, "advisory")
        self.assertEqual(evidence[0].metadata["tags"], ("public",))
        with self.assertRaises(TypeError):
            evidence[0].metadata["source"] = "changed"
        with self.assertRaises(Exception):
            evidence[0].path = "other.py"

        without_metadata = self.record()
        without_metadata.pop("metadata")
        evidence, warnings = self.load([without_metadata])
        self.assertEqual(warnings, [])
        self.assertEqual(dict(evidence[0].metadata), {})

    def test_malformed_file_root_and_records_warn_without_raising(self):
        from evergreen.evidence import load_evidence

        path = Path(self.temporary.name) / "evidence.json"
        path.write_text("not json")
        evidence, warnings = load_evidence(path, self.repo)
        self.assertEqual(evidence, [])
        self.assertTrue(any("invalid JSON" in warning for warning in warnings))

        path.write_text(json.dumps({"evidence": []}))
        evidence, warnings = load_evidence(path, self.repo)
        self.assertEqual(evidence, [])
        self.assertTrue(any("array" in warning for warning in warnings))

        evidence, warnings = self.load(["not an object", self.record()])
        self.assertEqual(len(evidence), 1)
        self.assertTrue(any("record 1" in warning for warning in warnings))

    def test_rejects_traversal_absolute_and_symlink_escape(self):
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        (self.repo / "escape").symlink_to(outside, target_is_directory=True)
        evidence, warnings = self.load([
            self.record(path="../secret"),
            self.record(path=str(outside / "secret")),
            self.record(path="escape/secret"),
        ])

        self.assertEqual(evidence, [])
        self.assertEqual(len(warnings), 3)
        self.assertTrue(all("path" in warning for warning in warnings))

    def test_rejects_invalid_lines_spans_field_types_and_confidence(self):
        invalid = [
            self.record(line=0),
            self.record(line=True),
            self.record(span=0),
            self.record(provider=1),
            self.record(old={"not": "a fact"}),
            self.record(confidence="certain"),
        ]
        evidence, warnings = self.load(invalid)

        self.assertEqual(evidence, [])
        self.assertEqual(len(warnings), len(invalid))

    def test_deduplicates_and_sorts_independently_of_input_order(self):
        first = self.record(provider="z-provider", path="src/z.py")
        second = self.record(provider="a-provider", path="src/a.py")
        evidence, warnings = self.load([first, second, first])

        self.assertEqual([item.provider for item in evidence], ["a-provider", "z-provider"])
        self.assertEqual(len(warnings), 1)
        self.assertIn("duplicate", warnings[0])

    def test_bounds_file_record_count_metadata_size_depth_and_shape(self):
        from evergreen import evidence as module

        path = Path(self.temporary.name) / "evidence.json"
        path.write_text("[]" + " " * 20)
        with mock.patch.object(module, "MAX_FILE_BYTES", 4):
            evidence, warnings = module.load_evidence(path, self.repo)
        self.assertEqual(evidence, [])
        self.assertTrue(any("too large" in warning for warning in warnings))

        with mock.patch.object(module, "MAX_RECORDS", 1):
            evidence, warnings = self.load([self.record(), self.record(provider="two")])
        self.assertEqual(evidence, [])
        self.assertTrue(any("too many records" in warning for warning in warnings))

        oversized = self.record(metadata={"payload": "x" * 100})
        with mock.patch.object(module, "MAX_METADATA_BYTES", 32):
            evidence, warnings = self.load([oversized])
        self.assertEqual(evidence, [])
        self.assertTrue(any("metadata" in warning for warning in warnings))

        deep = value = {}
        for _ in range(module.MAX_METADATA_DEPTH + 1):
            child = {}
            value["child"] = child
            value = child
        evidence, warnings = self.load([self.record(metadata=deep)])
        self.assertEqual(evidence, [])
        self.assertTrue(any("metadata" in warning for warning in warnings))

        evidence, warnings = self.load([self.record(metadata=["not", "an", "object"])])
        self.assertEqual(evidence, [])
        self.assertTrue(any("metadata" in warning for warning in warnings))

    def test_schema_describes_the_same_strict_record_contract(self):
        schema = json.loads((Path(__file__).parents[1] / "schemas/evidence-provider-v1.schema.json").read_text())

        self.assertEqual(schema["type"], "array")
        item = schema["items"]
        self.assertFalse(item["additionalProperties"])
        self.assertEqual(set(item["required"]), {
            "provider", "version", "type", "path", "line", "span", "symbol",
            "old", "current", "confidence",
        })
        self.assertEqual(item["properties"]["confidence"]["enum"], ["deterministic", "advisory"])


if __name__ == "__main__":
    unittest.main()
