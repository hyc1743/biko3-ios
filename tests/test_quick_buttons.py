"""Compile and run the real pure-C controller state machine on portable CI."""
from pathlib import Path
import os
import shlex
import shutil
import subprocess
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]


class QuickButtonsTests(unittest.TestCase):
    def test_native_button_transitions(self):
        configured = shlex.split(os.environ.get("CC", ""))
        compiler = configured if configured and shutil.which(configured[0]) else None
        if compiler is None:
            for candidate in ("cc", "clang", "gcc"):
                if shutil.which(candidate):
                    compiler = [candidate]
                    break
        if compiler is None:
            self.skipTest("a C compiler is required for the native state-machine test")
        with tempfile.TemporaryDirectory(prefix="biko3-quick-buttons-") as directory:
            executable = Path(directory) / ("quick-buttons.exe" if os.name == "nt" else "quick-buttons")
            command = [*compiler, "-std=c11", "-O2", "-Wall", "-Wextra", "-Werror", "-pedantic",
                       "-I", str(ROOT / "ios/runtime"), str(ROOT / "ios/runtime/Biko3QuickButtons.c"),
                       str(ROOT / "tests/native/quick_buttons.c"), "-o", str(executable)]
            compiled = subprocess.run(command, capture_output=True, text=True, timeout=60)
            self.assertEqual(compiled.returncode, 0, compiled.stdout + compiled.stderr)
            ran = subprocess.run([str(executable)], capture_output=True, text=True, timeout=10)
            self.assertEqual(ran.returncode, 0, ran.stdout + ran.stderr)
            self.assertIn("QUICK_BUTTONS_PASS cases=7", ran.stdout)


if __name__ == "__main__":
    unittest.main()
