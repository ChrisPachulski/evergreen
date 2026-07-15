import hashlib
import importlib
import json
from dataclasses import FrozenInstanceError
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

from eval.oracle.sources.java import generate as java_generate
from eval.oracle.sources.typescript import generate as typescript_generate


JAVA = """import java.util.Optional;

public final class Oracle {
    static int value() { return 1; }
    static String raises() {
        if (false) throw new IllegalStateException("expected");
        return "no-error";
    }
    static int fallback() { return (int) Optional.ofNullable(null).orElse(1); }
    static int cardinality() { int[] items = {1}; return items.length; }
    static boolean flip(boolean state) { state = !state; return state; }
}
"""

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
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode()


class JavaTypeScriptImportIsolationTests(unittest.TestCase):
    def test_language_configuration_is_immutable(self):
        with self.assertRaises(FrozenInstanceError):
            java_generate.JAVA_CONFIG.language = "typescript"
        with self.assertRaises(TypeError):
            typescript_generate.TOOLCHAIN["toolchain_id"] = "mutable"

    def test_import_order_does_not_change_language_configuration(self):
        modules = (
            "eval.oracle.sources.java.generate",
            "eval.oracle.sources.typescript.generate",
        )
        for order in (modules, tuple(reversed(modules))):
            with self.subTest(order=order):
                script = f"""
import importlib
for name in {order!r}:
    importlib.import_module(name)
java = importlib.import_module({modules[0]!r})
typescript = importlib.import_module({modules[1]!r})
assert java.LANGUAGE == "java"
assert typescript.LANGUAGE == "typescript"
assert java.TOOLCHAIN["toolchain_id"] == "temurin-21.0.7+6"
assert typescript.TOOLCHAIN["toolchain_id"] == "node-22.17.0-typescript-5.8.3"
"""
                completed = subprocess.run(
                    [sys.executable, "-c", script],
                    capture_output=True,
                    text=True,
                    check=False,
                )
                self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_importing_typescript_does_not_change_java_behavior(self):
        before = java_generate.discover_source_witnesses(JAVA.encode())
        importlib.reload(typescript_generate)
        self.assertEqual(java_generate.LANGUAGE, "java")
        self.assertEqual(java_generate.discover_source_witnesses(JAVA.encode()), before)


