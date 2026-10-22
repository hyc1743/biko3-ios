#!/usr/bin/env python3
"""Build and inspect the unsigned Biko3 IPA on macOS; no shell fallback."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import plistlib
import shutil
import struct
import subprocess
import sys
import tarfile
import urllib.request
import zipfile

from ci_guest import PINNED_SHA256

ROOT = Path(__file__).resolve().parents[1]
NX_COMMIT = "677345b0933f5f8754aeec3125d14df32683ca97"
MVK_VERSION = "v1.4.2"
MVK_URL = "https://github.com/KhronosGroup/MoltenVK/releases/download/v1.4.2/MoltenVK-all.tar"
MVK_SHA256 = "562a15a29bc358446a56a4091c5f7e08f604184187c1d34f712148b61ef17276"


def sha256(path):
    with Path(path).open("rb") as source:
        return hashlib.file_digest(source, "sha256").hexdigest()


def run(command, *, cwd=None, env=None, log=None):
    command = [str(value) for value in command]
    if log:
        log.parent.mkdir(parents=True, exist_ok=True)
        with log.open("w", encoding="utf-8") as output:
            result = subprocess.run(command, cwd=cwd, env=env, stdout=output, stderr=subprocess.STDOUT)
        if result.returncode:
            raise ValueError(f"{Path(command[0]).name} failed; see {log.name}")
        return ""
    return subprocess.check_output(command, cwd=cwd, env=env, text=True).strip()


def moltenvk(destination):
    """Download one fixed official release and reject checksum mismatches."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    archive = destination / "MoltenVK-all.tar"
    if not archive.exists() or sha256(archive) != MVK_SHA256:
        temporary = archive.with_suffix(".partial")
        try:
            request = urllib.request.Request(MVK_URL, headers={"User-Agent": "Biko3-iOS-build"})
            with urllib.request.urlopen(request, timeout=120) as source, temporary.open("wb") as output:
                shutil.copyfileobj(source, output)
            if sha256(temporary) != MVK_SHA256:
                raise ValueError("Official MoltenVK archive checksum mismatch")
            temporary.replace(archive)
        finally:
            temporary.unlink(missing_ok=True)
    extracted = destination / "extracted"
    if not extracted.exists():
        extracted.mkdir()
        with tarfile.open(archive) as source:
            source.extractall(extracted, filter="data")
    mvk_root = extracted / "MoltenVK/MoltenVK"
    if not (mvk_root / "static/MoltenVK.xcframework/ios-arm64/libMoltenVK.a").is_file() or not (mvk_root / "include/vulkan/vulkan.h").is_file():
        raise ValueError("Official MoltenVK release lacks the expected arm64 library and headers")
    return mvk_root


def pinned_source(source, work):
    source = Path(source).resolve()
    if not source.is_dir():
        raise ValueError("Pinned nxnative source directory is missing")
    git_checkout = (source / ".git").exists()
    if git_checkout:
        if run(["git", "-C", source, "rev-parse", "HEAD"]) != NX_COMMIT:
            raise ValueError("nxnative checkout is not pinned")
        if run(["git", "-C", source, "status", "--porcelain"]):
            raise ValueError("nxnative checkout contains local modifications")
    elif (source / "snapshot.json").is_file():
        snapshot = json.loads((source / "snapshot.json").read_text(encoding="utf-8"))
        if snapshot.get("commit") != NX_COMMIT or snapshot.get("repository") != "seedds/nxnative":
            raise ValueError("Unexpected nxnative reference snapshot")
    else:
        raise ValueError("nxnative source has no verified snapshot or Git identity")
    destination = Path(work) / "nxnative-build"
    shutil.copytree(source, destination, ignore=shutil.ignore_patterns(".git", "__pycache__"))
    if git_checkout:
        records = []
        for record in run(["git", "-C", source, "ls-tree", "-r", "-z", "HEAD"]).split("\0"):
            if record:
                metadata, path = record.split("\t", 1)
                mode, kind, digest = metadata.split()
                records.append({"path": path, "type": kind, "sha": digest, "mode": mode})
        (destination / "snapshot.json").write_text(json.dumps({"repository": "seedds/nxnative", "commit": NX_COMMIT}), encoding="utf-8")
        (destination / "tree.json").write_text(json.dumps({"tree": records}), encoding="utf-8")
    return destination


