import hashlib
import json
from pathlib import Path
import subprocess
import tempfile
import unittest

import generate


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
        (self.root / "Oracle.java").write_text(JAVA)
        subprocess.run(["git", "-C", str(self.root), "add", "LICENSE", "Oracle.java"], check=True)
        subprocess.run(["git", "-C", str(self.root), "commit", "-qm", "fixture"], check=True)
        self.commit = self.git("rev-parse", "HEAD")
        self.tree = self.git("rev-parse", "HEAD^{tree}")
        operators = {
            "return-value": ("return-value-1-to-2-v1", "return 1"),
            "raises": ("raises-none-to-value-error-v1", "throw new IllegalStateException"),
            "default-value": ("default-value-one-to-two-v1", "orElse(1)"),
            "cardinality": ("cardinality-one-to-two-v1", "{1}"),
            "state-change": ("state-change-before-to-after-v1", "!state"),
        }
        witnesses = [
            {"kind": kind, "operator": operator,
             "offset": JAVA.encode().index(fragment.encode())}
            for kind, (operator, fragment) in operators.items()
        ]
        self.source = {
            "source_id": "java-acme-oracle",
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
                "path": "Oracle.java",
                "blob_oid": self.git("rev-parse", "HEAD:Oracle.java"),
                "sha256": hashlib.sha256(JAVA.encode()).hexdigest(),
            },
            "witnesses": witnesses,
        }
        self.catalog = {
            "schema_version": 1,
            "kind": "evergreen-oracle-language-source-catalog",
            "language": "java",
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
        self.assertEqual(verified["commit"], self.commit)
        self.assertEqual(verified["tree"], self.tree)
        self.assertEqual(
            verified["extracted_tree_sha256"],
            hashlib.sha256(canonical([
                {
                    "repository_path": "Oracle.java", "input_path": "Oracle.java",
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
        self.assertEqual(report["readiness_reasons"], [
            "digest-addressed-adapter-execution-receipt-missing",
            "projects-below-20",
            "executable-seeds-below-250",
        ])
        self.assertEqual(report["sources"][0]["toolchain_id"], "temurin-21.0.7+6")
        self.assertEqual(report["sources"][0]["harness"]["argv"], [
            "/opt/evergreen/bin/java-oracle-v1",
            "/input/Oracle.java",
            "/control/oracle-v1.json",
        ])

    def test_source_bound_wrappers_cover_every_operator_without_relabeling(self):
        code = JAVA.encode()
        for witness in self.source["witnesses"]:
            wrapper = generate.generate_wrapper(code, witness)
            contract = generate.MUTATION_OPERATORS[witness["operator"]]
            length = wrapper["source_binding"]["length"]
            span = code[witness["offset"]:witness["offset"] + length]
            self.assertEqual(wrapper["oracle_kind"], contract["kind"])
            self.assertEqual(wrapper["source_binding"]["offset"], witness["offset"])
            self.assertEqual(wrapper["source_binding"]["span_sha256"], hashlib.sha256(span).hexdigest())
            self.assertEqual(wrapper, generate.generate_wrapper(code, witness))
            discovered = generate.discover_witnesses(wrapper["code"].encode())
            self.assertEqual(len(discovered), 1)
            self.assertEqual(discovered[0]["operator"], witness["operator"])

    def test_wrapper_rejects_a_changed_source_span(self):
        code = bytearray(JAVA.encode())
        witness = self.source["witnesses"][0]
        code[witness["offset"]] ^= 1
        with self.assertRaisesRegex(generate.CatalogError, "exact pinned source span"):
            generate.generate_wrapper(bytes(code), witness)

    def test_wrapper_discovery_ignores_comments_and_string_literals(self):
        decoys = b'''// return 1; throw new IllegalStateException; orElse(1); {1}; !state
class Decoys { String text = "return 1; throw new IllegalStateException; orElse(1); {1}; !state"; }
/* return 1; throw new IllegalStateException; orElse(1); {1}; !state */
'''
        self.assertEqual(generate.discover_source_witnesses(decoys), [])


if __name__ == "__main__":
    unittest.main()
