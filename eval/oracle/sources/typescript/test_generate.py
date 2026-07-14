import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import generate


TYPESCRIPT = """function value() { return 1; }
function raises() {
    if (false) { throw new Error("expected"); }
    return "no-error";
}
namespace Defaults { export function value(item = 1) { return item; } }
function cardinality() { const items = [1]; return items.length; }
function flip(state: boolean) { state = !state; return state; }
"""


def canonical(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False,
                      sort_keys=True, separators=(",", ":")).encode()


class CatalogTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.email", "test@example.com"], check=True)
        subprocess.run(["git", "-C", str(self.root), "config", "user.name", "Test"], check=True)
        (self.root / "LICENSE").write_text("MIT License\n")
        (self.root / "oracle.ts").write_text(TYPESCRIPT)
        subprocess.run(["git", "-C", str(self.root), "add", "LICENSE", "oracle.ts"], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "fixture"], check=True)
        self.commit = self.git("rev-parse", "HEAD")
        self.tree = self.git("rev-parse", "HEAD^{tree}")
        operators = {
            "return-value": ("return-value-1-to-2-v1", "return 1"),
            "raises": ("raises-none-to-value-error-v1", "throw new Error"),
            "default-value": ("default-value-one-to-two-v1", "item = 1"),
            "cardinality": ("cardinality-one-to-two-v1", "[1]"),
            "state-change": ("state-change-before-to-after-v1", "!state"),
        }
        witnesses = [
            {"kind": kind, "operator": operator,
             "offset": TYPESCRIPT.encode().index(fragment.encode())}
            for kind, (operator, fragment) in operators.items()
        ]
        self.source = {
            "source_id": "typescript-acme-oracle",
            "project": "acme/oracle",
            "lineage_id": "github.com-acme-oracle",
            "origin": "https://example.invalid/acme/oracle.git",
            "commit": self.commit,
            "tree": self.tree,
            "license": {
                "spdx": "MIT", "path": "LICENSE",
                "sha256": hashlib.sha256(b"MIT License\n").hexdigest(),
            },
            "source": {
                "path": "oracle.ts",
                "blob_oid": self.git("rev-parse", "HEAD:oracle.ts"),
                "sha256": hashlib.sha256(TYPESCRIPT.encode()).hexdigest(),
            },
            "witnesses": witnesses,
        }
        self.catalog = {
            "schema_version": 1,
            "kind": "evergreen-oracle-language-source-catalog",
            "language": "typescript",
            "sources": [self.source],
        }

    def tearDown(self):
        self.temporary.cleanup()

    def git(self, *args):
        return subprocess.check_output(
            ["git", "-C", str(self.root), *args], text=True,
        ).strip()

    def test_exact_git_bytes_produce_five_byte_bound_candidates(self):
        generate.validate_catalog(self.catalog)
        verified = generate.verify_checkout(self.source, self.root)
        self.assertEqual(
            verified["oracle_kind_counts"],
            {"return-value": 1, "raises": 1, "default-value": 1,
             "cardinality": 1, "state-change": 1},
        )
        self.assertEqual(
            verified["extracted_tree_sha256"],
            hashlib.sha256(canonical([
                {
                    "repository_path": "oracle.ts", "input_path": "oracle.ts",
                    "blob_oid": self.source["source"]["blob_oid"],
                    "sha256": self.source["source"]["sha256"], "oracle_kind": kind,
                }
                for kind in ("return-value", "raises", "default-value", "cardinality", "state-change")
            ])).hexdigest(),
        )

    def test_changed_blob_identity_is_rejected(self):
        source = json.loads(json.dumps(self.source))
        source["source"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(generate.CatalogError, "source blob"):
            generate.verify_checkout(source, self.root)

    def test_changed_git_blob_oid_is_rejected(self):
        source = json.loads(json.dumps(self.source))
        source["source"]["blob_oid"] = "0" * 40
        with self.assertRaisesRegex(generate.CatalogError, "source blob Git object"):
            generate.verify_checkout(source, self.root)

    def test_unexpected_catalog_field_is_rejected(self):
        catalog = json.loads(json.dumps(self.catalog))
        catalog["sources"][0]["seed_claims"] = 999
        with self.assertRaisesRegex(generate.CatalogError, "source fields"):
            generate.validate_catalog(catalog)

    def test_report_does_not_promote_candidates_to_executable_seeds(self):
        verified = generate.verify_checkout(self.source, self.root)
        report = generate.build_report(self.catalog, [verified])
        self.assertEqual(report["byte_bound_candidates"], 5)
        self.assertEqual(report["executable_seeds"], 0)
        self.assertEqual(report["project_shortfall"], 19)
        self.assertEqual(report["seed_shortfall"], 250)
        self.assertEqual(report["sources"][0]["toolchain_id"], "node-22.17.0-typescript-5.8.3")
        self.assertEqual(report["sources"][0]["harness"]["argv"], [
            "/opt/evergreen/bin/typescript-oracle-v1",
            "/input/oracle.ts",
            "/control/oracle-v1.json",
        ])

    def test_source_bound_wrappers_execute_all_five_operator_shapes(self):
        code = TYPESCRIPT.encode()
        expected = {
            "return-value": ("1\n", "2\n"),
            "raises": ("no-error\n", "ValueError\n"),
            "default-value": ("default:1\n", "default:2\n"),
            "cardinality": ("cardinality:1\n", "cardinality:2\n"),
            "state-change": ("state:changed\n", "state:unchanged\n"),
        }
        for witness in self.source["witnesses"]:
            wrapper = generate.generate_wrapper(code, witness)
            contract = generate.MUTATION_OPERATORS[witness["operator"]]
            variant = contract["variants"]["typescript"]
            self.assertEqual(wrapper["oracle_kind"], contract["kind"])
            self.assertEqual(wrapper, generate.generate_wrapper(code, witness))
            self.assertEqual(self.run_node(wrapper["code"]), expected[contract["kind"]][0])
            mutated = wrapper["code"].encode().replace(variant["before"], variant["after"], 1).decode()
            self.assertEqual(self.run_node(mutated), expected[contract["kind"]][1])

    def test_wrapper_rejects_a_changed_source_span(self):
        code = bytearray(TYPESCRIPT.encode())
        witness = self.source["witnesses"][0]
        code[witness["offset"]] ^= 1
        with self.assertRaisesRegex(generate.CatalogError, "exact pinned source span"):
            generate.generate_wrapper(bytes(code), witness)

    def test_wrapper_discovery_ignores_comments_strings_and_templates(self):
        decoys = b'''// return 1; throw new Error; item = 1; [1]; !state
const a = "return 1; throw new Error; item = 1; [1]; !state";
const b = `return 1; throw new Error; item = 1; [1]; !state`;
/* return 1; throw new Error; item = 1; [1]; !state */
'''
        self.assertEqual(generate.discover_source_witnesses(decoys), [])

    def run_node(self, code):
        completed = subprocess.run(
            ["node", "--input-type=commonjs"], input=code, text=True,
            capture_output=True, timeout=10, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed.stdout


if __name__ == "__main__":
    unittest.main()
