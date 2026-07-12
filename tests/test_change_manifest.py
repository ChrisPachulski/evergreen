import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "ci" / "change_manifest.py"
SPEC = importlib.util.spec_from_file_location("change_manifest", SCRIPT)
change_manifest = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(change_manifest)


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
        self.assertEqual(first["errors"], [])
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

    def test_invalid_refs_are_reported_without_raising(self):
        self.write("file.py", "value = 1\n")
        head = self.commit("base")

        manifest = change_manifest.build_manifest(self.repo, "not-a-ref", head)

        self.assertEqual(manifest["files"], [])
        self.assertEqual(manifest["contract_seeds"], [])
        self.assertFalse(manifest["truncated"])
        self.assertEqual(len(manifest["errors"]), 1)
        self.assertIn("invalid base ref", manifest["errors"][0])

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