def native_vulkan_smoke(mvk, work, logs, env):
    """Run real compute and lifecycle operations; unavailable GPU is a skip."""
    library = Path(mvk) / "static/MoltenVK.xcframework/macos-arm64_x86_64/libMoltenVK.a"
    if not library.is_file():
        raise ValueError("MoltenVK release lacks the universal macOS static library")
    executable = Path(work) / "vulkan-compute-smoke"
    run(["xcrun", "--sdk", "macosx", "clang", "-std=c11", "-O1",
         "-Werror=implicit-function-declaration", "-I", Path(mvk) / "include",
         ROOT / "ios/runtime/Biko3Vulkan.c", ROOT / "tests/native/vulkan_smoke.c",
         library, "-lc++", "-framework", "Metal", "-framework", "QuartzCore",
         "-framework", "IOSurface", "-framework", "Foundation", "-o", executable],
        env=env, log=Path(logs) / "vulkan-macos-build.log")
    log = Path(logs) / "vulkan-macos-smoke.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    with log.open("w", encoding="utf-8") as output:
        try:
            result = subprocess.run([str(executable)], env=env, stdout=output,
                                    stderr=subprocess.STDOUT, timeout=120)
        except subprocess.TimeoutExpired:
            output.write("\nFAIL: native Vulkan smoke exceeded 120 seconds\n")
            raise ValueError("Native Vulkan compute smoke timed out; see vulkan-macos-smoke.log") from None
        if result.returncode == 77:
            output.write("\nSKIP: runner GPU unavailable; compute was not verified\n")
            return {"status": "skipped", "verified": False, "reason": "runner_gpu_unavailable", "exit_code": 77}
        if result.returncode:
            output.write(f"\nFAIL: native Vulkan smoke exited {result.returncode}\n")
            raise ValueError("Native Vulkan compute smoke failed; see vulkan-macos-smoke.log")
    return {"status": "passed", "verified": True, "exit_code": 0}


def bundle_notices(app, source, mvk):
    destination = Path(app) / "dm/notices"
    destination.mkdir(parents=True, exist_ok=True)
    mvk_license = Path(mvk).parent / "LICENSE"
    if not mvk_license.is_file():
        raise ValueError("MoltenVK release is missing its license")
    shutil.copyfile(mvk_license, destination / "MoltenVK-Apache-2.0.txt")
    for name in ("LICENSE", "THIRD_PARTY.md"):
        path = ROOT / name
        if path.is_file():
            shutil.copyfile(path, destination / ("Biko3-" + name))
    third_party = Path(source) / "dm/third_party"
    count = 0
    for path in sorted(third_party.rglob("*")):
        if path.is_file() and path.name.upper().startswith(("LICENSE", "COPYING", "NOTICE")):
            target = destination / "nxnative-third-party" / path.relative_to(third_party)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(path, target)
            count += 1
    return {"moltenvk_license_included": True, "nxnative_third_party_notice_files": count}


def guest_startup_result(report):
    if (report.get("status") == "skipped" and report.get("verified") is False and
            report.get("reason") == "requires_native_arm64_macos"):
        return False
    required = ("libnx_initialized", "original_main_entered", "newlib_missing_patch_observed",
                "original_main_returned", "host_returned_normally")
    if (report.get("status") != "passed" or report.get("verified") is not True or
            report.get("exit_code") != 0 or report.get("guest_main_result") != 1 or
            any(report.get("checks", {}).get(key) is not True for key in required)):
        raise ValueError("macOS guest startup evidence is incomplete or returned abnormally")
    return True


