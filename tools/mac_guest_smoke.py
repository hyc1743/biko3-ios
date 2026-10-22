#!/usr/bin/env python3
"""Execute the real staged Biko3 guest on an arm64 Mac with empty game assets.

This exercises startup and guest newlib, not iOS execution or game playability.
The host uses its normal dynamic code loader; no iOS signed embed is substituted.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shlex
import subprocess
import sys
import time

PROFILE = "000000000000B103"


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def smoke_makefile(text, moltenvk):
    """Change only build-copy compilation/link flags, retaining its actual SRC."""
    library = moltenvk / "static/MoltenVK.xcframework/macos-arm64_x86_64/libMoltenVK.a"
    if "Biko3Libnx.c" not in text or "Biko3FS.c" not in text or "Biko3Hooks.c" not in text:
        raise ValueError("staged host Makefile does not compile Biko3 runtime sources")
    cflags, count = re.subn(r"^CFLAGS=(.*)$", lambda m: m.group(0) +
                           " -mcpu=apple-m2 -I" + shlex.quote(str(moltenvk / "include")),
                           text, count=1, flags=re.MULTILINE)
    if count != 1:
        raise ValueError("staged Makefile CFLAGS anchor changed")
    anchor = " -framework UniformTypeIdentifiers"
    if cflags.count(anchor) != 1:
        raise ValueError("staged Makefile macOS link anchor changed")
    return cflags.replace(anchor, anchor + " " + shlex.quote(str(library)) +
                          " -lc++ -framework IOSurface -framework AppKit -framework CoreGraphics -framework IOKit", 1)


def runtime_evidence(log, exit_code):
    returned = re.search(r"\[biko3\] original main returned (-?\d+)", log)
    checks = {
        "libnx_initialized": "[biko3] original libnx init completed" in log,
        "original_main_entered": "[biko3] entering original main +0x99280" in log,
        "newlib_missing_patch_observed": bool(re.search(
            r"\[biko3\] newlib stat [^\r\n]*[/]patch\.pp -> -1 errno=2(?:\r?\n|$)", log)),
        "original_main_returned": returned is not None,
        "host_returned_normally": exit_code == 0,
    }
    return checks, int(returned.group(1)) if returned else None


def run_smoke(staged, moltenvk, prefix, timeout=120):
    prefix = prefix.resolve()
    prefix.parent.mkdir(parents=True, exist_ok=True)
    report_path = Path(str(prefix) + ".json")
    build_log, run_log = Path(str(prefix) + ".build.log"), Path(str(prefix) + ".run.log")
    report = {
        "schema": 1, "status": "failed", "verified": False,
        "platform": platform.system(), "architecture": platform.machine(),
        "ios_device_execution": False, "gameplay_verified": False,
        "scope": "real arm64 macOS guest startup/newlib with an empty game/Data directory",
        "build_log": build_log.name, "run_log": run_log.name,
    }
    started = time.monotonic()
    try:
        if sys.platform != "darwin" or platform.machine().lower() != "arm64":
            report.update(status="skipped", reason="requires_native_arm64_macos")
            return report
        staged, moltenvk = staged.resolve(), moltenvk.resolve()
        host = staged / "work/nxnative-staged/dm/host"
        bindings = staged / "rewritten-bindings"
        stage_report = json.loads((staged / "stage-report.json").read_text(encoding="utf8"))
        if stage_report["host_root"] != "work/nxnative-staged/dm/host":
            raise ValueError("unexpected staged host layout")
        patched = bindings / "main.patched.bin"
        if digest(patched) != stage_report["patched_sha256"]:
            raise ValueError("staged guest differs from verified hook image")
        if not (host.parent.parent / "snapshot.json").is_file():
            raise ValueError("missing staged reference identity")
        library = moltenvk / "static/MoltenVK.xcframework/macos-arm64_x86_64/libMoltenVK.a"
        if not library.is_file() or not (moltenvk / "include/vulkan/vulkan.h").is_file():
            raise ValueError("MoltenVK macOS universal library and Vulkan headers are required")
        if "biko3_hook_bindings" not in (host.parent / "games" / PROFILE / "Biko3HookTable.h").read_text():
            raise ValueError("generated guest hook bindings are missing")
        makefile = host / "Makefile.biko3-smoke"
        makefile.write_text(smoke_makefile((host / "Makefile").read_text(encoding="utf8"), moltenvk),
                            encoding="utf8")
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("DM_", "BIKO3_"))}
        env["GAME"] = PROFILE
        build = ["make", "-f", makefile.name, "-j", "3", "GAME=" + PROFILE, "dmhost"]
        with build_log.open("w", encoding="utf8") as output:
            compiled = subprocess.run(build, cwd=host, env=env, stdout=output,
                                      stderr=subprocess.STDOUT, timeout=1200)
        report["build_exit_code"] = compiled.returncode
        if compiled.returncode:
            raise ValueError("macOS host compilation failed")
        # A fresh directory is required; a stale/nonempty asset tree cannot turn
        # this missing-resource check into an accidental gameplay execution.
        empty_root = prefix.parent / (prefix.name + ".empty-assets")
        empty_root.mkdir(exist_ok=False)
        game, saves, captures = (empty_root / name for name in ("game", "saves", "captures"))
        (game / "Data").mkdir(parents=True)
        saves.mkdir(); captures.mkdir()
        env.update(BIKO3_GAME_ROOT=str(game), BIKO3_SAVE_ROOT=str(saves),
                   BIKO3_CAPTURE_ROOT=str(captures), BIKO3_FS_TRACE="1",
                   DM_ROMFS=str(game), DM_SAVE=str(saves), DM_TEMP=str(empty_root),
                   DM_AUDIO="1", DM_SVC="1")
        report.update(guest_sha256=stage_report["guest_sha256"],
                      patched_sha256=digest(patched), hook_count=stage_report["hook_count"],
                      host_sha256=digest(host / "dmhost"), compiled=True)
        with run_log.open("w", encoding="utf8") as output:
            executed = subprocess.run([str(host / "dmhost"), str(bindings)],
                                      cwd=empty_root, env=env, stdout=output,
                                      stderr=subprocess.STDOUT, timeout=timeout)
        report["exit_code"] = executed.returncode
        log = run_log.read_text(encoding="utf8", errors="replace")
        checks, guest_result = runtime_evidence(log, executed.returncode)
        report.update(checks=checks, guest_main_result=guest_result,
                      verified=all(checks.values()))
        report["status"] = "passed" if report["verified"] else "failed"
        if not report["verified"]:
            report["reason"] = "guest_startup_or_newlib_evidence_incomplete"
        return report
    except subprocess.TimeoutExpired as error:
        report.update(reason="process_timeout", timed_out=True,
                      timeout_seconds=error.timeout)
        return report
    except (OSError, ValueError, KeyError) as error:
        report["reason"] = str(error)
        return report
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", type=Path, required=True)
    parser.add_argument("--moltenvk-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True, help="output filename prefix")
    parser.add_argument("--timeout", type=int, choices=(60, 120), default=120)
    args = parser.parse_args(argv)
    report = run_smoke(args.staged, args.moltenvk_root, args.out, args.timeout)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] in {"passed", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
