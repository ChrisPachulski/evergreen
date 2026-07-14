import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


SCRIPT = Path(__file__).with_name("generate.py")


def load_generator():
    spec = importlib.util.spec_from_file_location("python_source_generator", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(root, *args):
    return subprocess.run(
        ["git", "-C", str(root), *args], check=True, capture_output=True, text=True,
    ).stdout.strip()


class SourceCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name) / "upstream"
        self.root.mkdir()
        git(self.root, "init", "-q")
        git(self.root, "config", "user.name", "Evergreen Test")
        git(self.root, "config", "user.email", "evergreen@example.invalid")
        git(self.root, "remote", "add", "origin", "https://example.invalid/acme/python-demo.git")
        (self.root / "LICENSE").write_text(
            "Permission is hereby granted, free of charge, to any person obtaining a copy\n"
        )
        (self.root / "oracle.py").write_text(
            "def value(): return 1\n"
            "if False: raise ValueError\n"
            "def default(item=1): return item\n"
            "items = [1]\n"
            "state = False\nstate = not state\n"
        )
        (self.root / ".hidden.py").write_text("raise RuntimeError('not extractable')\n")
        git(self.root, "add", "LICENSE", "oracle.py", ".hidden.py")
        git(self.root, "commit", "-qm", "fixture")

    def tearDown(self):
        self.temporary.cleanup()

    def project(self):
        return {
            "source_id": "python-acme-demo",
            "project": "acme/python-demo",
            "lineage_id": "github.com-acme-python-demo",
            "origin": "https://example.invalid/acme/python-demo.git",
            "commit": git(self.root, "rev-parse", "HEAD"),
            "tree": git(self.root, "rev-parse", "HEAD^{tree}"),
            "license": {"spdx": "MIT", "path": "LICENSE"},
        }

    def test_freeze_binds_every_source_byte_and_all_operator_witnesses(self):
        generator = load_generator()
        record = generator.freeze_project(self.project(), self.root)

        source = (self.root / "oracle.py").read_bytes()
        self.assertEqual(record["source_file_count"], 1)
        self.assertEqual(record["source_blobs"], [record["representative_source"]])
        self.assertEqual(record["representative_source"]["path"], "oracle.py")
        self.assertEqual(
            record["representative_source"]["sha256"], hashlib.sha256(source).hexdigest(),
        )
        self.assertEqual(
            record["operator_witness_counts"],
            {"return-value": 1, "raises": 1, "default-value": 1,
             "cardinality": 1, "state-change": 1},
        )
        self.assertEqual(set(record), generator.RECORD_KEYS)
        self.assertTrue(generator.verify_project(record, self.root))

    def test_verify_recomputes_instead_of_trusting_catalog_hashes(self):
        generator = load_generator()
        record = generator.freeze_project(self.project(), self.root)
        record["source_inventory_sha256"] = "0" * 64

        with self.assertRaisesRegex(ValueError, "record does not match upstream bytes"):
            generator.verify_project(record, self.root)

    def test_catalog_is_canonical_closed_and_contains_no_seed_claims(self):
        generator = load_generator()
        record = generator.freeze_project(self.project(), self.root)
        catalog = generator.catalog([record])
        encoded = generator.canonical(catalog)

        self.assertEqual(encoded, generator.canonical(json.loads(encoded)))
        self.assertNotIn(b"seed_claims", encoded)
        self.assertNotIn(b"label", encoded)
        self.assertTrue(generator.validate_catalog(json.loads(encoded)))

    def test_provenance_emission_refuses_pattern_witnesses_without_adapter_receipt(self):
        generator = load_generator()
        record = generator.freeze_project(self.project(), self.root)

        with self.assertRaisesRegex(ValueError, "runnable adapter receipt"):
            generator.provenance_record(record)

    def test_freeze_catalog_uses_only_exact_named_cached_checkouts(self):
        generator = load_generator()
        cache = Path(self.temporary.name) / "cache"
        checkout = cache / self.project()["source_id"]
        cache.mkdir()
        shutil.copytree(self.root, checkout)

        frozen = generator.freeze_catalog({
            "schema_version": 1,
            "kind": "evergreen-upstream-project-pins",
            "language": "python",
            "projects": [self.project()],
        }, cache)

        self.assertEqual(frozen["verified_projects"], 1)
        with self.assertRaisesRegex(ValueError, "cached checkout is missing"):
            generator.freeze_catalog({
                "schema_version": 1,
                "kind": "evergreen-upstream-project-pins",
                "language": "python",
                "projects": [{**self.project(), "source_id": "missing"}],
            }, cache)

    def test_source_bound_wrappers_are_deterministic_executable_and_exact_blob_members(self):
        generator = load_generator()
        record = generator.freeze_project(self.project(), self.root)
        source = (self.root / "oracle.py").read_bytes()
        expected = {
            "return-value": "1\n", "raises": "no-error\n", "default-value": "default:1\n",
            "cardinality": "cardinality:1\n", "state-change": "state:changed\n",
        }
        for kind, stdout in expected.items():
            receipt = generator.derive_wrapper(record, "oracle.py", source, kind)
            wrapper = Path(self.temporary.name) / receipt["wrapper"]["path"]
            wrapper.write_text(receipt["wrapper"]["code"])
            completed = subprocess.run(
                ["python3", "-I", str(wrapper)], check=True, capture_output=True, text=True,
            )
            self.assertEqual(completed.stdout, stdout)
            self.assertEqual(receipt, generator.derive_wrapper(record, "oracle.py", source, kind))
            self.assertEqual(receipt["upstream_span"]["sha256"], hashlib.sha256(source).hexdigest())
            self.assertEqual(receipt["generator"], record["wrapper_recipe"])

        with self.assertRaisesRegex(ValueError, "exact catalog member"):
            generator.derive_wrapper(record, "oracle.py", source + b" ", "return-value")
        record["wrapper_recipe"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(ValueError, "wrapper recipe"):
            generator.derive_wrapper(record, "oracle.py", source, "return-value")


if __name__ == "__main__":
    unittest.main()
