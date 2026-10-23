from pathlib import Path
import os
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]

class PatchPathTests(unittest.TestCase):
    def test_native_patch_fallback_and_root_precedence(self):
        compiler = next((shutil.which(c) for c in ("cc", "clang", "gcc") if shutil.which(c)), None)
        if not compiler or os.name == "nt":
            self.skipTest("requires a POSIX C compiler")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Data").mkdir()
            executable = root / "test-fs"
            built = subprocess.run([compiler, "-std=c11", "-Wall", "-Wextra", "-Werror",
                                    "-I", str(ROOT / "ios/runtime"),
                                    str(ROOT / "tests/native/fs_patch.c"), "-o", str(executable)],
                                   capture_output=True, text=True, timeout=60)
            self.assertEqual(built.returncode, 0, built.stdout + built.stderr)
            ran = subprocess.run([str(executable), str(root)], capture_output=True, text=True, timeout=15)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("FS_PATCH_ALL_PASSED", ran.stdout)
