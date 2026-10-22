from pathlib import Path, PurePosixPath
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from mac_guest_smoke import runtime_evidence, smoke_makefile


class MacGuestSmokeTests(unittest.TestCase):
    def test_partial_or_aborted_boot_cannot_pass(self):
        log = "[biko3] entering original main +0x99280\n"
        checks, result = runtime_evidence(log, -11)
        self.assertFalse(all(checks.values()))
        self.assertFalse(checks["libnx_initialized"])
        self.assertFalse(checks["host_returned_normally"])
        self.assertIsNone(result)

    def test_filesystem_check_requires_actual_missing_stat_signature(self):
        checks, _ = runtime_evidence("patch.pp is absent\n", 0)
        self.assertFalse(checks["newlib_missing_patch_observed"])
        checks, _ = runtime_evidence("[biko3] newlib stat /tmp/game/patch.pp -> 0 bytes=20\n", 0)
        self.assertFalse(checks["newlib_missing_patch_observed"])
        checks, _ = runtime_evidence("[biko3] newlib stat /tmp/game/patch.pp -> -1 errno=2\n", 0)
        self.assertTrue(checks["newlib_missing_patch_observed"])

    def test_build_overlay_requires_real_runtime_source_list(self):
        with self.assertRaisesRegex(ValueError, "runtime"):
            smoke_makefile("CFLAGS=-O1\n", Path("/tmp/MoltenVK"))
        original = "CFLAGS=-O1\nSRC=Biko3Libnx.c Biko3FS.c Biko3Hooks.c main.c\nclang $(CFLAGS) -framework UniformTypeIdentifiers\n"
        result = smoke_makefile(original, PurePosixPath("/tmp/MoltenVK folder"))
        self.assertIn("-mcpu=apple-m2", result)
        self.assertIn("macos-arm64_x86_64/libMoltenVK.a", result)
        self.assertIn("-lc++ -framework IOSurface", result)
        self.assertIn("SRC=Biko3Libnx.c Biko3FS.c Biko3Hooks.c main.c", result)


if __name__ == "__main__":
    unittest.main()
