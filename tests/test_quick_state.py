"""Compile and run the portable fixed-slot snapshot transaction tests."""
from pathlib import Path
import os
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class QuickStateTests(unittest.TestCase):
    def test_native_snapshot_transactions(self):
        configured = shlex.split(os.environ.get("CC", ""))
        compiler = configured if configured and shutil.which(configured[0]) else None
        if compiler is None:
            for candidate in ("cc", "clang", "gcc"):
                if shutil.which(candidate):
                    compiler = [candidate]
                    break
        if compiler is None:
            self.skipTest("a C compiler is required for the native snapshot test")
        with tempfile.TemporaryDirectory(prefix="biko3-quick-state-") as directory:
            executable = Path(directory) / ("quick-state.exe" if os.name == "nt" else "quick-state")
            command = [*compiler, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-pedantic",
                       "-I", str(ROOT / "ios/runtime"), str(ROOT / "ios/runtime/Biko3QuickState.c"),
                       str(ROOT / "tests/native/quick_state.c"), "-lm", "-o", str(executable)]
            compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("QUICK_STATE_ALL_PASSED cases=4", ran.stdout)


if __name__ == "__main__":
    unittest.main()
