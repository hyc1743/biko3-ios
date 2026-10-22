import hashlib
import json
from pathlib import Path
import struct
import sys
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import patch_guest_hooks as hooks
from nro import NroError
from test_nro import sample


class VulkanPatchTests(unittest.TestCase):
    def test_branch_signed_reach_and_alignment(self):
        for delta in (-0x8000000, -4, 0, 0x7fffffc):
            word = struct.unpack("<I", hooks.branch(0x8000000, 0x8000000+delta))[0]
            imm = word & 0x3ffffff
            if imm & (1 << 25): imm -= 1 << 26
            self.assertEqual(imm*4, delta)
        for delta in (-0x8000004, 0x8000000, 2):
            with self.assertRaises(NroError): hooks.branch(0, delta)

    def test_stub_decodes_to_exact_slot_without_x18(self):
        for pc in (0xfb0000, 0x102fff0):
            for slot in (0, 1, 1024, 2047):
                adrp, ldr, branch = struct.unpack("<III", hooks.stub(pc,slot))
                imm = ((adrp >> 29) & 3) | (((adrp >> 5) & 0x7ffff) << 2)
                if imm & (1 << 20): imm -= 1 << 21
                address = (pc & ~0xfff) + imm*4096 + ((ldr >> 10) & 0xfff)*8
                self.assertEqual(address, hooks.TABLE_OFF+slot*8)
                self.assertEqual(adrp & 31, 16)
                self.assertEqual((ldr & 31, (ldr >> 5) & 31), (16,16))
                self.assertEqual(branch, 0xd61f0200)
        with self.assertRaises(NroError): hooks.stub(0xfb0000,2048)

    def fixture(self):
        original = bytes(sample())
        image = original + bytes(hooks.PATCH_OFF-len(original))
        manifest = {"source_nro_sha256": hashlib.sha256(original).hexdigest(),
                    "functions":[{"start":0x80,"size":16}]}
        return original,image,manifest

    def test_patcher_preserves_original_and_existing_gates(self):
        original,image,manifest = self.fixture()
        with patch.object(hooks,"SOURCE_SHA",hashlib.sha256(original).hexdigest()), patch.object(hooks,"REWRITTEN_SHA",hashlib.sha256(image).hexdigest()):
            result, report = hooks.apply_hooks(original,image,manifest,[{"offset":0x80,"slot":0,"symbol":"vkCreateInstance"}],guard=False)
        self.assertEqual(result[:0x80],original[:0x80])
        self.assertEqual(result[0x84:len(image)],image[0x84:])
        self.assertEqual(report["hooks"][0]["stub_offset"],len(image))
        self.assertEqual(result[len(image):],hooks.stub(len(image),0))

    def test_unverified_input_or_non_fde_hook_rejected(self):
        original,image,manifest = self.fixture()
        with self.assertRaisesRegex(NroError,"source NRO"):
            hooks.apply_hooks(original,image,manifest,[],guard=False)
        with patch.object(hooks,"SOURCE_SHA",hashlib.sha256(original).hexdigest()), patch.object(hooks,"REWRITTEN_SHA",hashlib.sha256(image).hexdigest()):
            with self.assertRaisesRegex(NroError,"FDE text entry"):
                hooks.apply_hooks(original,image,manifest,[{"offset":0x84,"slot":0}],guard=False)
            with self.assertRaisesRegex(NroError,"duplicate"):
                hooks.apply_hooks(original,image,manifest,[{"offset":0x80,"slot":0}]*2,guard=False)

    def test_manifest_covers_compute_and_direct_api_resolver(self):
        root = Path(__file__).resolve().parents[1]
        manifest = json.loads((root/"ios/runtime/vulkan-hooks.json").read_text())
        names = {h["symbol"] for h in manifest["hooks"]}
        self.assertIn("vkCreateComputePipelines",names)
        self.assertIn("vkCmdDispatch",names)
        self.assertIn("vkEnumeratePhysicalDevices",names)
        self.assertEqual(manifest["hooks"][0]["symbol"],"vkGetInstanceProcAddr")
        self.assertEqual(manifest["hooks"][1]["symbol"],"vkGetDeviceProcAddr")
        exports=(root/"ios/runtime/Biko3VulkanExports.inc").read_text()
        for name in names-{"vkCreateViSurfaceNN"}:
            self.assertIn(f"BIKO3_VK_EXPORT({name})",exports)

if __name__=="__main__": unittest.main()
