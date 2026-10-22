import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from stage_ios import binding_header, replace_once


class StageTests(unittest.TestCase):
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
