import struct
import sys
import tempfile
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from extract_embedded_spirv import MAGIC, PROFILES, extract, parse_module, reference_evidence
from nro import NroError


def module(two_functions=False):
    words = [MAGIC, 0x10000, 0, 32, 0,
             (5 << 16) | 15, 5, 4, 0x6E69616D, 0,
             (5 << 16) | 54, 1, 4, 0, 2,
             (1 << 16) | 56]
    if two_functions:
        words += [(5 << 16) | 54, 1, 5, 0, 2, (1 << 16) | 56]
    return struct.pack(f"<{len(words)}I", *words)


class SpirvTests(unittest.TestCase):
    def test_module_can_have_multiple_functions_without_padding(self):
        parsed = parse_module(module(True))
        self.assertEqual(parsed["function_count"], 2)
        self.assertEqual(parsed["entry_points"][0]["stage"], "comp")

    def test_padding_and_concatenated_header_are_not_valid_module_extent(self):
        for raw in [module() + b"\0" * 4, module() + module(), module()[:-4], module() + b"x"]:
            with self.subTest(length=len(raw)), self.assertRaises(NroError):
                parse_module(raw)

    def test_instruction_crossing_declared_length_is_rejected(self):
        raw = bytearray(module())
        struct.pack_into("<I", raw, 20, (600 << 16) | 15)
        with self.assertRaisesRegex(NroError, "instruction boundary"):
            parse_module(raw)

    def test_reference_store_must_use_correct_createinfo_field(self):
        # A tiny synthetic ARM64 reference with real MOV/STR/ADRP/ADD/BL words.
        raw = bytearray(64)
        struct.pack_into("<I", raw, 0, 0xD2800100)  # mov x0, #8
        struct.pack_into("<I", raw, 4, 0xF9000FE0)  # str x0, [sp, #24]
        struct.pack_into("<I", raw, 8, 0x90000001)  # adrp x1, #0
        struct.pack_into("<I", raw, 12, 0x91008021) # add x1, x1, #32
        struct.pack_into("<I", raw, 16, 0xF90013E1) # str x1, [sp, #32]
        struct.pack_into("<I", raw, 20, 0x94000000 | ((0xD2760 - 20) // 4))
        profile = (32, 0, 4, [8, 12], 16, 0, 20, "test")
        self.assertEqual(reference_evidence(raw, profile)["size_bytes"], 8)
        struct.pack_into("<I", raw, 4, 0xF9000BE0)  # store at wrong field #16
        with self.assertRaisesRegex(NroError, "field store"):
            reference_evidence(raw, profile)

    def test_unknown_binary_rejected_without_creating_output(self):
        with tempfile.TemporaryDirectory() as directory:
            out = Path(directory) / "out"
            with self.assertRaisesRegex(NroError, "reviewed static reference"):
                extract(b"unreviewed", out)
            self.assertFalse(out.exists())


if __name__ == "__main__":
    unittest.main()
