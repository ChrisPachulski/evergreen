import base64
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest import mock


from ci import change_manifest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ci" / "change_manifest.py"


class ChangeManifestTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp_dir.name)
        self.git("init", "-q")
        self.git("config", "user.email", "test@example.com")
        self.git("config", "user.name", "Test")

    def tearDown(self):
        self.temp_dir.cleanup()

    def git(self, *args):
        return subprocess.run(
            ["git", *args],
            cwd=self.repo,
            check=True,
            stdout=subprocess.PIPE,
            text=True,
        ).stdout.strip()

    def write(self, name, content):
        path = self.repo / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")

    def commit(self, message):
        self.git("add", "-A")
        self.git("commit", "-qm", message)
        return self.git("rev-parse", "HEAD")

    def test_reports_statuses_seeds_unusual_names_and_stable_order(self):
        self.write("modify.py", "def old_name():\n    return 1\n")
        self.write("delete.py", "def removed():\n    return 1\n")
        self.write("before.py", "def moved():\n    return 1\n")
        base = self.commit("base")

        self.write(
            "modify.py",
            "def renamed_handler():\n"
            "    flag = '--workers'\n"
            "    env = 'SERVICE_TOKEN'\n"
            "    route = '/v1/items'\n"
            "    return flag, env, route\n",
        )
        (self.repo / "delete.py").unlink()
        self.git("mv", "before.py", "renamed.py")
        unusual = "odd name\twith newline\n.py"
        self.write(unusual, "def added_contract():\n    return True\n")
        head = self.commit("changes")

        first = change_manifest.build_manifest(self.repo, base, head)
        second = change_manifest.build_manifest(self.repo, base, head)

        self.assertEqual(first, second)
        self.assertEqual(first["schema_version"], 1)
        self.assertEqual(first["base"], base)
        self.assertEqual(first["head"], head)
        self.assertFalse(first["truncated"])
        self.assertTrue(any("not citable" in error for error in first["errors"]))
        self.assertEqual(
            [(item["status"], item.get("old_path"), item["path"]) for item in first["files"]],
            [
                ("D", None, "delete.py"),
                ("M", None, "modify.py"),
                ("A", None, unusual),
                ("R", "before.py", "renamed.py"),
            ],
        )
        self.assertTrue(all(item["hunks"] for item in first["files"] if item["status"] != "R"))
        self.assertEqual(first["contract_seeds"], sorted(first["contract_seeds"]))
        for seed in ("renamed_handler", "--workers", "SERVICE_TOKEN", "/v1/items"):
            self.assertIn(seed, first["contract_seeds"])

    def test_stops_before_a_hunk_that_exceeds_the_byte_budget(self):
        original = "".join(f"line {number}\n" for number in range(30))
        self.write("large.txt", original)
        base = self.commit("base")
        lines = original.splitlines(keepends=True)
        lines[1] = "first_changed_identifier\n"
        lines[25] = "second_changed_identifier\n"
        self.write("large.txt", "".join(lines))
        head = self.commit("changes")

        complete = change_manifest.build_manifest(self.repo, base, head)
        hunks = complete["files"][0]["hunks"]
        self.assertEqual(len(hunks), 2)
        first_hunk_bytes = len(hunks[0].encode("utf-8"))

        bounded = change_manifest.build_manifest(self.repo, base, head, max_bytes=first_hunk_bytes)
        self.assertEqual(bounded["files"][0]["hunks"], [hunks[0]])
        self.assertTrue(bounded["truncated"])

    def test_token_dense_omitted_hunks_do_not_create_unbounded_seeds(self):
        original = "".join(f"line {number}\n" for number in range(100))
        self.write("dense.txt", original)
        base = self.commit("base")
        lines = original.splitlines(keepends=True)
        lines[1] = "small_contract_seed\n"
        lines[80] = " ".join(f"dense_contract_{number}" for number in range(20_000)) + "\n"
        self.write("dense.txt", "".join(lines))
        head = self.commit("changes")

        complete = change_manifest.build_manifest(self.repo, base, head)
        first_hunk_bytes = len(complete["files"][0]["hunks"][0].encode("utf-8"))
        bounded = change_manifest.build_manifest(self.repo, base, head, max_bytes=first_hunk_bytes)
        serialized = json.dumps(bounded, ensure_ascii=False).encode("utf-8")
        hunk_bytes = sum(
            len(hunk.encode("utf-8")) for file in bounded["files"] for hunk in file["hunks"]
        )
        seed_bytes = len(json.dumps(bounded["contract_seeds"], ensure_ascii=False).encode("utf-8"))

        self.assertTrue(bounded["truncated"])
        self.assertLessEqual(hunk_bytes, first_hunk_bytes)
        self.assertLessEqual(seed_bytes, first_hunk_bytes)
        self.assertLessEqual(len(serialized), first_hunk_bytes * 3 + 4096)
        self.assertIn("small_contract_seed", bounded["contract_seeds"])
        self.assertNotIn("dense_contract_19999", bounded["contract_seeds"])

    @unittest.skipUnless(os.name == "posix", "Git byte-path identity is POSIX-specific")
    def test_invalid_utf8_paths_keep_lossless_identity_and_report_display_collision(self):
        self.write("base.txt", "base\n")
        base = self.commit("base")
        raw_paths = [b"invalid-\xfe.py", b"invalid-\xff.py"]
        blob = subprocess.run(
            [b"git", b"hash-object", b"-w", b"--stdin"],
            cwd=os.fsencode(self.repo),
            input=b"def byte_named_contract():\n    return True\n",
            check=True,
            stdout=subprocess.PIPE,
        ).stdout.strip()
        for raw_path in raw_paths:
            subprocess.run(
                [b"git", b"update-index", b"--add", b"--cacheinfo", b"100644", blob, raw_path],
                cwd=os.fsencode(self.repo),
                check=True,
            )
        self.git("commit", "-qm", "invalid byte paths")
        head = self.git("rev-parse", "HEAD")

        manifest = change_manifest.build_manifest(self.repo, base, head)

        self.assertEqual(len(manifest["files"]), 2)
        self.assertEqual(
            {item["path_bytes_b64"] for item in manifest["files"]},
            {base64.b64encode(path).decode("ascii") for path in raw_paths},
        )
        self.assertEqual(len({item["path"] for item in manifest["files"]}), 1)
        self.assertTrue(all(item["hunks"] for item in manifest["files"]))
        self.assertTrue(any("invalid UTF-8" in error for error in manifest["errors"]))
        self.assertTrue(any("display collision" in error for error in manifest["errors"]))

    def test_invalid_refs_are_reported_without_raising(self):
        self.write("file.py", "value = 1\n")
        head = self.commit("base")

        manifest = change_manifest.build_manifest(self.repo, "not-a-ref", head)

        self.assertEqual(manifest["files"], [])
        self.assertEqual(manifest["contract_seeds"], [])
        self.assertFalse(manifest["truncated"])
        self.assertEqual(len(manifest["errors"]), 1)
        self.assertIn("invalid base ref", manifest["errors"][0])

    def test_protocol_incompatible_changed_path_makes_manifest_incomplete(self):
        self.write("base.txt", "base\n")
        base = self.commit("base")
        self.write("line\nbreak.py", "value = 1\n")
        head = self.commit("unquotable path")

        manifest = change_manifest.build_manifest(self.repo, base, head)

        self.assertTrue(any("not citable" in error for error in manifest["errors"]))

    def test_git_deadline_failure_is_a_manifest_error(self):
        self.write("file.py", "value = 1\n")
        head = self.commit("base")

        with mock.patch.object(
            change_manifest, "run_bounded",
            return_value=(124, b"", "command timed out after 0.1 seconds"),
            create=True,
        ):
            manifest = change_manifest.build_manifest(
                self.repo, head, head, timeout_seconds=0.1
            )

        self.assertTrue(manifest["errors"])
        self.assertTrue(any("timed out" in error for error in manifest["errors"]))

    def test_cli_prints_one_json_object(self):
        self.write("file.py", "value = 1\n")
        base = self.commit("base")
        self.write("file.py", "value = 2\n")
        head = self.commit("change")

        result = subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--base",
                base,
                "--head",
                head,
                "--repo",
                str(self.repo),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )

        self.assertEqual(len(result.stdout.strip().splitlines()), 1)
        self.assertEqual(json.loads(result.stdout)["files"][0]["path"], "file.py")
        self.assertEqual(result.stderr, "")


if __name__ == "__main__":
    unittest.main()
