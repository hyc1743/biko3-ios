import struct
import unittest
from pathlib import Path
import sys
import tempfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from biko3_assets import decode_tbl, index_entries, legacy_entries, replace_entry


class Biko3AssetTests(unittest.TestCase):
    def test_decode_tbl_rejects_bad_header(self):
        with self.assertRaises(ValueError):
            decode_tbl(b"\0" * 32)

    def test_real_decoder_output_is_record_aligned(self):
        source = Path("out/translation-inspect/Data/bk3_03.tbl")
        if not source.is_file():
            self.skipTest("local game Data is not available")
        decoded = decode_tbl(source.read_bytes())
        self.assertEqual(decoded[0x800:0x804], struct.pack("<I", 0x50))
        self.assertTrue(decoded[0x810:0x81b].startswith(b"m001_15.bmp"))

    def test_index_entries_uses_bucket_offsets_and_bounds(self):
        decoded = bytearray(0x800 + 0x50)
        for bucket in range(256):
            struct.pack_into("<II", decoded, bucket * 8, 0 if bucket <= ord('h') else 80, int(bucket == ord('h')))
        struct.pack_into("<I", decoded, 0x800, 80)
        struct.pack_into("<II", decoded, 0x804, 123, 7)
        decoded[0x810:0x817] = b"hello\0\0"
        self.assertEqual(index_entries(bytes(decoded), 200), [("hello", 123, 7)])
        with self.assertRaises(ValueError):
            index_entries(bytes(decoded), 100)

    def test_legacy_archive_negation_and_extent(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "fixture.pp"
            name = b"dialog.txt".ljust(32, b"\0")
            path.write_bytes(struct.pack("<II", 1, 3) + bytes((-v) & 255 for v in name)
                             + struct.pack("<I", 3) + b"abc")
            self.assertEqual(legacy_entries(path), [("dialog.txt", 44, 3)])
            path.write_bytes(path.read_bytes() + b"extra")
            with self.assertRaises(ValueError):
                legacy_entries(path)

    def test_replace_entry_requires_same_size(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "x.pp"
            path.write_bytes(b"0123456789")
            replace_entry(path, 2, 4, b"ABCD")
            self.assertEqual(path.read_bytes(), b"01ABCD6789")
            with self.assertRaises(ValueError):
                replace_entry(path, 2, 4, b"too long")


if __name__ == "__main__":
    unittest.main()
