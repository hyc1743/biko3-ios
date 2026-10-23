import json
from pathlib import Path
import plistlib
import struct
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch
import zipfile

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from build_ios import NX_COMMIT, PINNED_SHA256, bundle_notices, game_compute_smoke, guest_startup_result, renderer_startup_result, inspect_app, macho_sections, native_vulkan_smoke, package


def name(value):
    return value.encode().ljust(16, b"\0")


def executable():
    data = bytearray(0x500)
    struct.pack_into("<8I", data, 0, 0xFEEDFACF, 0x0100000C, 0, 2, 2, 232 + 152, 0, 0)
    struct.pack_into("<II16s4Q4I", data, 32, 0x19, 232, name("__TEXT"), 0, 0x4C0, 0, 0x4C0, 5, 5, 2, 0)
    struct.pack_into("<16s16sQQ8I", data, 32 + 72, name("__dmmainx0"), name("__TEXT"), 0, 0x80, 0x400, 0, 0, 0, 0, 0, 0, 0)
    struct.pack_into("<16s16sQQ8I", data, 32 + 152, name("__dmmainx1"), name("__TEXT"), 0, 0x40, 0x480, 0, 0, 0, 0, 0, 0, 0)
    struct.pack_into("<II16s4Q4I", data, 32 + 232, 0x19, 152, name("__DMmainW"), 0, 0x40, 0x4C0, 0x40, 3, 3, 1, 0)
    struct.pack_into("<16s16sQQ8I", data, 32 + 232 + 72, name("__img"), name("__DMmainW"), 0, 0x40, 0x4C0, 0, 0, 0, 0, 0, 0, 0)
    data[0x410:0x414] = b"NRO0"
    return data