class SourceCatalogTestsMixin:
    code = None
    generate = None
    language = None
    source_path = None
    operators = None

    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.root = Path(self.temporary.name)
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        subprocess.run(
            ["git", "-C", str(self.root), "config", "user.email", "test@example.com"],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "config", "user.name", "Test"],
            check=True,
        )
        (self.root / "LICENSE").write_text("MIT License\n")
        (self.root / self.source_path).write_text(self.code)
        subprocess.run(
            ["git", "-C", str(self.root), "add", "LICENSE", self.source_path],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "commit", "-qm", "fixture"],
            check=True,
        )
        self.commit = self.git("rev-parse", "HEAD")
        self.tree = self.git("rev-parse", "HEAD^{tree}")
        witnesses = [
            {
                "kind": kind,
                "operator": operator,
                "offset": self.code.encode().index(fragment.encode()),
            }
            for kind, (operator, fragment) in self.operators.items()
        ]
        self.source = {
            "source_id": f"{self.language}-acme-oracle",
            "project": "acme/oracle",
            "lineage_id": "github.com-acme-oracle",
            "origin": "https://example.invalid/acme/oracle.git",
            "commit": self.commit,
            "tree": self.tree,
            "license": {
                "spdx": "MIT",
                "path": "LICENSE",
                "sha256": hashlib.sha256(b"MIT License\n").hexdigest(),
            },
            "source": {
                "path": self.source_path,
                "blob_oid": self.git("rev-parse", f"HEAD:{self.source_path}"),
                "sha256": hashlib.sha256(self.code.encode()).hexdigest(),
            },
            "witnesses": witnesses,
        }
        self.catalog = {
            "schema_version": 1,
            "kind": "evergreen-oracle-language-source-catalog",
            "language": self.language,
            "sources": [self.source],
        }

    def tearDown(self):
        self.temporary.cleanup()

    def git(self, *args):
        return subprocess.check_output(
            ["git", "-C", str(self.root), *args],
            text=True,
        ).strip()

    def test_exact_git_bytes_produce_five_byte_bound_candidates(self):
        self.generate.validate_catalog(self.catalog)
        verified = self.generate.verify_checkout(self.source, self.root)
        self.assertEqual(
            verified["oracle_kind_counts"],
            {
                "return-value": 1,
                "raises": 1,
                "default-value": 1,
                "cardinality": 1,
                "state-change": 1,
            },
        )
        self.assertEqual(verified["commit"], self.commit)
        self.assertEqual(verified["tree"], self.tree)
        self.assertEqual(
            verified["extracted_tree_sha256"],
            hashlib.sha256(
                canonical(
                    [
                        {
                            "repository_path": self.source_path,
                            "input_path": self.source_path,
                            "blob_oid": self.source["source"]["blob_oid"],
                            "sha256": self.source["source"]["sha256"],
                            "oracle_kind": kind,
                        }
                        for kind in (
                            "return-value",
                            "raises",
                            "default-value",
                            "cardinality",
                            "state-change",
                        )
                    ]
                )
            ).hexdigest(),
        )

    def test_changed_blob_identity_is_rejected(self):
        source = json.loads(json.dumps(self.source))
        source["source"]["sha256"] = "0" * 64
        with self.assertRaisesRegex(self.generate.CatalogError, "source blob"):
            self.generate.verify_checkout(source, self.root)

    def test_changed_git_blob_oid_is_rejected(self):
        source = json.loads(json.dumps(self.source))
        source["source"]["blob_oid"] = "0" * 40
        with self.assertRaisesRegex(
            self.generate.CatalogError, "source blob Git object"
        ):
            self.generate.verify_checkout(source, self.root)

    def test_git_replacement_object_cannot_redirect_pinned_commit(self):
        (self.root / self.source_path).write_text(self.code + "// replacement\n")
        subprocess.run(
            ["git", "-C", str(self.root), "add", self.source_path],
            check=True,
        )
        subprocess.run(
            ["git", "-C", str(self.root), "commit", "-qm", "replacement"],
            check=True,
        )
        replacement = self.git("rev-parse", "HEAD")
        subprocess.run(
            ["git", "-C", str(self.root), "replace", self.commit, replacement],
            check=True,
        )

        verified = self.generate.verify_checkout(self.source, self.root)

        self.assertEqual(verified["commit"], self.commit)
        self.assertEqual(verified["tree"], self.tree)

    def test_unexpected_catalog_field_is_rejected(self):
        catalog = json.loads(json.dumps(self.catalog))
        catalog["sources"][0]["seed_claims"] = 999
        with self.assertRaisesRegex(self.generate.CatalogError, "source fields"):
            self.generate.validate_catalog(catalog)

    def test_report_does_not_promote_candidates_to_executable_seeds(self):
        verified = self.generate.verify_checkout(self.source, self.root)
        report = self.generate.build_report(self.catalog, [verified])
        self.assertEqual(report["byte_bound_candidates"], 5)
        self.assertEqual(report["executable_seeds"], 0)
        self.assertEqual(report["project_shortfall"], 19)
        self.assertEqual(report["seed_shortfall"], 250)
        self.assertEqual(
            report["readiness_reasons"],
            [
                "digest-addressed-adapter-execution-receipt-missing",
                "projects-below-20",
                "executable-seeds-below-250",
            ],
        )
        self.assertFalse(report["ready"])
        self.assertTrue(
            {
                "execution_receipt",
                "harness",
                "toolchain_id",
                "toolchain_identity_sha256",
            }.isdisjoint(report["sources"][0])
        )

    def test_wrapper_rejects_a_changed_source_span(self):
        code = bytearray(self.code.encode())
        witness = self.source["witnesses"][0]
        code[witness["offset"]] ^= 1
        with self.assertRaisesRegex(
            self.generate.CatalogError, "exact pinned source span"
        ):
            self.generate.generate_wrapper(bytes(code), witness)


