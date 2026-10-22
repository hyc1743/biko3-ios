import copy
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from pack_resources import MANIFEST, PackError, inventory, pack, read_manifest, source_identity, validate_manifest, verify


class ResourcePackTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.data = self.root / "Data"
        (self.data / "nested").mkdir(parents=True)
        self.originals = {"a.pp": bytes(range(31)), "empty.tbl": b"", "nested/b.tbl": bytes(range(20))}
        for name, content in self.originals.items(): (self.data / name).write_bytes(content)
        self.parts = self.root / "parts"

    def create(self, limit=17):
        return pack(self.data, self.parts, part_bytes=limit)

    def test_raw_cross_volume_roundtrip_and_source_identity(self):
        with patch("pack_resources.CHUNK", 7): manifest = self.create()
        self.assertEqual([part["size"] for part in manifest["parts"]], [17, 17, 17])
        self.assertEqual(manifest["total_bytes"], 51)
        self.assertEqual(manifest["file_count"], 3)
        self.assertEqual(manifest["files"][1]["segments"], [])
        raw = b"".join((self.parts / part["name"]).read_bytes() for part in manifest["parts"])
        self.assertEqual(raw, b"".join(self.originals[name] for name in sorted(self.originals)))
        for item in manifest["files"]:
            recovered = b"".join((self.parts / manifest["parts"][s["part"]]["name"]).read_bytes()[s["offset"]:s["offset"]+s["length"]]
                                 for s in item["segments"])
            self.assertEqual(recovered, self.originals[item["path"][5:]])
        result = verify(self.parts, read_manifest(self.parts / MANIFEST), data=self.data,
                        restore=self.root / "restored")
        self.assertTrue(result["verified"])
        self.assertTrue(result["source_verified"])
        self.assertEqual(result["source_identity_sha256"], source_identity(manifest["files"]))
        for name, content in self.originals.items():
            self.assertEqual((self.root / "restored/Data" / name).read_bytes(), content)
            self.assertEqual((self.data / name).read_bytes(), content)

    def test_partial_final_volume_and_all_empty_files(self):
        manifest = self.create(limit=20)
        self.assertEqual([p["size"] for p in manifest["parts"]], [20, 20, 11])
        empty = self.root / "empty-data"; empty.mkdir(); (empty / "zero").write_bytes(b"")
        empty_parts = self.root / "empty-parts"
        empty_manifest = pack(empty, empty_parts, part_bytes=9)
        self.assertEqual(empty_manifest["parts"], [])
        self.assertTrue(verify(empty_parts, empty_manifest, data=empty)["verified"])

    def test_corrupt_truncated_and_appended_parts_fail(self):
        manifest = self.create()
        path = self.parts / manifest["parts"][0]["name"]
        raw = path.read_bytes()
        for damaged in (bytes([raw[0] ^ 1])+raw[1:], raw[:-1], raw+b"x"):
            with self.subTest(length=len(damaged)):
                path.write_bytes(damaged)
                with self.assertRaises(PackError): verify(self.parts, manifest)
        path.write_bytes(raw)
        path.unlink()
        with self.assertRaises(OSError): verify(self.parts, manifest)

    def test_file_hash_and_original_source_hash_are_checked_independently(self):
        manifest = self.create()
        forged = copy.deepcopy(manifest)
        forged["files"][0]["sha256"] = "0"*64
        forged["source_identity_sha256"] = source_identity(forged["files"])
        with self.assertRaisesRegex(PackError, "file SHA256"):
            verify(self.parts, forged)
        changed = bytearray(self.originals["a.pp"]); changed[0] ^= 1
        (self.data / "a.pp").write_bytes(changed)
        with self.assertRaisesRegex(PackError, "source SHA256"):
            verify(self.parts, manifest, data=self.data)

    def test_manifest_gaps_overlaps_path_escape_and_case_collision_fail(self):
        manifest = self.create()
        variants = []
        for offset in (-1, 1):
            bad = copy.deepcopy(manifest); bad["files"][0]["segments"][0]["offset"] = offset; variants.append(bad)
        bad = copy.deepcopy(manifest); bad["files"][0]["segments"][0]["length"] += 1; variants.append(bad)
        for unsafe in ("../escape", "Data/../escape", "Data/a\\escape", "Data//a", "Data/C:/escape", "Data/a."):
            bad = copy.deepcopy(manifest); bad["files"][0]["path"] = unsafe; variants.append(bad)
        bad = copy.deepcopy(manifest); bad["files"][1]["path"] = "Data/A.PP"; variants.append(bad)
        bad = copy.deepcopy(manifest); bad["files"][1]["path"] = "Data/a.pp/child"; variants.append(bad)
        bad = copy.deepcopy(manifest); bad["parts"][0]["name"] = "../outside"; variants.append(bad)
        bad = copy.deepcopy(manifest); bad["schema"] = True; variants.append(bad)
        for i, bad in enumerate(variants):
            with self.subTest(case=i), self.assertRaises(PackError): validate_manifest(bad)

    def test_duplicate_json_keys_are_rejected(self):
        self.create()
        path = self.root / "ambiguous.json"
        path.write_text('{"schema":1,"schema":2}')
        with self.assertRaisesRegex(PackError, "duplicate JSON"):
            read_manifest(path)

    def test_case_aliases_of_parent_directories_are_rejected(self):
        manifest = self.create()
        manifest["files"][0]["path"] = "Data/Folder/a.pp"
        manifest["files"][1]["path"] = "Data/folder/empty.tbl"
        manifest["source_identity_sha256"] = source_identity(manifest["files"])
        with self.assertRaisesRegex(PackError, "case-colliding"):
            validate_manifest(manifest)

    def test_existing_outputs_and_outputs_inside_source_are_refused(self):
        self.create()
        with self.assertRaises(FileExistsError): self.create()
        with self.assertRaisesRegex(PackError, "outside"):
            pack(self.data, self.data / "nested/output")
        with self.assertRaises(FileExistsError):
            verify(self.parts, read_manifest(self.parts / MANIFEST), restore=self.data)

    def test_source_mutation_during_pack_never_publishes_manifest(self):
        def mutate(_part):
            (self.data / "a.pp").write_bytes(b"changed original during copy")
        with self.assertRaises(PackError):
            pack(self.data, self.parts, part_bytes=17, progress=mutate)
        self.assertFalse((self.parts / MANIFEST).exists())

    def test_symlink_sources_and_parts_are_rejected(self):
        outside = self.root / "outside"; outside.write_bytes(b"unrelated")
        link = self.data / "link"
        try:
            os.symlink(outside, link)
        except OSError as error:
            self.skipTest(f"host cannot create symlinks: {error}")
        with self.assertRaisesRegex(PackError, "symlink"):
            inventory(self.data)
        link.unlink()
        manifest = self.create()
        part = self.parts / manifest["parts"][0]["name"]
        part.unlink(); os.symlink(outside, part)
        with self.assertRaisesRegex(PackError, "symlink"):
            verify(self.parts, manifest)

    def test_source_case_collision_when_filesystem_supports_it(self):
        (self.data / "A.PP").write_bytes(b"different")
        if len([p for p in self.data.iterdir() if p.name.casefold() == "a.pp"]) != 2:
            self.skipTest("filesystem is case insensitive; manifest collision test still runs")
        with self.assertRaisesRegex(PackError, "colliding"):
            inventory(self.data)

    def test_reparse_attribute_is_rejected_without_following_it(self):
        class Reparse:
            st_mode = 0o100644
            st_file_attributes = 0x400
        with patch.object(Path, "lstat", return_value=Reparse()):
            with self.assertRaisesRegex(PackError, "reparse"):
                inventory(self.data)


if __name__ == "__main__": unittest.main()