class IosBuildTests(unittest.TestCase):
    def test_renderer_requires_original_ready_trace_and_windowed_mode(self):
        report = {"status": "passed", "verified": True, "mode": "windowed", "exit_code": 0,
                  "guest_main_result": 1, "checks": {key: True for key in (
                      "libnx_initialized", "original_main_entered", "newlib_missing_patch_observed",
                      "original_main_returned", "host_returned_normally", "original_renderer_ready",
                      "missing_assets_returned_one")}}
        self.assertTrue(renderer_startup_result(report))
        for changed in (dict(report, mode="headless"),
                        dict(report, checks=dict(report["checks"], original_renderer_ready=False))):
            with self.assertRaises(ValueError):
                renderer_startup_result(changed)

    def test_game_compute_requires_both_original_pipelines_or_strict_no_gpu_skip(self):
        from game_shader_smoke import EXPECTED
        passed = {"status": "passed", "verified": True, "source_nro_sha256": PINNED_SHA256,
                  "native_exit_code": 0, "pipeline_results": [
                      {"sha256": digest, "role": expected["role"], "stage": "comp",
                       "entry_point": "main", "pipeline_created": True} for digest, expected in EXPECTED.items()]}
        skipped = {"status": "skipped", "verified": False, "source_nro_sha256": PINNED_SHA256,
                   "native_exit_code": 77, "reason": "runner_gpu_unavailable",
                   "native_skip_reason": "zero_physical_devices", "pipeline_results": []}
        for code, evidence, accepted in ((0, passed, True), (77, skipped, True),
                (0, dict(passed, pipeline_results=passed["pipeline_results"][:1]), False),
                (77, dict(skipped, native_skip_reason="shader_failed"), False),
                (1, dict(passed, status="failed", verified=False), False)):
            with self.subTest(code=code, accepted=accepted), tempfile.TemporaryDirectory() as tmp:
                root = Path(tmp)
                destination = root / "game-compute"
                destination.mkdir()
                (destination / "result.json").write_text(json.dumps(evidence))
                with patch("build_ios.subprocess.run", return_value=subprocess.CompletedProcess([], code)):
                    if accepted:
                        result = game_compute_smoke(root / "guest.nro", root / "mvk", root, root / "logs", {})
                        self.assertEqual(result["verified"], code == 0)
                    else:
                        with self.assertRaises(ValueError):
                            game_compute_smoke(root / "guest.nro", root / "mvk", root, root / "logs", {})
                self.assertEqual(json.loads((root / "logs/game-compute.json").read_text()), evidence)

    def test_guest_startup_requires_every_trace_and_normal_guest_return(self):
        report = {"status": "passed", "verified": True, "exit_code": 0, "guest_main_result": 1,
                  "checks": {key: True for key in ("libnx_initialized", "original_main_entered",
                      "newlib_missing_patch_observed", "original_main_returned", "host_returned_normally")}}
        self.assertTrue(guest_startup_result(report))
        for changed in (dict(report, guest_main_result=-1), dict(report, guest_main_result=0), dict(report, exit_code=-11),
                        dict(report, checks={}), dict(report, verified=False), dict(report, status="failed")):
            with self.assertRaises(ValueError):
                guest_startup_result(changed)
        self.assertFalse(guest_startup_result({"status": "skipped", "verified": False,
                                              "reason": "requires_native_arm64_macos"}))
    def test_native_smoke_distinguishes_pass_skip_and_failure_and_has_deadline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            library = root / "mvk/static/MoltenVK.xcframework/macos-arm64_x86_64/libMoltenVK.a"
            library.parent.mkdir(parents=True)
            library.write_bytes(b"fixture")
            for code, status in ((0, "passed"), (77, "skipped"), (1, "failed")):
                with self.subTest(code=code), patch("build_ios.run"), patch("build_ios.subprocess.run", return_value=subprocess.CompletedProcess([], code)) as invoke:
                    if code == 1:
                        with self.assertRaisesRegex(ValueError, "smoke failed"):
                            native_vulkan_smoke(root / "mvk", root, root / "logs", {})
                    else:
                        result = native_vulkan_smoke(root / "mvk", root, root / "logs", {})
                        self.assertEqual(result["status"], status)
                        self.assertEqual(result["verified"], code == 0)
                    self.assertEqual(invoke.call_args.kwargs["timeout"], 120)
            with patch("build_ios.run"), patch("build_ios.subprocess.run", side_effect=subprocess.TimeoutExpired("smoke", 120)):
                with self.assertRaisesRegex(ValueError, "timed out"):
                    native_vulkan_smoke(root / "mvk", root, root / "logs", {})
            self.assertIn("exceeded 120 seconds", (root / "logs/vulkan-macos-smoke.log").read_text())

    def test_dependency_notices_are_copied_into_the_actual_app(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            mvk = root / "MoltenVK/MoltenVK"
            mvk.mkdir(parents=True)
            (mvk.parent / "LICENSE").write_text("Apache license fixture")
            source = root / "source"
            license = source / "dm/third_party/libvpx/LICENSE"
            license.parent.mkdir(parents=True)
            license.write_text("libvpx license fixture")
            app = root / "Biko3.app"
            report = bundle_notices(app, source, mvk)
            self.assertEqual(report["nxnative_third_party_notice_files"], 1)
            self.assertEqual((app / "dm/notices/MoltenVK-Apache-2.0.txt").read_text(), "Apache license fixture")
            self.assertEqual((app / "dm/notices/nxnative-third-party/libvpx/LICENSE").read_text(), "libvpx license fixture")

    def test_guest_embedded_sections_and_preserved_header_are_required(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = Path(tmp) / "Biko3.app"
            app.mkdir()
            (app / "Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier": "io.github.hyc1743.biko3", "MinimumOSVersion": "26.0", "CFBundleExecutable": "Biko3", "CFBundleIcons": {"CFBundlePrimaryIcon": {"CFBundleIconName": "AppIcon"}}}))
            (app / "Assets.car").write_bytes(b"compiled icon fixture")
            binary = executable()
            (app / "Biko3").write_bytes(binary)
            stage = {"guest_sha256": PINNED_SHA256, "source_commit": NX_COMMIT, "guest_embedded": True}
            report = inspect_app(app, stage)
            self.assertTrue(report["arm64"])
            self.assertEqual(len(report["guest_sections"]), 3)
            localized = app / "dm/localization/patch.pp"
            localized.parent.mkdir(parents=True)
            localized.write_bytes(b"reviewed patch fixture")
            import hashlib
            localization = {"included": True, "bytes": localized.stat().st_size,
                            "sha256": hashlib.sha256(localized.read_bytes()).hexdigest()}
            with self.assertRaisesRegex(ValueError, "Unrecorded localization"):
                inspect_app(app, stage)
            self.assertEqual(inspect_app(app, dict(stage, localization_patch=localization))["localization_patch"], localization)
            localized.write_bytes(b"corrupt")
            with self.assertRaisesRegex(ValueError, "localization patch"):
                inspect_app(app, dict(stage, localization_patch=localization))
            localized.unlink()
            (app / "Assets.car").unlink()
            with self.assertRaisesRegex(ValueError, "application icon"):
                inspect_app(app, stage)
            (app / "Assets.car").write_bytes(b"compiled icon fixture")
            for changed in (dict(stage, guest_embedded=False), dict(stage, guest_sha256="0" * 64)):
                with self.assertRaises(ValueError):
                    inspect_app(app, changed)
            binary[0x410:0x414] = b"FAKE"
            (app / "Biko3").write_bytes(binary)
            with self.assertRaisesRegex(ValueError, "preserved NRO header"):
                inspect_app(app, stage)

    def test_malformed_or_wrong_architecture_macho_is_rejected(self):
        for data in (b"shell", executable()[:300]):
            with self.assertRaises(ValueError):
                macho_sections(data)
        data = executable()
        struct.pack_into("<I", data, 4, 7)
        with self.assertRaisesRegex(ValueError, "arm64"):
            macho_sections(data)

    def test_ipa_has_compiled_app_and_excludes_signing_material(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = root / "Biko3.app"
            app.mkdir()
            (app / "Biko3").write_bytes(executable())
            (app / "embedded.mobileprovision").write_bytes(b"profile")
            target = root / "build.ipa"
            package(app, target)
            with zipfile.ZipFile(target) as archive:
                self.assertEqual(archive.namelist(), ["Payload/Biko3.app/Biko3"])
            (app / "game.nro").write_bytes(b"input")
            with self.assertRaises(ValueError):
                package(app, target)


if __name__ == "__main__":
    unittest.main()