class JavaSourceCatalogTests(SourceCatalogTestsMixin, unittest.TestCase):
    code = JAVA
    generate = java_generate
    language = "java"
    source_path = "Oracle.java"
    operators = {
        "return-value": ("return-value-1-to-2-v1", "return 1"),
        "raises": ("raises-none-to-value-error-v1", "throw new IllegalStateException"),
        "default-value": ("default-value-one-to-two-v1", "orElse(1)"),
        "cardinality": ("cardinality-one-to-two-v1", "{1}"),
        "state-change": ("state-change-before-to-after-v1", "!state"),
    }

    def test_source_bound_wrappers_cover_every_operator_without_relabeling(self):
        code = self.code.encode()
        for witness in self.source["witnesses"]:
            wrapper = self.generate.generate_wrapper(code, witness)
            contract = self.generate.MUTATION_OPERATORS[witness["operator"]]
            length = wrapper["source_binding"]["length"]
            span = code[witness["offset"] : witness["offset"] + length]
            self.assertEqual(wrapper["oracle_kind"], contract["kind"])
            self.assertEqual(wrapper["source_binding"]["offset"], witness["offset"])
            self.assertEqual(
                wrapper["source_binding"]["span_sha256"],
                hashlib.sha256(span).hexdigest(),
            )
            self.assertEqual(wrapper, self.generate.generate_wrapper(code, witness))
            discovered = self.generate.discover_witnesses(wrapper["code"].encode())
            self.assertEqual(len(discovered), 1)
            self.assertEqual(discovered[0]["operator"], witness["operator"])

    def test_wrapper_discovery_ignores_comments_and_string_literals(self):
        decoys = b"""// return 1; throw new IllegalStateException; orElse(1); {1}; !state
class Decoys { String text = "return 1; throw new IllegalStateException; orElse(1); {1}; !state"; }
/* return 1; throw new IllegalStateException; orElse(1); {1}; !state */
"""
        self.assertEqual(self.generate.discover_source_witnesses(decoys), [])


class TypeScriptSourceCatalogTests(SourceCatalogTestsMixin, unittest.TestCase):
    code = TYPESCRIPT
    generate = typescript_generate
    language = "typescript"
    source_path = "oracle.ts"
    operators = {
        "return-value": ("return-value-1-to-2-v1", "return 1"),
        "raises": ("raises-none-to-value-error-v1", "throw new Error"),
        "default-value": ("default-value-one-to-two-v1", "item = 1"),
        "cardinality": ("cardinality-one-to-two-v1", "[1]"),
        "state-change": ("state-change-before-to-after-v1", "!state"),
    }

    def test_source_bound_wrappers_execute_all_five_operator_shapes(self):
        expected = {
            "return-value": ("1\n", "2\n"),
            "raises": ("no-error\n", "ValueError\n"),
            "default-value": ("default:1\n", "default:2\n"),
            "cardinality": ("cardinality:1\n", "cardinality:2\n"),
            "state-change": ("state:changed\n", "state:unchanged\n"),
        }
        for witness in self.source["witnesses"]:
            wrapper = self.generate.generate_wrapper(self.code.encode(), witness)
            contract = self.generate.MUTATION_OPERATORS[witness["operator"]]
            variant = contract["variants"]["typescript"]
            self.assertEqual(wrapper["oracle_kind"], contract["kind"])
            self.assertEqual(
                wrapper,
                self.generate.generate_wrapper(self.code.encode(), witness),
            )
            self.assertEqual(
                self.run_node(wrapper["code"]), expected[contract["kind"]][0]
            )
            mutated = (
                wrapper["code"]
                .encode()
                .replace(
                    variant["before"],
                    variant["after"],
                    1,
                )
                .decode()
            )
            self.assertEqual(self.run_node(mutated), expected[contract["kind"]][1])

    def test_wrapper_discovery_ignores_comments_strings_and_templates(self):
        decoys = b"""// return 1; throw new Error; item = 1; [1]; !state
const a = "return 1; throw new Error; item = 1; [1]; !state";
const b = `return 1; throw new Error; item = 1; [1]; !state`;
/* return 1; throw new Error; item = 1; [1]; !state */
"""
        self.assertEqual(self.generate.discover_source_witnesses(decoys), [])

    def run_node(self, code):
        completed = subprocess.run(
            ["node", "--input-type=commonjs"],
            input=code,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        return completed.stdout


if __name__ == "__main__":
    unittest.main()
