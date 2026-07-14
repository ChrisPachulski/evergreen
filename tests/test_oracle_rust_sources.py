import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

from eval.oracle.sources.rust import derive, generate


class RustSourceInventoryTests(unittest.TestCase):
    def git(self, repository, *arguments, input_bytes=None):
        return subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=True,
            input=input_bytes,
            stdout=subprocess.PIPE,
        ).stdout

    def fixture(self, root):
        repository = root / "fixture"
        repository.mkdir()
        self.git(repository, "init", "-q")
        self.git(repository, "config", "user.email", "oracle@example.invalid")
        self.git(repository, "config", "user.name", "Oracle Fixture")
        self.git(repository, "remote", "add", "origin", "https://github.com/example/fixture.git")
        (repository / "LICENSE-MIT").write_text("MIT fixture license\n")
        (repository / "src").mkdir()
        (repository / "src" / "lib.rs").write_text("pub fn answer() -> i32 { 42 }\n")
        self.git(repository, "add", "LICENSE-MIT", "src/lib.rs")
        self.git(repository, "commit", "-qm", "fixture")
        commit = self.git(repository, "rev-parse", "HEAD").decode().strip()
        tree = self.git(repository, "rev-parse", "HEAD^{tree}").decode().strip()
        license_bytes = self.git(repository, "show", f"{commit}:LICENSE-MIT")
        source_bytes = self.git(repository, "show", f"{commit}:src/lib.rs")
        blob = self.git(repository, "rev-parse", f"{commit}:src/lib.rs").decode().strip()
        record = {
            "source_id": "rust-example-fixture",
            "project": "example/fixture",
            "lineage_id": "github-example-fixture",
            "origin": "https://github.com/example/fixture.git",
            "commit": commit,
            "tree": tree,
            "license": {
                "spdx": "MIT",
                "path": "LICENSE-MIT",
                "sha256": hashlib.sha256(license_bytes).hexdigest(),
            },
            "source": {
                "path": "src/lib.rs",
                "blob_oid": blob,
                "sha256": hashlib.sha256(source_bytes).hexdigest(),
                "bytes": len(source_bytes),
            },
        }
        record["extracted_tree_sha256"] = generate.extracted_tree_sha256(
            record["source"]
        )
        return repository, record

    def test_verifies_exact_git_objects_and_emits_canonical_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            repository, record = self.fixture(Path(directory))
            inventory = generate.verify_sources(
                [record], lambda _source_id: repository, minimum_sources=1
            )

            self.assertEqual(inventory[0]["commit"], record["commit"])
            self.assertEqual(
                inventory[0]["extraction"]["argv"],
                ["git", "show", "--no-ext-diff", f"{record['commit']}:src/lib.rs"],
            )
            self.assertNotIn("harness", inventory[0])
            first = generate.canonical(inventory)
            second = generate.canonical(
                generate.verify_sources(
                    [record], lambda _source_id: repository, minimum_sources=1
                )
            )
            self.assertEqual(first, second)

    def test_rejects_unbound_source_bytes_tree_origin_and_license(self):
        with tempfile.TemporaryDirectory() as directory:
            repository, record = self.fixture(Path(directory))
            mutations = (
                ("source sha", lambda item: item["source"].update({"sha256": "0" * 64})),
                ("source blob", lambda item: item["source"].update({"blob_oid": "0" * 40})),
                ("tree", lambda item: item.update({"tree": "0" * 40})),
                ("origin", lambda item: item.update({"origin": "https://github.com/other/repo.git"})),
                ("license", lambda item: item["license"].update({"sha256": "0" * 64})),
            )
            for label, mutate in mutations:
                with self.subTest(label=label):
                    changed = json.loads(json.dumps(record))
                    mutate(changed)
                    if label in ("source sha", "source blob"):
                        changed["extracted_tree_sha256"] = generate.extracted_tree_sha256(
                            changed["source"]
                        )
                    with self.assertRaisesRegex(ValueError, "does not match pinned Git objects"):
                        generate.verify_sources(
                            [changed], lambda _source_id: repository, minimum_sources=1
                        )

    def test_rejects_non_regular_source_blob(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            repository, _record = self.fixture(root)
            (repository / "src" / "lib.rs").unlink()
            (repository / "src" / "lib.rs").symlink_to("../LICENSE-MIT")
            self.git(repository, "add", "src/lib.rs")
            self.git(repository, "commit", "-qm", "replace source with symlink")
            commit = self.git(repository, "rev-parse", "HEAD").decode().strip()
            source_bytes = self.git(repository, "show", f"{commit}:src/lib.rs")
            record = {
                "source_id": "rust-example-fixture",
                "project": "example/fixture",
                "lineage_id": "github-example-fixture",
                "origin": "https://github.com/example/fixture.git",
                "commit": commit,
                "tree": self.git(repository, "rev-parse", "HEAD^{tree}").decode().strip(),
                "license": {
                    "spdx": "MIT",
                    "path": "LICENSE-MIT",
                    "sha256": hashlib.sha256(
                        self.git(repository, "show", f"{commit}:LICENSE-MIT")
                    ).hexdigest(),
                },
                "source": {
                    "path": "src/lib.rs",
                    "blob_oid": self.git(
                        repository, "rev-parse", f"{commit}:src/lib.rs"
                    ).decode().strip(),
                    "sha256": hashlib.sha256(source_bytes).hexdigest(),
                    "bytes": len(source_bytes),
                },
            }
            record["extracted_tree_sha256"] = generate.extracted_tree_sha256(
                record["source"]
            )

            with self.assertRaisesRegex(ValueError, "does not match pinned Git objects"):
                generate.verify_sources(
                    [record], lambda _source_id: repository, minimum_sources=1
                )

    def test_git_replace_cannot_redirect_pinned_commit(self):
        with tempfile.TemporaryDirectory() as directory:
            repository, record = self.fixture(Path(directory))
            original_commit = record["commit"]
            (repository / "src" / "lib.rs").write_text("pub fn answer() -> i32 { 7 }\n")
            self.git(repository, "add", "src/lib.rs")
            self.git(repository, "commit", "-qm", "replacement")
            replacement_commit = self.git(repository, "rev-parse", "HEAD").decode().strip()
            self.git(repository, "replace", original_commit, replacement_commit)

            self.assertNotEqual(
                self.git(repository, "rev-parse", f"{original_commit}^{{tree}}").decode().strip(),
                record["tree"],
            )
            inventory = generate.verify_sources(
                [record], lambda _source_id: repository, minimum_sources=1
            )
            self.assertEqual(inventory[0]["tree"], record["tree"])

    def test_rejects_duplicate_projects_lineages_content_and_too_few_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            repository, record = self.fixture(Path(directory))
            with self.assertRaisesRegex(ValueError, "at least 20 distinct sources"):
                generate.verify_sources([record], lambda _source_id: repository)
            for field in ("project", "lineage_id", "extracted_tree_sha256"):
                second = json.loads(json.dumps(record))
                second["source_id"] = "rust-example-second"
                second["project"] = "example/second"
                second["lineage_id"] = "github-example-second"
                second[field] = record[field]
                with self.subTest(field=field):
                    with self.assertRaisesRegex(ValueError, "duplicate source identity"):
                        generate.verify_sources(
                            [record, second],
                            lambda _source_id: repository,
                            minimum_sources=1,
                        )

    def test_checked_in_catalog_declares_twenty_distinct_real_candidates(self):
        catalog = generate.load_catalog(
            Path(generate.__file__).with_name("catalog.json")
        )
        sources = catalog["sources"]

        self.assertEqual(len(sources), 20)
        self.assertEqual(len({item["source_id"] for item in sources}), 20)
        self.assertEqual(len({item["project"] for item in sources}), 20)
        self.assertEqual(len({item["lineage_id"] for item in sources}), 20)
        self.assertFalse(
            {"seed_claims", "oracle_kind_counts", "sandbox_image"}
            & set().union(*(item.keys() for item in sources))
        )

    def test_candidate_inventory_cannot_be_promoted_to_ready_provenance(self):
        with self.assertRaisesRegex(ValueError, "adapter receipt"):
            generate.provenance_record({})


class RustDerivationReceiptTests(unittest.TestCase):
    def spec(self, source):
        span = b"const INITIALIZING: usize = 1;"
        start = source.index(span)
        return {
            "schema_version": 1,
            "kind": "evergreen-rust-derivation-spec",
            "generator_id": "rust-const-usize-return-v1",
            "source_id": "rust-example-fixture",
            "repository_path": "src/lib.rs",
            "input_path": "derived/initializing.rs",
            "blob_oid": "a" * 40,
            "blob_sha256": hashlib.sha256(source).hexdigest(),
            "span": {
                "start": start,
                "end": start + len(span),
                "sha256": hashlib.sha256(span).hexdigest(),
            },
            "symbol": "INITIALIZING",
            "oracle_kind": "return-value",
            "documentation_template": "The INITIALIZING state constant has value 1.",
        }

    def test_derives_recomputable_wrapper_and_closed_receipt_from_exact_span(self):
        source = b"const UNINITIALIZED: usize = 0;\nconst INITIALIZING: usize = 1;\n"
        spec = self.spec(source)

        result = derive.derive(spec, source)

        self.assertEqual(
            result["private_material"]["code"],
            "const INITIALIZING: usize = 1;\n"
            "fn value() -> i32 { return INITIALIZING as i32; }\n"
            "fn main() { println!(\"{}\", value()); }\n",
        )
        self.assertEqual(result["receipt"]["oracle_kind"], "return-value")
        self.assertEqual(result["receipt"]["observed_value"], 1)
        self.assertEqual(
            result["receipt"]["receipt_schema_sha256"],
            hashlib.sha256(
                Path(derive.__file__).with_name("derivation-receipt-schema-v1.json").read_bytes()
            ).hexdigest(),
        )
        self.assertEqual(
            result["receipt"]["wrapper_sha256"],
            hashlib.sha256(result["private_material"]["code"].encode()).hexdigest(),
        )
        self.assertNotIn("code", result["receipt"])
        self.assertNotIn("documentation_template", result["receipt"])
        self.assertEqual(derive.canonical(result), derive.canonical(derive.derive(spec, source)))

    def test_rejects_changed_blob_span_symbol_kind_and_free_form_value(self):
        source = b"const UNINITIALIZED: usize = 0;\nconst INITIALIZING: usize = 1;\n"
        cases = (
            ("blob", source.replace(b"UNINITIALIZED", b"NOT_INITIALIZED"), self.spec(source)),
            ("span", source, {**self.spec(source), "span": {"start": 0, "end": 1,
                "sha256": hashlib.sha256(source[:1]).hexdigest()}}),
            ("symbol", source, {**self.spec(source), "symbol": "ACTIVE"}),
            ("kind", source, {**self.spec(source), "oracle_kind": "cardinality"}),
            ("value", source.replace(b"= 1;", b"= 2;"), self.spec(source)),
        )
        for label, candidate, spec in cases:
            with self.subTest(label=label):
                with self.assertRaises(ValueError):
                    derive.derive(spec, candidate)

    def test_real_prototype_binds_the_pinned_log_blob_and_span(self):
        catalog = generate.load_catalog(Path(generate.__file__).with_name("catalog.json"))
        log_source = next(
            item for item in catalog["sources"] if item["source_id"] == "rust-rust-lang-log"
        )
        spec = derive.load_spec(Path(derive.__file__).with_name("prototype-return-value.json"))

        self.assertEqual(spec["blob_oid"], log_source["source"]["blob_oid"])
        self.assertEqual(spec["blob_sha256"], log_source["source"]["sha256"])
        self.assertEqual(spec["repository_path"], log_source["source"]["path"])

    def test_receipt_schema_is_closed_and_matches_emitted_fields(self):
        source = b"const INITIALIZING: usize = 1;"
        receipt = derive.derive(self.spec(source), source)["receipt"]
        schema = json.loads(
            Path(derive.__file__).with_name("derivation-receipt-schema-v1.json").read_text()
        )

        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["properties"]), set(receipt))
        self.assertEqual(set(schema["required"]), set(receipt))


if __name__ == "__main__":
    unittest.main()
