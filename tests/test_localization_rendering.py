"""Verify authored-text rendering invariants with optional local fonts."""
from pathlib import Path
import importlib.util
import tempfile
import unittest
import sys

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools"))


class LocalizationPolicyTests(unittest.TestCase):
    def test_old_character_name_batches_are_rejected_before_resource_reads(self):
        import json
        from build_localization_supplement import build
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            batch = root / "old-batch"
            batch.mkdir()
            (batch / "replacements.json").write_text(json.dumps({"schema": 1, "replacements": [
                {"archive": "bk3_00", "name": "pr_13.tga"}]}))
            with self.assertRaisesRegex(ValueError, "character-name"):
                build(root / "missing-data", [batch], root / "rejected")

    def test_name_preservation_policy_covers_named_portraits_and_labels(self):
        from build_localization_supplement import PRESERVED_NAMES
        for name in ("al_05.tga", "al_07.tga", "pr_13.tga", "pr_15.tga", "pr_18.tga", "pr_20.tga"):
            self.assertIn("bk3_00." + name, PRESERVED_NAMES)


@unittest.skipUnless(importlib.util.find_spec("PIL"), "Pillow is optional for rendering tools")
class LocalizationRenderingTests(unittest.TestCase):
    def test_local_area_render_preserves_outside_pixels_and_removes_old_glyph_alpha(self):
        from PIL import Image
        from render_localization_areas import render
        font = Path("C:/Windows/Fonts/NotoSansSC-VF.ttf")
        if not font.is_file():
            self.skipTest("local reviewed Chinese font unavailable")
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, destination = root / "lo_74.tga", root / "translated.tga"
            image = Image.new("RGBA", (128, 64), (220, 230, 240, 204))
            image.putpixel((0, 0), (40, 50, 60, 0))
            image.putpixel((11, 18), (255, 255, 255, 255))
            image.save(source)
            original = source.read_bytes()
            record = render(source, destination, 1, font)
            self.assertEqual(source.read_bytes(), original)
            self.assertFalse(record["runtime_verified"])
            with Image.open(destination) as changed:
                self.assertEqual(changed.getpixel((11, 18))[3], 204)
                self.assertEqual(changed.getpixel((0, 0)), image.getpixel((0, 0)))
                self.assertNotEqual(changed.crop((10, 17, 118, 49)).tobytes(), image.crop((10, 17, 118, 49)).tobytes())


if __name__ == "__main__":
    unittest.main()


class LocalizationCatalogTests(unittest.TestCase):
    def test_catalog_stays_in_sync_with_merged_manifest(self):
        """The shipped ja-zh-CN.json must equal the tool output for the
        merged manifest: every replacement hash-bound and name-preserved."""
        import json
        from sync_localization_catalog import sync, CATALOG, PRESERVED_NAMES
        manifest = ROOT / "out/localization-104-v1/replacements/replacements.json"
        if not manifest.is_file():
            self.skipTest("merged localization manifest not present")
        expected = sync(manifest)
        current = json.loads(CATALOG.read_text(encoding="utf-8"))
        self.assertEqual(current, expected)

    def test_merged_replacements_never_include_preserved_names(self):
        import json
        from sync_localization_catalog import PRESERVED_NAMES
        manifest = ROOT / "out/localization-104-v1/replacements/replacements.json"
        if not manifest.is_file():
            self.skipTest("merged localization manifest not present")
        rows = json.loads(manifest.read_text(encoding="utf-8"))["replacements"]
        names = {row["archive"] + "." + row["name"] for row in rows}
        self.assertTrue(names.isdisjoint("bk3_00." + name for name in PRESERVED_NAMES))
