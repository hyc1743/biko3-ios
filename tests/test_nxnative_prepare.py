import contextlib
import io
import json
from pathlib import Path
import struct
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from nro import NroError
from prepare_nxnative import compatibility, main, recover_eh_frame, to_nso, validated_nro, verify_nso
from test_nro import sample


def frame_sample():
    data = sample((0x2300,))
    data[0x40:0x60] = bytes(range(32))
    # Empty eh_frame_hdr, followed by a zR CIE and one PC-relative sdata4 FDE.
    marker = 0x1200
    struct.pack_into("<ii", data, 0xF0 + 16, marker - 0xF0, marker - 0xF0)
    cie = marker + 4
    body = struct.pack("<I", 0) + b"\x01zR\0\x04\x78\x1e\x01\x1b\x0c\x1f\0"
    struct.pack_into("<I", data, cie, len(body))
    data[cie + 4:cie + 4 + len(body)] = body
    fde = cie + 4 + len(body)
    body = struct.pack("<Iii", fde + 4 - cie, 0x100 - (fde + 8), 0x40) + b"\0\0\0\0"
    struct.pack_into("<I", data, fde, len(body))
    data[fde + 4:fde + 4 + len(body)] = body
    return data, cie, fde


class NxnativePreparationTests(unittest.TestCase):
    def test_carrier_preserves_segments_build_id_bss_and_excludes_footer(self):
        raw, _, _ = frame_sample()
        raw += b"ASSET FOOTER"
        image = validated_nro(bytes(raw))
        nso = to_nso(image)
        self.assertEqual(nso[:4], b"NSO0")
        self.assertEqual(nso[0x40:0x60], bytes(range(32)))
        self.assertEqual(len(nso), 0x3100)
        self.assertNotIn(b"ASSET FOOTER", nso)
        for i, segment in enumerate(image.segments):
            fo, mo, size = struct.unpack_from("<III", nso, 0x10 + i * 16)
            self.assertEqual(mo, segment.offset)
            self.assertEqual(nso[fo:fo + size], raw[mo:mo + size])
        self.assertEqual(struct.unpack_from("<I", nso, 0x3C)[0], 0x1000)
        self.assertEqual(image.relr()[0]["target"], 0x2300)
        verify_nso(nso, image)

    def test_carrier_verification_rejects_corruption_and_extra_payload(self):
        image = validated_nro(bytes(frame_sample()[0]))
        original = to_nso(image)
        for offset in (0, 0xC, 0x14, 0x3C, 0x40, 0xA0, 0x100, 0x1100, 0x2100):
            bad = bytearray(original)
            bad[offset] ^= 1
            with self.subTest(offset=offset), self.assertRaises(NroError):
                verify_nso(bytes(bad), image)
        with self.assertRaises(NroError):
            verify_nso(original + b"footer", image)

    def test_invalid_nro_flags_mod0_and_relr_rejected_before_conversion(self):
        flags = sample()
        struct.pack_into("<I", flags, 0x1C, 1)
        for bad in (bytes(flags), bytes(sample(mod0=False)), bytes(sample((3,)))):
            with self.assertRaises(NroError):
                validated_nro(bad)

    def test_recovers_fde_without_treating_instruction_patterns_as_functions(self):
        data, cie, fde = frame_sample()
        result = recover_eh_frame(validated_nro(bytes(data)))
        self.assertEqual(result["cie_count"], 1)
        self.assertEqual(result["function_count"], 1)
        self.assertEqual(result["functions"], [{"start": 0x100, "size": 0x40, "fde_offset": fde, "cie_offset": cie}])
        self.assertEqual(result["eh_frame_start"], cie)
        self.assertEqual(len(result["eh_frame_sha256"]), 64)

    def test_bad_fde_reference_range_and_encoding_are_explicit_errors(self):
        for change in ("reference", "range", "encoding", "length", "augmentation"):
            data, cie, fde = frame_sample()
            if change == "reference":
                struct.pack_into("<I", data, fde + 4, 1)
            elif change == "range":
                struct.pack_into("<i", data, fde + 12, 0x3000)
            elif change == "encoding":
                data[cie + 16] = 0x12  # udata2 is deliberately unsupported
            elif change == "augmentation":
                data[cie + 10] = ord("X")
            else:
                struct.pack_into("<I", data, fde, 0xFFFFFFFF)
            with self.subTest(change=change), self.assertRaises(NroError):
                recover_eh_frame(validated_nro(bytes(data)))

    def test_empty_eh_header_is_a_known_original_rewriter_blocker(self):
        result = compatibility(validated_nro(bytes(frame_sample()[0])))
        self.assertTrue(result["rewrite_blockers"])

    def test_cli_produces_manifest_and_boundary_artifact_without_source_execution(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            path = root / "game.nro"
            path.write_bytes(bytes(frame_sample()[0]) + b"FOOTER")
            with contextlib.redirect_stdout(io.StringIO()):
                self.assertEqual(main([str(path), "--out", str(root / "out")]), 0)
            manifest = json.loads((root / "out/main.preparation.json").read_text())
            self.assertEqual(manifest["status"], "converted_offline_only")
            self.assertEqual(manifest["source_nro"]["excluded_footer_size"], 6)
            self.assertEqual(manifest["function_boundaries"]["function_count"], 1)
            self.assertNotIn("rewrite", manifest)
            with self.assertRaisesRegex(NroError, "already exist"):
                main([str(path), "--out", str(root / "out")])


if __name__ == "__main__":
    unittest.main()
