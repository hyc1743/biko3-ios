import struct
import unittest
from pathlib import Path
import sys
import tempfile
import json
import hashlib

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from biko3_assets import (decode_tbl, index_entries, legacy_entries, replace_entry,
                         apply_resource_patch, encode_resource_patch, write_legacy_archive,
                         supplement_patch, build_inventory, audit_existing_patch)


class Biko3AssetTests(unittest.TestCase):
    def test_supplement_preserves_existing_chinese_and_requires_source_hash(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            data = root / "Data"
            data.mkdir()
            replacements = root / "replacements"
            replacements.mkdir()
            original = b"original Japanese fixture"
            chinese = b"existing Chinese fixture"
            translated = b"new Japanese-only translation fixture"
            config = b"BKPT\x02\0\0\0\x03\0\0\0"
            preserved = encode_resource_patch(b"other original", b"other Chinese")
            write_legacy_archive(data / "bk3_05.pp", [("dialog.txt", original)])
            existing = encode_resource_patch(original, chinese)
            write_legacy_archive(data / "patch.pp", [("patch.cfg", config),
                                 ("bk3_05.dialog.txt", existing), ("bk3_00.keep.tga", preserved)])
            (replacements / "dialog.txt").write_bytes(translated)
            record = {"archive": "bk3_05", "name": "dialog.txt", "file": "dialog.txt",
                      "effective_sha256": hashlib.sha256(chinese).hexdigest(),
                      "replacement_sha256": hashlib.sha256(translated).hexdigest()}
            manifest = root / "manifest.json"
            manifest.write_text(json.dumps({"schema": 1, "replacements": [record]}))
            output = root / "merged.pp"
            report = supplement_patch(data, replacements, manifest, output)
            self.assertEqual(report["replacements"], 1)
            with output.open("rb") as stream:
                result = {}
                for name, offset, size in legacy_entries(output):
                    stream.seek(offset)
                    result[name] = bytes((-v) & 255 for v in stream.read(size))
            self.assertEqual(result["patch.cfg"], config)
            self.assertEqual(result["bk3_00.keep.tga"], preserved)
            self.assertEqual(apply_resource_patch(original, result["bk3_05.dialog.txt"]), translated)
            record["effective_sha256"] = "0" * 64
            manifest.write_text(json.dumps({"schema": 1, "replacements": [record]}))
            with self.assertRaisesRegex(ValueError, "source hash"):
                supplement_patch(data, replacements, manifest, root / "rejected.pp")
            inventory = build_inventory(data, root / "inventory.json")
            self.assertEqual(len(inventory["archives"]), 2)
            self.assertFalse(inventory["complete_localization"])

    def test_patch_audit_checks_legacy_base_and_loose_font(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            config = b"BKPT\x02\0\0\0\x03\0\0\0"
            write_legacy_archive(root / "bk3_05.pp", [("test.txt", b"original")])
            (root / "Type_G.FTT").write_bytes(b"font-base")
            write_legacy_archive(root / "patch.pp", [
                ("patch.cfg", config),
                ("bk3_05.test.txt", encode_resource_patch(b"original", b"Chinese")),
                ("fonts.type_g.ftt", encode_resource_patch(b"font-base", b"new-font")),
            ])
            report = audit_existing_patch(root, root / "report.json")
            self.assertEqual(len(report["records"]), 2)
            self.assertTrue(all(r["format_validated"] for r in report["records"]))
            self.assertFalse(report["runtime_verified"])
            (root / "Type_G.FTT").write_bytes(b"different-length-font")
            with self.assertRaisesRegex(ValueError, "base identity"):
                audit_existing_patch(root, root / "invalid.json")

    def test_resource_patch_roundtrip_and_wrong_base_rejected(self):
        for replacement in (b"short", b"long translated resource" * 10):
            patch = encode_resource_patch(b"original", replacement)
            self.assertEqual(apply_resource_patch(b"original", patch), replacement)
            with self.assertRaises(ValueError):
                apply_resource_patch(b"wrong base size", patch)
            with self.assertRaises(ValueError):
                apply_resource_patch(b"original", patch + b"trailing")

    def test_legacy_writer_roundtrip_and_no_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "patch.pp"
            entries = [("patch.cfg", b"BKPT\0"), ("bk3_00.test.tga", bytes(range(256)))]
            write_legacy_archive(path, entries)
            with path.open("rb") as stream:
                for expected, (name, offset, size) in zip(entries, legacy_entries(path)):
                    stream.seek(offset)
                    self.assertEqual((name, bytes((-v) & 255 for v in stream.read(size))), expected)
            with self.assertRaises(ValueError):
                write_legacy_archive(path, entries)

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