def macho_sections(data):
    """Parse a thin arm64 executable's segment/section table without macOS tools."""
    if len(data) < 32:
        raise ValueError("Mach-O header is truncated")
    magic, cpu, subtype, filetype, count, size, flags, reserved = struct.unpack_from("<8I", data)
    if magic != 0xFEEDFACF or cpu != 0x0100000C or filetype != 2 or 32 + size > len(data):
        raise ValueError("Expected a thin arm64 Mach-O executable")
    offset, sections, executable_segments = 32, {}, []
    for _ in range(count):
        if offset + 8 > 32 + size:
            raise ValueError("Mach-O load command is truncated")
        command, length = struct.unpack_from("<II", data, offset)
        if length < 8 or offset + length > 32 + size:
            raise ValueError("Mach-O load command exceeds header")
        if command == 0x19:
            if length < 72:
                raise ValueError("Mach-O segment is truncated")
            segment = struct.unpack_from("<II16s4Q4I", data, offset)
            name = segment[2].rstrip(b"\0").decode("ascii")
            if segment[8] & 4:
                executable_segments.append(name)
            nsects = segment[9]
            if 72 + nsects * 80 > length:
                raise ValueError("Mach-O section table is truncated")
            for index in range(nsects):
                section = struct.unpack_from("<16s16sQQ8I", data, offset + 72 + index * 80)
                section_name = section[0].rstrip(b"\0").decode("ascii")
                file_offset, section_size = section[4], section[3]
                if file_offset + section_size > len(data):
                    # Zero-fill sections have no corresponding bytes on disk.
                    if section[8] & 0xFF not in (1, 0xC, 0x12):
                        raise ValueError("Mach-O section exceeds executable")
                sections[(name, section_name)] = {"offset": file_offset, "size": section_size}
        offset += length
    if offset != 32 + size:
        raise ValueError("Mach-O load command size mismatch")
    return sections, executable_segments


def inspect_app(app, stage):
    app = Path(app)
    info = plistlib.loads((app / "Info.plist").read_bytes())
    if info.get("CFBundleIdentifier") != "io.github.hyc1743.biko3" or info.get("MinimumOSVersion") != "26.0":
        raise ValueError("Unexpected app identity or minimum iOS version")
    if stage.get("guest_sha256") != PINNED_SHA256 or stage.get("guest_embedded") is not True or stage.get("source_commit") != NX_COMMIT:
        raise ValueError("Staging report does not prove the pinned guest was embedded")
    executable = app / info.get("CFBundleExecutable", "Biko3")
    data = executable.read_bytes()
    sections, executable_segments = macho_sections(data)
    required = (("__TEXT", "__dmmainx0"), ("__TEXT", "__dmmainx1"), ("__DMmainW", "__img"))
    for section in required:
        if section not in sections or sections[section]["size"] <= 0:
            raise ValueError("Guest executable section is missing: " + ",".join(section))
    text = sections[required[0]]
    if text["size"] < 0x80 or data[text["offset"] + 0x10:text["offset"] + 0x14] != b"NRO0":
        raise ValueError("Embedded guest text does not contain the preserved NRO header")
    if executable_segments != ["__TEXT"]:
        raise ValueError("The app must have one executable segment (__TEXT)")
    return {"bundle_id": info["CFBundleIdentifier"], "minimum_ios": "26.0", "arm64": True,
            "guest_sections": {",".join(key): sections[key]["size"] for key in required},
            "executable_sha256": hashlib.sha256(data).hexdigest()}


