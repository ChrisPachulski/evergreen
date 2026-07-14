import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
LANGUAGES = {
    "python": {
        "extension": ".py",
        "source": (
            "def value(): return 1\n"
            "if False: raise ValueError\n"
            "def default(item=1): return item\n"
            "items = [1]\n"
            "state = False\nstate = not state\n"
        ),
    },
    "go": {
        "extension": ".go",
        "source": (
            "package main\n"
            "func value() int { return 1 }\n"
            "func noError() { if false { panic(\"bad\") } }\n"
            "func defaultValue(v int) int { return v }\n"
            "func useDefault() int { return defaultValue(1) }\n"
            "func count() int { items := []int{1}; return len(items) }\n"
            "func change() bool { state := false; state = !state; return state }\n"
        ),
    },
}


def load_generator(language):
    script = ROOT / "eval" / "oracle" / "sources" / language / "generate.py"
    spec = importlib.util.spec_from_file_location(f"{language}_source_generator", script)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def git(root, *arguments):
    return subprocess.run(
        ["git", "-C", str(root), *arguments],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


class PythonGoSourceCatalogTests(unittest.TestCase):
    def fixture(self, directory, language):
        details = LANGUAGES[language]
        repository = Path(directory) / f"{language}-upstream"
        repository.mkdir()
        git(repository, "init", "-q")
        git(repository, "config", "user.name", "Evergreen Test")
        git(repository, "config", "user.email", "evergreen@example.invalid")
        origin = f"https://example.invalid/acme/{language}-demo.git"
        git(repository, "remote", "add", "origin", origin)
        (repository / "LICENSE").write_text(
            "Permission is hereby granted, free of charge, to any person obtaining a copy\n"
        )
        source_path = f"oracle{details['extension']}"
        (repository / source_path).write_text(details["source"])
        (repository / f".hidden{details['extension']}").write_text("ignored\n")
        git(repository, "add", "LICENSE", source_path, f".hidden{details['extension']}")
        git(repository, "commit", "-qm", "fixture")
        project = {
            "source_id": f"{language}-acme-demo",
            "project": f"acme/{language}-demo",
            "lineage_id": f"github.com-acme-{language}-demo",
            "origin": origin,
            "commit": git(repository, "rev-parse", "HEAD"),
            "tree": git(repository, "rev-parse", "HEAD^{tree}"),
            "license": {"spdx": "MIT", "path": "LICENSE"},
        }
        return repository, source_path, project

    def test_freeze_binds_all_source_bytes_without_unverified_semantic_claims(self):
        for language in LANGUAGES:
            with self.subTest(language=language), tempfile.TemporaryDirectory() as directory:
                generator = load_generator(language)
                repository, source_path, project = self.fixture(directory, language)
                record = generator.freeze_project(project, repository)
                source = (repository / source_path).read_bytes()

                self.assertEqual(record["source_file_count"], 2)
                source_record = next(
                    item for item in record["source_blobs"] if item["path"] == source_path
                )
                self.assertEqual(
                    source_record["sha256"],
                    hashlib.sha256(source).hexdigest(),
                )
                self.assertIn(f".hidden{LANGUAGES[language]['extension']}", {
                    item["path"] for item in record["source_blobs"]
                })
                self.assertEqual(set(record), generator.RECORD_KEYS)
                self.assertNotIn("operator_witness_counts", record)
                self.assertNotIn("harness", record)
                self.assertNotIn("wrapper_recipe", record)
                self.assertFalse(hasattr(generator, "derive_wrapper"))
                self.assertTrue(generator.verify_project(record, repository))

    def test_verify_recomputes_records_and_catalog_aggregates(self):
        for language in LANGUAGES:
            with self.subTest(language=language), tempfile.TemporaryDirectory() as directory:
                generator = load_generator(language)
                repository, _source_path, project = self.fixture(directory, language)
                record = generator.freeze_project(project, repository)
                catalog = generator.catalog([record])
                encoded = generator.canonical(catalog)

                self.assertEqual(encoded, generator.canonical(json.loads(encoded)))
                self.assertNotIn(b"seed_claims", encoded)
                self.assertNotIn(b"label", encoded)
                self.assertTrue(generator.validate_catalog(json.loads(encoded)))

                record["source_inventory_sha256"] = "0" * 64
                with self.assertRaisesRegex(ValueError, "record does not match upstream bytes"):
                    generator.verify_project(record, repository)

    def test_catalog_freeze_requires_exact_named_cached_checkout(self):
        for language in LANGUAGES:
            with self.subTest(language=language), tempfile.TemporaryDirectory() as directory:
                generator = load_generator(language)
                repository, _source_path, project = self.fixture(directory, language)
                cache = Path(directory) / "cache"
                checkout = cache / project["source_id"]
                cache.mkdir()
                shutil.copytree(repository, checkout)
                pins = {
                    "schema_version": 1,
                    "kind": "evergreen-upstream-project-pins",
                    "language": language,
                    "projects": [project],
                }

                self.assertEqual(generator.freeze_catalog(pins, cache)["verified_projects"], 1)
                pins["projects"] = [{**project, "source_id": "missing"}]
                with self.assertRaisesRegex(ValueError, "cached checkout is missing"):
                    generator.freeze_catalog(pins, cache)

    def test_source_bytes_cannot_be_emitted_as_provenance_without_adapter_receipt(self):
        for language in LANGUAGES:
            with self.subTest(language=language), tempfile.TemporaryDirectory() as directory:
                generator = load_generator(language)
                repository, _source_path, project = self.fixture(directory, language)
                record = generator.freeze_project(project, repository)

                with self.assertRaisesRegex(ValueError, "runnable adapter receipt"):
                    generator.provenance_record(record)

    def test_git_replacement_objects_cannot_spoof_pinned_source_bytes(self):
        for language in LANGUAGES:
            with self.subTest(language=language), tempfile.TemporaryDirectory() as directory:
                generator = load_generator(language)
                repository, source_path, project = self.fixture(directory, language)
                record = generator.freeze_project(project, repository)
                original = project["commit"]
                (repository / source_path).write_text(LANGUAGES[language]["source"] + "\n")
                git(repository, "add", source_path)
                git(repository, "commit", "-qm", "replacement")
                replacement = git(repository, "rev-parse", "HEAD")
                git(repository, "checkout", "-q", original)
                git(repository, "replace", original, replacement)

                self.assertTrue(generator.verify_project(record, repository))

    def test_checked_inputs_are_pins_not_stale_generated_catalogs(self):
        for language in LANGUAGES:
            with self.subTest(language=language):
                source_directory = ROOT / "eval" / "oracle" / "sources" / language
                pins = json.loads((source_directory / "projects.json").read_bytes())
                projects = pins["projects"]

                self.assertFalse((source_directory / "catalog.json").exists())
                self.assertEqual(len(projects), 20)
                self.assertEqual(len({item["source_id"] for item in projects}), 20)
                self.assertEqual(len({item["project"] for item in projects}), 20)
                self.assertEqual(len({item["lineage_id"] for item in projects}), 20)


if __name__ == "__main__":
    unittest.main()
