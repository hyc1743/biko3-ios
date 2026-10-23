import hashlib
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))
from patch_libnx_host import patch_main


class LibnxOverlayTests(unittest.TestCase):
    def test_unknown_host_rejected(self):
        with self.assertRaisesRegex(ValueError, "reviewed"):
            patch_main(b"unreviewed")

    def test_reviewed_host_enters_libnx_instead_of_sdk(self):
        source = ROOT / "references/seedds--nxnative/dm/host/main.c"
        if not source.exists():
            self.skipTest("reviewed reference not present")
        result = patch_main(source.read_bytes()).decode("utf8")
        guest = result[result.index("static void guest_main"):result.index("struct dm_flags g_flag")]
        self.assertIn("guest_thread_init();", guest)
        self.assertIn("svc_thread_main_init();", guest)
        self.assertIn("biko3_libnx_boot(g_mod[0].base", guest)
        self.assertNotIn('req("', guest)
        self.assertNotIn("run_module_inits", guest)
        with self.assertRaises(ValueError):
            patch_main(result.encode("utf8"))

    def test_hook_manifest_is_bound_to_original_bytes_and_fde_entries(self):
        nro = ROOT / "switch/biko3/biko3-runtime.nro"
        boundaries = ROOT / "work/biko3-ios/nxnative-preparation/main.function-boundaries.json"
        if not nro.exists() or not boundaries.exists():
            self.skipTest("offline input not present")
        data = nro.read_bytes()
        manifest = json.loads((ROOT / "ios/runtime/libnx-hooks.json").read_text())
        self.assertEqual(hashlib.sha256(data).hexdigest(), manifest["source_nro_sha256"])
        fdes = {f["start"] for f in json.loads(boundaries.read_text())["functions"]}
        slots = set()
        for hook in manifest["hooks"]:
            offset = int(hook["offset"], 16)
            self.assertIn(offset, fdes)
            self.assertEqual(data[offset:offset + 16].hex(), hook["expected"])
            self.assertNotIn(hook["slot"], slots)
            self.assertGreaterEqual(hook["slot"], 1024)
            self.assertLess(hook["slot"], 2048)
            slots.add(hook["slot"])


if __name__ == "__main__":
    unittest.main()
