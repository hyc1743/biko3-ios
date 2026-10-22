import sys
import re
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from stage_ios import binding_header, replace_once, gamepad


class StageTests(unittest.TestCase):
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
