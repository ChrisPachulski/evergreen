import json
import os
import re
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

        evidence, warnings = self.load([self.record(metadata={
            "z": {"z": 1, "a": 2}, "a": 0,
        })])
        self.assertEqual(warnings, [])
        self.assertEqual(list(evidence[0].metadata), ["a", "z"])
        self.assertEqual(list(evidence[0].metadata["z"]), ["a", "z"])

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

    def test_rejects_duplicate_json_keys_at_record_and_metadata_depths(self):
        from evergreen.evidence import load_evidence

        path = Path(self.temporary.name) / "evidence.json"
        base = json.dumps(self.record())
        duplicate_record = base.replace(
            '"provider": "drift-shape"',
            '"provider": "first", "provider": "second"',
        )
        valid = json.dumps(self.record(provider="valid"))
        path.write_text(f"[{duplicate_record},{valid}]")
        evidence, warnings = load_evidence(path, self.repo)
        self.assertEqual([item.provider for item in evidence], ["valid"])
        self.assertEqual(warnings, ["record 1: duplicate JSON key: provider"])

        duplicate_metadata = base.replace(
            '"metadata": {"source": "ast", "tags": ["public"]}',
            '"metadata": {"source": "ast", "source": "tokens"}',
        )
        path.write_text(f"[{valid},{duplicate_metadata}]")
        evidence, warnings = load_evidence(path, self.repo)
        self.assertEqual([item.provider for item in evidence], ["valid"])
        self.assertEqual(warnings, ["record 2: duplicate JSON key: source"])

        path.write_text('{"version": 1, "version": 2, "evidence": []}')
        evidence, warnings = load_evidence(path, self.repo)
        self.assertEqual(evidence, [])
        self.assertEqual(warnings, ["evidence file contains duplicate JSON key: version"])

    def test_rejects_traversal_absolute_and_symlink_escape(self):
        outside = Path(self.temporary.name) / "outside"
        outside.mkdir()
        (self.repo / "escape").symlink_to(outside, target_is_directory=True)
        evidence, warnings = self.load([
            self.record(path="../secret"),
            self.record(path=str(outside / "secret")),
            self.record(path="escape/secret"),
            self.record(path="src/bad\nname.py"),
        ])

        self.assertEqual(evidence, [])
        self.assertEqual(len(warnings), 4)
        self.assertTrue(all("path" in warning for warning in warnings))

    def test_rejects_symlink_loops_as_invalid_records(self):
        (self.repo / "loop").symlink_to("loop")
        evidence, warnings = self.load([self.record(path="loop/file.py")])

        self.assertEqual(evidence, [])
        self.assertEqual(len(warnings), 1)
        self.assertIn("path cannot be resolved", warnings[0])

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

    @unittest.skipUnless(hasattr(os, "mkfifo"), "FIFO test requires POSIX")
    def test_rejects_non_regular_evidence_files_before_reading(self):
        from evergreen.evidence import load_evidence

        fifo = Path(self.temporary.name) / "evidence.fifo"
        os.mkfifo(fifo)
        evidence, warnings = load_evidence(fifo, self.repo)

        self.assertEqual(evidence, [])
        self.assertEqual(warnings, ["evidence input must be a regular file"])

        if Path("/dev/null").exists():
            evidence, warnings = load_evidence(Path("/dev/null"), self.repo)
            self.assertEqual(evidence, [])
            self.assertEqual(warnings, ["evidence input must be a regular file"])

        regular = Path(self.temporary.name) / "regular.json"
        regular.write_text("[]")
        linked = Path(self.temporary.name) / "linked.json"
        linked.symlink_to(regular)
        evidence, warnings = load_evidence(linked, self.repo)
        self.assertEqual(evidence, [])
        self.assertEqual(warnings, ["evidence input must be a regular file"])

    def test_utf8_byte_limits_and_schema_annotations_match(self):
        schema = json.loads(
            (Path(__file__).parents[1] / "schemas/evidence-provider-v1.schema.json").read_text()
        )
        properties = schema["items"]["properties"]
        expected = {
            "provider": 256, "version": 256, "type": 256, "path": 4096,
            "symbol": 8192, "old": 8192, "current": 8192,
        }
        for name, byte_limit in expected.items():
            with self.subTest(name=name):
                self.assertEqual(properties[name]["x-maxUtf8Bytes"], byte_limit)
                self.assertEqual(properties[name]["maxLength"], byte_limit)

        evidence, warnings = self.load([self.record(provider="a" * 256)])
        self.assertEqual(len(evidence), 1)
        self.assertEqual(warnings, [])
        evidence, warnings = self.load([self.record(provider="é" * 256)])
        self.assertEqual(evidence, [])
        self.assertTrue(any("provider" in warning for warning in warnings))

    def test_verdict_fields_are_absent_rejected_and_schema_is_candidate_only(self):
        from evergreen.evidence import Evidence

        self.assertFalse(any(name in Evidence.__dataclass_fields__ for name in (
            "verdict", "finding", "drift", "status",
        )))
        for field in ("verdict", "finding", "drift", "status"):
            with self.subTest(field=field):
                evidence, warnings = self.load([self.record(**{field: "inconsistent"})])
                self.assertEqual(evidence, [])
                self.assertTrue(any("candidate-only" in warning for warning in warnings))

        schema = json.loads(
            (Path(__file__).parents[1] / "schemas/evidence-provider-v1.schema.json").read_text()
        )
        self.assertIn("candidate-only", schema["description"])
        self.assertTrue({"verdict", "finding", "drift", "status"}.isdisjoint(
            schema["items"]["properties"]
        ))

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
        path = item["properties"]["path"]
        self.assertIn("normalized POSIX", path["description"])
        self.assertEqual(path["x-repositoryContainment"], "resolved-within-repo")
        self.assertTrue(re.fullmatch(path["pattern"], "src/api.py"))
        for invalid in ("/abs", "../x", "src/../x", "src//x", "src/./x", "src\\x", "src/x\n"):
            with self.subTest(path=invalid):
                self.assertIsNone(re.fullmatch(path["pattern"], invalid))
        metadata = item["properties"]["metadata"]
        self.assertEqual(metadata["x-maxUtf8Bytes"], 16384)
        self.assertEqual(metadata["x-maxDepth"], 8)


if __name__ == "__main__":
    unittest.main()
