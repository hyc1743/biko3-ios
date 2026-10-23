import sys
import re
import unittest
import tempfile
import hashlib
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from stage_ios import binding_header, replace_once, gamepad, stage_localization
from biko3_assets import write_legacy_archive


class StageTests(unittest.TestCase):
    def test_bundled_localization_requires_review_and_exact_digest(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = Path(tmp) / "source"
            source.mkdir()
            target = Path(tmp) / "bundle"
            self.assertEqual(stage_localization(source, target), {"included": False})
            patch = source / "patch.pp"
            write_legacy_archive(patch, [("patch.cfg", b"BKPT\x02\0\0\0\x03\0\0\0")])
            with self.assertRaises(ValueError):
                stage_localization(source, target)
            spec = {"schema": 1, "file": "patch.pp", "bytes": patch.stat().st_size,
                    "sha256": hashlib.sha256(patch.read_bytes()).hexdigest(), "reviewed": False,
                    "base_patch_sha256": "5aa69af72f240d6cf2fa1d12293564256bef120b1c1a70cb210e597927df9f42"}
            manifest = source / "bundle.json"
            manifest.write_text(json.dumps(spec))
            with self.assertRaises(ValueError):
                stage_localization(source, target)
            spec["reviewed"] = True
            manifest.write_text(json.dumps(spec))
            self.assertTrue(stage_localization(source, target)["included"])
            self.assertEqual((target / "patch.pp").read_bytes(), patch.read_bytes())
            patch.write_bytes(patch.read_bytes() + b"corrupt")
            with self.assertRaises(ValueError):
                stage_localization(source, Path(tmp) / "second")

    def test_touch_and_physical_buttons_agree_on_biko3_positions(self):
        original = '''switch (b) { case B_A: return CGPointMake(c.x + g, c.y); case B_B: return CGPointMake(c.x, c.y + g); case B_X: return CGPointMake(c.x, c.y - g); default: return CGPointMake(c.x - g, c.y); }
K(g.buttonB, B_A); K(g.buttonA, B_B); K(g.buttonY, B_X); K(g.buttonX, B_Y);   // by position: right A, bottom B, top X, left Y'''
        patched = gamepad(original)
        touch_positions = {}
        for key, x, y in re.findall(r'(?:case B_([ABX]):|default:) return CGPointMake\(([^,]+), ([^)]+)\)', patched):
            key = key or "Y"
            touch_positions[key] = (eval(x, {"__builtins__": {}}, {"c": type("Point", (), {"x": 0, "y": 0})(), "g": 1}),
                                    eval(y, {"__builtins__": {}}, {"c": type("Point", (), {"x": 0, "y": 0})(), "g": 1}))
        self.assertEqual(touch_positions, {"Y": (0, -1), "A": (0, 1), "X": (-1, 0), "B": (1, 0)})
        physical = dict(re.findall(r'K\(g.button([ABXY]), B_([ABXY])\)', patched))
        self.assertEqual(physical, {"A": "A", "B": "B", "X": "X", "Y": "Y"})

    def test_binding_names_cannot_inject_c_or_duplicate_slots(self):
        base = {"table_offset": 0xFAC000, "table_size": 0x4000}
        for records in (
            [{"symbol": 'bad";abort();//', "slot": 0}],
            [{"symbol": "vkCreateInstance", "slot": 0}, {"symbol": "other", "slot": 0}],
        ):
            with self.subTest(records=records), self.assertRaises(ValueError):
                binding_header(dict(base, hooks=records), "00" * 32)

    def test_versioned_overlay_requires_one_exact_anchor(self):
        for text in ("unknown", "anchor anchor"):
            with self.subTest(text=text), self.assertRaises(ValueError):
                replace_once(text, "anchor", "replacement")


if __name__ == "__main__":
    unittest.main()