def package(app, target):
    """Package the actual compiled app; refuse original inputs in resources."""
    app, target = Path(app), Path(target)
    with zipfile.ZipFile(target, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for path in sorted(app.rglob("*")):
            if not path.is_file():
                continue
            relative = path.relative_to(app)
            if path.is_symlink() or path.suffix.lower() in {".keys", ".nsp", ".nro", ".nso"} or "data" in {part.lower() for part in relative.parts}:
                raise ValueError("Game input is present in app resources: " + relative.as_posix())
            if "_CodeSignature" in relative.parts or path.name == "embedded.mobileprovision":
                continue
            archive.write(path, "Payload/Biko3.app/" + relative.as_posix())


def build(args):
    if sys.platform != "darwin":
        raise ValueError("Building an iOS IPA requires macOS with Xcode 26")
    if sha256(args.nro) != PINNED_SHA256:
        raise ValueError("The build input is not the pinned original Biko3 NRO")
    sdk = run(["xcrun", "--sdk", "iphoneos", "--show-sdk-version"])
    if int(sdk.split(".")[0]) < 26:
        raise ValueError("The selected Xcode must include the iOS 26 SDK")
    output = args.output.resolve()
    if output.exists():
        raise ValueError("Build output already exists; choose a fresh output directory")
    work = output / "work"
    work.mkdir(parents=True)
    source = pinned_source(args.nxnative_root, work)
    mvk = moltenvk(ROOT / ".tools/moltenvk-1.4.2")
    env = dict(os.environ, MOLTENVK_ROOT=str(mvk))
    env.pop("BIKO3_GUEST_KEY", None)
    logs = output / "logs"
    sdk_path = run(["xcrun", "--sdk", "iphoneos", "--show-sdk-path"])
    run(["xcrun", "--sdk", "iphoneos", "clang", "-std=c11", "-fsyntax-only",
         "-Werror=implicit-function-declaration", "-target", "arm64-apple-ios26.0",
         "-isysroot", sdk_path, "-I", mvk / "include",
         ROOT / "ios/runtime/Biko3Vulkan.c", ROOT / "tests/native/vulkan_smoke.c"],
        env=env, log=logs / "vulkan-syntax.log")
    gpu_smoke = native_vulkan_smoke(mvk, work, logs, env)
    run([sys.executable, ROOT / "tools/stage_ios.py", "--nxnative-root", source,
         "--nro", args.nro.resolve(), "--out", output], env=env, log=logs / "stage.log")
    stage = json.loads((output / "stage-report.json").read_text(encoding="utf-8"))
    run([sys.executable, ROOT / "tools/mac_guest_smoke.py", "--staged", output,
         "--moltenvk-root", mvk, "--out", logs / "guest-macos"],
        env=env, log=logs / "guest-macos-driver.log")
    guest_smoke = json.loads((logs / "guest-macos.json").read_text(encoding="utf-8"))
    startup_verified = guest_startup_result(guest_smoke)
    ios = output / "ios"
    run(["xcodegen", "generate", "--spec", ios / "project.yml"], cwd=ios, env=env, log=logs / "xcodegen.log")
    run(["xcodebuild", "-project", ios / "Biko3.xcodeproj", "-scheme", "Biko3", "-configuration", "Release",
         "-destination", "generic/platform=iOS", "-derivedDataPath", work / "DerivedData",
         "ARCHS=arm64", "IPHONEOS_DEPLOYMENT_TARGET=26.0", "CODE_SIGNING_ALLOWED=NO", "CODE_SIGNING_REQUIRED=NO",
         "CODE_SIGN_IDENTITY=", "build"], cwd=ios, env=env, log=logs / "xcodebuild.log")
    app = work / "DerivedData/Build/Products/Release-iphoneos/Biko3.app"
    app_report = inspect_app(app, stage)
    notice_report = bundle_notices(app, source, mvk)
    ipa = output / "Biko3-development.ipa"
    package(app, ipa)
    report = {"kind": "development-guest-ipa", "signed": False, "runtime_verified": False,
              "playable_verified": False, "contains_guest_executable": True, "contains_game_data": False,
              "data_import": "Documents/game/Data", "guest_sha256": PINNED_SHA256,
              "nxnative_commit": NX_COMMIT, "moltenvk_version": MVK_VERSION,
              "vulkan_bridge_syntax_checked": True, "vulkan_gpu_smoke_verified": gpu_smoke["verified"],
              "vulkan_gpu_smoke": gpu_smoke, "notices": notice_report,
              "macos_guest_startup_verified": startup_verified, "macos_guest_smoke": guest_smoke,
              "moltenvk_archive_sha256": MVK_SHA256, "ipa_bytes": ipa.stat().st_size,
              "ipa_sha256": sha256(ipa), **app_report}
    (output / "build-report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nro", type=Path, required=True)
    parser.add_argument("--nxnative-root", type=Path, default=ROOT / "references/seedds--nxnative")
    parser.add_argument("--output", type=Path, default=ROOT / "out/biko3-ios")
    build(parser.parse_args(argv))


if __name__ == "__main__":
    try:
        main()
    except (ValueError, OSError, subprocess.SubprocessError) as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
