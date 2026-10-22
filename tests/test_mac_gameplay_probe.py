from pathlib import Path
import hashlib
import io
import json
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from mac_gameplay_probe import DEFAULT_KEYS, download_parts, main, observations, validate_keys


class GameplayProbeTests(unittest.TestCase):
    def test_default_startup_script_selects_start_then_load(self):
        times = [int(step.split(":", 1)[0]) for step in DEFAULT_KEYS.split(",")]
        self.assertEqual(times[0], 2)
        self.assertEqual(times[-1], 80)
        self.assertGreaterEqual(min(b - a for a, b in zip(times, times[1:])), 6)
    def test_download_checks_every_pinned_part_and_rejects_tampering(self):
        parts = [{"name": f"Biko3Data.part{i+1:03d}.b3data", "size": 4,
                  "sha256": hashlib.sha256(b"part").hexdigest()} for i in range(6)]
        with tempfile.TemporaryDirectory() as tmp:
            directory = Path(tmp) / "parts"
            with patch("mac_gameplay_probe.urllib.request.urlopen", side_effect=lambda *a, **k: io.BytesIO(b"part")) as fetch:
                result = download_parts(directory, {"parts": parts})
            self.assertEqual(len(result["parts"]), 6)
            self.assertEqual(fetch.call_count, 6)
            for call in fetch.call_args_list:
                self.assertIn("/releases/download/v0.1.0-dev.3/Biko3Data.part", call.args[0].full_url)
        with tempfile.TemporaryDirectory() as tmp, patch("mac_gameplay_probe.urllib.request.urlopen", return_value=io.BytesIO(b"evil")):
            directory = Path(tmp) / "parts"
            with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
                download_parts(directory, {"parts": parts})
            self.assertFalse((directory / parts[0]["name"]).exists())
            self.assertFalse(list(directory.glob("*.partial")))

    def test_renderer_or_survival_does_not_prove_active_game_scene(self):
        result = observations("Renderer ready: device\n[biko3-probe] frame=120 mode=1 bank=0 area=0 clock=2.0\n")
        self.assertTrue(result["renderer_ready"])
        self.assertFalse(result["gameplay_active_observed"])
        self.assertEqual(result["probe_frames"][0]["mode"], 1)

    def test_only_exact_probe_live_scene_signature_counts(self):
        text = "[biko3-probe] gameplay-active frame=240 group=3 area=7 live_clock=4.500000000\n"
        result = observations(text)
        self.assertTrue(result["gameplay_active_observed"])
        self.assertEqual(result["active_scene_events"], [{"frame": 240, "group": 3, "area": 7, "live_clock": 4.5}])
        self.assertFalse(observations("DM_KEYS: unknown key " + text)["gameplay_active_observed"])
        self.assertFalse(observations("[biko3-probe] gameplay-active maybe\n")["gameplay_active_observed"])

    def test_input_script_cannot_forge_newline_evidence(self):
        self.assertEqual(validate_keys("2:A,4:A"), "2:A,4:A")
        for text in ("2:A\n[biko3-probe] gameplay-active", "A" * 4096):
            with self.assertRaises(ValueError):
                validate_keys(text)

    def test_non_arm64_mac_is_explicit_unverified_skip_without_reading_assets(self):
        with tempfile.TemporaryDirectory() as tmp, patch("mac_gameplay_probe.sys.platform", "win32"):
            output = Path(tmp) / "probe"
            with patch("mac_gameplay_probe.verify") as restore:
                code = main(["--staged", "absent", "--moltenvk-root", "absent", "--parts", "absent", "--out", str(output)])
            restore.assert_not_called()
            self.assertEqual(code, 0)
            report = json.loads((output / "evidence/report.json").read_text())
            self.assertEqual(report["status"], "skipped")
            self.assertFalse(report["gameplay_verified"])
            self.assertFalse(report["runtime_verified"])


if __name__ == "__main__":
    unittest.main()
