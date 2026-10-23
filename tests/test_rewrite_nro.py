import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from nro import Nro, NroError
from rewrite_nro import END, SCAN, START, make_overlay, reviewed_sources, verify_outputs
from test_nro import sample


class RewriteTests(unittest.TestCase):
    def test_overlay_executes_hash_bound_boundaries_and_excludes_header(self):
        fde = {"source_nro_sha256": "abc", "functions": [{"start": 0x80, "size": 4}]}
        carrier = b"carrier"
        source = START + "\nraise AssertionError('old header parser ran')\n" + END + "\n" + SCAN + "\n"
        adapted = make_overlay(source, fde, hashlib.sha256(carrier).hexdigest())
        class Code:
            def data_blocks(self, *_): return [(0x40, 0x100), (0x200, 0x204)]
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            (p / "function-boundaries.json").write_text(json.dumps(fde))
            ns = {"__file__": str(p / "patch_nso.py"), "d": carrier, "TEXT_END": 0x1000,
                  "IMG": b"", "md": None, "nso_code": Code(), "os": __import__("os")}
            exec(adapted, ns)
            self.assertEqual(ns["starts"], [0x80])
            self.assertEqual(ns["DATA_BLOCKS"], [(0, 0x100), (0x200, 0x204)])
            ns["d"] = b"different"
            with self.assertRaisesRegex(AssertionError, "carrier hash"):
                exec(adapted, ns)
            ns["d"] = carrier
            fde["functions"][0]["start"] = 0x84
            (p / "function-boundaries.json").write_text(json.dumps(fde))
            with self.assertRaisesRegex(AssertionError, "manifest mismatch"):
                exec(adapted, ns)

    def test_unknown_source_anchors_fail_closed(self):
        with self.assertRaises(NroError):
            make_overlay("unreviewed", {}, "")

    def test_wrong_snapshot_rejected_before_execution(self):
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            (p / "snapshot.json").write_text(json.dumps({"commit": "wrong", "repository": "seedds/nxnative"}))
            with self.assertRaisesRegex(NroError, "snapshot identity"):
                reviewed_sources(p)

    def test_artifact_validation_rejects_header_and_data_corruption(self):
        image = Nro(bytes(sample()))
        with tempfile.TemporaryDirectory() as directory:
            p = Path(directory)
            layout = {"TEXT_END": "0x1000", "RO_OFF": "0x1000", "DATA_OFF": "0x2000", "BUILD_ID": image.build_id[:32]}
            (p / "main.fixups.tsv").write_text("".join(f"# {k}\t{v}\n" for k, v in layout.items()))
            (p / "main.sites.tsv").write_text("0x80\tsvc\n")
            (p / "main.gates.tsv").write_text("0x4000\tsvc\t1\t0x80\n")
            (p / "main.patched.bin").write_bytes(image.data)
            self.assertTrue(verify_outputs(p, "main", image)["header_and_data_preserved"])
            for offset in (0, 0x1000, 0x2000):
                bad = bytearray(image.data)
                bad[offset] ^= 1
                (p / "main.patched.bin").write_bytes(bad)
                with self.assertRaises(NroError):
                    verify_outputs(p, "main", image)
            (p / "main.patched.bin").write_bytes(image.data)
            (p / "main.sites.tsv").write_text("0x40\tx18\n")
            with self.assertRaisesRegex(NroError, "header was scanned"):
                verify_outputs(p, "main", image)


if __name__ == "__main__":
    unittest.main()
