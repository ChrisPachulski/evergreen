import hashlib
import hmac
import json
import subprocess
import sys
import tempfile
from pathlib import Path
import unittest
from unittest import mock

from eval.bench import make_probe
from eval.bench.make_probe import (
    CONTROL_LABEL, POSITIVE_LABEL, build_probe, build_probe_bytes, receipt_bytes,
    verify_probe_receipt,
)


def row(pair_id, label, language="Python", **extra):
    payload = {
        "id": pair_id, "label": label, "language": language, "category": None,
        "code": "return 1", "doc": "returns one", "func": "f", "source": "unit-test",
        "source_status": "validated",
    }
    payload.update(extra)
    return payload


def jsonl_bytes(rows):
    return b"".join(json.dumps(item, sort_keys=True).encode() + b"\n" for item in rows)


def dataset(n_positive, n_control, language="Python"):
    rows = (
        [row(f"org/repo/f{i}#pos", "inconsistent", language) for i in range(n_positive)] +
        [row(f"org/repo/f{i}#ctl", "consistent", language) for i in range(n_control)]
    )
    return jsonl_bytes(rows)


def expected_selection(payload, positive_count, control_count):
    """Reference implementation of the selection formula, computed independently of make_probe."""
    parent_sha256 = hashlib.sha256(payload).hexdigest()
    key = parent_sha256.encode("ascii")
    rows = [json.loads(line) for line in payload.splitlines() if line.strip()]
    selected = set()
    for label, count in ((POSITIVE_LABEL, positive_count), (CONTROL_LABEL, control_count)):
        stratum = [item["id"] for item in rows if item["label"] == label]
        ranked = sorted(
            stratum,
            key=lambda pair_id: (hmac.new(key, pair_id.encode(), hashlib.sha256).digest(), pair_id),
        )
        selected.update(ranked[:count])
    return selected, parent_sha256


class MakeProbeTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.name)

    def tearDown(self):
        self.directory.cleanup()

    def write_parent(self, payload, name="parent.jsonl"):
        path = self.root / name
        path.write_bytes(payload)
        return path

    # -- deterministic ordering --------------------------------------------------

    def test_selection_matches_hmac_lowest_n_per_stratum_formula(self):
        payload = dataset(6, 6)
        expected, parent_sha256 = expected_selection(payload, 3, 3)
        parent = self.write_parent(payload)

        output_payload, receipt = build_probe(parent, 3, 3)

        selected_ids = {item["id"] for item in receipt["rows"]}
        self.assertEqual(selected_ids, expected)
        self.assertEqual(receipt["parent_dataset_sha256"], parent_sha256)
        output_ids = {json.loads(line)["id"] for line in output_payload.splitlines() if line}
        self.assertEqual(output_ids, expected)

    # -- exact counts per label ---------------------------------------------------

    def test_selects_exactly_the_requested_count_per_label(self):
        payload = dataset(60, 60)
        parent = self.write_parent(payload)

        output_payload, receipt = build_probe(parent, 50, 50)

        rows = [json.loads(line) for line in output_payload.splitlines() if line]
        self.assertEqual(len(rows), 100)
        self.assertEqual(sum(1 for r in rows if r["label"] == "inconsistent"), 50)
        self.assertEqual(sum(1 for r in rows if r["label"] == "consistent"), 50)
        self.assertEqual(receipt["positive_count"], 50)
        self.assertEqual(receipt["control_count"], 50)

    # -- no duplicate IDs ----------------------------------------------------------

    def test_rejects_duplicate_ids(self):
        rows = [row("org/repo/f0#pos", "inconsistent"), row("org/repo/f0#pos", "inconsistent"),
                row("org/repo/f1#ctl", "consistent")]
        payload = jsonl_bytes(rows)
        parent = self.write_parent(payload)

        with self.assertRaisesRegex(ValueError, "duplicate"):
            build_probe(parent, 1, 1)

    # -- no holdout input ------------------------------------------------------------

    def test_selector_needs_no_secondary_file_in_the_directory(self):
        payload = dataset(5, 5)
        parent = self.write_parent(payload)
        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["parent.jsonl"])

        output_payload, receipt = build_probe(parent, 2, 2)

        self.assertEqual(sorted(p.name for p in self.root.iterdir()), ["parent.jsonl"])
        self.assertEqual(len(receipt["rows"]), 4)

    def test_never_opens_any_file_other_than_the_declared_parent(self):
        payload = dataset(5, 5)
        parent = self.write_parent(payload)

        with mock.patch.object(
            make_probe.artifact, "read_bytes", wraps=make_probe.artifact.read_bytes
        ) as spy:
            build_probe(parent, 2, 2)

        self.assertEqual(spy.call_count, 1)
        self.assertEqual(Path(spy.call_args[0][0]), parent)

    # -- changed-parent rejection --------------------------------------------------

    def test_expect_parent_sha256_rejects_a_drifted_parent(self):
        payload = dataset(5, 5)
        parent = self.write_parent(payload)
        stale_sha256 = "0" * 64

        with self.assertRaisesRegex(ValueError, "sha256 mismatch"):
            build_probe(parent, 2, 2, expect_parent_sha256=stale_sha256)

    def test_expect_parent_sha256_accepts_a_matching_parent(self):
        payload = dataset(5, 5)
        parent = self.write_parent(payload)
        actual_sha256 = hashlib.sha256(payload).hexdigest()

        output_payload, receipt = build_probe(parent, 2, 2, expect_parent_sha256=actual_sha256)
        self.assertEqual(len(receipt["rows"]), 4)

    def test_verify_probe_receipt_rejects_a_parent_that_changed_after_the_probe_was_cut(self):
        payload = dataset(5, 5)
        parent = self.write_parent(payload)
        _output_payload, receipt = build_probe(parent, 2, 2)

        parent.write_bytes(payload + b'{"id": "org/repo/extra#ctl", "label": "consistent", '
                                       b'"language": "Python", "category": null, "code": "x", '
                                       b'"doc": "x", "func": "x", "source": "x", '
                                       b'"source_status": "validated"}\n')

        with self.assertRaisesRegex(ValueError, "changed"):
            verify_probe_receipt(receipt, parent)

    def test_verify_probe_receipt_accepts_an_unchanged_parent(self):
        payload = dataset(5, 5)
        parent = self.write_parent(payload)
        _output_payload, receipt = build_probe(parent, 2, 2)

        verify_probe_receipt(receipt, parent)  # must not raise

    # -- output/receipt byte determinism --------------------------------------------

    def test_output_and_receipt_bytes_are_identical_across_repeated_runs(self):
        payload = dataset(20, 20)
        parent = self.write_parent(payload)

        output_a, receipt_a = build_probe(parent, 10, 10)
        output_b, receipt_b = build_probe(parent, 10, 10)

        self.assertEqual(output_a, output_b)
        self.assertEqual(receipt_bytes(receipt_a), receipt_bytes(receipt_b))

    # -- stratum too small -----------------------------------------------------------

    def test_fails_when_positive_stratum_is_too_small(self):
        payload = dataset(3, 60)
        parent = self.write_parent(payload)

        with self.assertRaisesRegex(ValueError, "only 3.*inconsistent"):
            build_probe(parent, 50, 50)

    def test_fails_when_control_stratum_is_too_small(self):
        payload = dataset(60, 3)
        parent = self.write_parent(payload)

        with self.assertRaisesRegex(ValueError, "only 3.*consistent"):
            build_probe(parent, 50, 50)

    # -- unexpected labels / mixed languages -----------------------------------------

    def test_rejects_unexpected_label(self):
        rows = [row("org/repo/f0#pos", "drift"), row("org/repo/f1#ctl", "consistent")]
        payload = jsonl_bytes(rows)
        parent = self.write_parent(payload)

        with self.assertRaisesRegex(ValueError, "unexpected label"):
            build_probe(parent, 1, 1)

    def test_rejects_mixed_languages(self):
        rows = [row("org/repo/f0#pos", "inconsistent", language="Python"),
                row("org/repo/f1#ctl", "consistent", language="go")]
        payload = jsonl_bytes(rows)
        parent = self.write_parent(payload)

        with self.assertRaisesRegex(ValueError, "mixed languages"):
            build_probe(parent, 1, 1)

    # -- symlinked parent rejected ----------------------------------------------------

    def test_rejects_a_symlinked_parent(self):
        payload = dataset(5, 5)
        real = self.write_parent(payload, name="real.jsonl")
        link = self.root / "parent.jsonl"
        link.symlink_to(real)

        with self.assertRaises(ValueError):
            build_probe(link, 2, 2)

    # -- byte preservation, not reserialization ---------------------------------------

    def test_preserves_original_row_bytes_without_reserializing(self):
        odd_line = (b'{"id":  "org/repo/oddspace#pos",   "label": "inconsistent", '
                    b'"language": "Python", "category": null, "code": "x", "doc": "x", '
                    b'"func": "x", "source": "x", "source_status": "validated", '
                    b'"z_trailing": 1}')
        control_line = json.dumps(row("org/repo/ctl#ctl", "consistent"), sort_keys=True).encode()
        payload = odd_line + b"\n" + control_line + b"\n"
        parent = self.write_parent(payload)

        output_payload, _receipt = build_probe(parent, 1, 1)

        self.assertIn(odd_line, output_payload.splitlines())

    def test_output_preserves_parent_row_order(self):
        rows = [row("org/repo/a#pos", "inconsistent"), row("org/repo/b#ctl", "consistent"),
                row("org/repo/c#pos", "inconsistent"), row("org/repo/d#ctl", "consistent")]
        payload = jsonl_bytes(rows)
        parent = self.write_parent(payload)

        output_payload, _receipt = build_probe(parent, 2, 2)

        output_ids = [json.loads(line)["id"] for line in output_payload.splitlines() if line]
        self.assertEqual(output_ids, ["org/repo/a#pos", "org/repo/b#ctl",
                                       "org/repo/c#pos", "org/repo/d#ctl"])

    # -- pure bytes-in function works without touching the filesystem -----------------

    def test_build_probe_bytes_is_a_pure_function_of_its_payload(self):
        payload = dataset(5, 5)
        output_payload, receipt = build_probe_bytes(payload, 2, 2)
        self.assertEqual(len(receipt["rows"]), 4)
        self.assertEqual(receipt["selection_protocol"], make_probe.SELECTION_PROTOCOL)

    # -- CLI end to end -----------------------------------------------------------------

    def test_cli_writes_probe_and_receipt(self):
        payload = dataset(20, 20)
        parent = self.write_parent(payload)
        out = self.root / "probe.jsonl"
        receipt_out = self.root / "probe.receipt.json"

        completed = subprocess.run(
            [sys.executable, "-m", "eval.bench.make_probe", str(parent),
             "--positive-count", "10", "--control-count", "10",
             "--out", str(out), "--receipt-out", str(receipt_out)],
            cwd=Path(__file__).parents[1], capture_output=True, text=True, check=True,
        )

        rows = [json.loads(line) for line in out.read_bytes().splitlines() if line]
        self.assertEqual(len(rows), 20)
        receipt = json.loads(receipt_out.read_text())
        self.assertEqual(receipt["positive_count"], 10)
        self.assertEqual(receipt["control_count"], 10)
        self.assertIn("probe:", completed.stdout)


if __name__ == "__main__":
    unittest.main()
