import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest


class HostTestCase(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.home = Path(self.temporary.name) / "home with spaces"
        self.home.mkdir()

    def tearDown(self):
        self.temporary.cleanup()

    def set_test_xattr(self, path, suffix, value):
        if hasattr(os, "setxattr"):
            name = f"user.{suffix}"
            try:
                os.setxattr(path, name, value)
            except OSError:
                return None
            return name
        tool = shutil.which("xattr")
        if tool:
            name = f"com.evergreen.{suffix}"
            result = subprocess.run(
                [tool, "-w", name, value.decode("ascii"), str(path)],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=False,
            )
            return name if result.returncode == 0 else None
        return None

    def get_test_xattr(self, path, name):
        if hasattr(os, "getxattr"):
            return os.getxattr(path, name)
        result = subprocess.run(
            [shutil.which("xattr"), "-p", name, str(path)],
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, check=True,
        )
        return result.stdout.rstrip(b"\n")

    def snapshot(self, include_directories=False):
        result = {}
        for path in self.home.rglob("*"):
            if path.name == ".evergreen-host.lock":
                continue
            relative = path.relative_to(self.home).as_posix()
            if path.is_symlink():
                result[relative] = ("link", os.readlink(path))
            elif path.is_file():
                result[relative] = ("file", path.read_bytes())
            elif include_directories:
                result[relative] = ("directory",)
        return result
