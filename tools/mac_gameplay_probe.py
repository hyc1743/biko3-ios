#!/usr/bin/env python3
"""Research-only real game observation with restored assets in an isolated tree.

An observed active scene is narrower than playable verification. Reaching the
observation deadline, remaining alive, and renderer startup never prove gameplay.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import signal
import subprocess
import sys
import time
import urllib.request

from ci_guest import PINNED_SHA256
from mac_guest_smoke import PROFILE, digest, smoke_makefile, windowed_frontend
from pack_resources import read_manifest, verify

ROOT = Path(__file__).resolve().parents[1]
RELEASE_TAG = "v0.1.0-dev.3"
RELEASE_ID = 420846443
# Give each title/confirmation screen time to settle before the next confirm.
DEFAULT_KEYS = "2:A,8:A,14:A,20:A,26:A,32:A,38:A,44:A,50:A,56:A,62:A,68:A,74:A,80:A"
ACTIVE = re.compile(r"^\[biko3-probe\] gameplay-active frame=(\d+) group=(\d+) area=(\d+) live_clock=(-?\d+(?:\.\d+)?)$")
FRAME = re.compile(r"^\[biko3-probe\] frame=(\d+) mode=(\d+) bank=(\d+) area=(\d+) clock=(-?\d+(?:\.\d+)?)$")


def validate_keys(value):
    if len(value) > 4095 or any(ord(c) < 32 or ord(c) > 126 for c in value):
        raise ValueError("--keys must be at most 4095 printable ASCII characters")
    return value


def observations(text):
    active, frames, raw = [], [], []
    for line in text.splitlines():
        if line.startswith(("[biko3-probe]", "[biko3-quick-probe]")):
            raw.append(line)
        match = ACTIVE.fullmatch(line)
        if match:
            frame, group, area, clock = match.groups()
            active.append({"frame": int(frame), "group": int(group), "area": int(area), "live_clock": float(clock)})
        match = FRAME.fullmatch(line)
        if match:
            frame, mode, bank, area, clock = match.groups()
            frames.append({"frame": int(frame), "mode": int(mode), "bank": int(bank), "area": int(area), "clock": float(clock)})
    return {"libnx_initialized": "[biko3] original libnx init completed" in text,
            "original_main_entered": "[biko3] entering original main +0x99280" in text,
            "renderer_ready": "Renderer ready:" in text, "probe_frames": frames,
            "active_scene_events": active, "gameplay_active_observed": bool(active),
            "probe_lines": raw, "scripted_input_events": text.count("DM_KEYS:")}


def download_parts(destination, manifest):
    """Fetch only the six named dev.3 assets, verifying local pinned digests."""
    destination = Path(destination)
    destination.mkdir(parents=True, exist_ok=True)
    if len(manifest["parts"]) != 6:
        raise ValueError("Research release must contain the six pinned resource parts")
    records = []
    for part in manifest["parts"]:
        target = destination / part["name"]
        if target.exists() and (target.is_symlink() or not target.is_file()):
            raise ValueError("Resource part target must be a regular file")
        if not target.exists() or target.stat().st_size != part["size"] or digest(target) != part["sha256"]:
            temporary = target.with_name(target.name + ".partial")
            url = "https://github.com/hyc1743/biko3-ios/releases/download/" + RELEASE_TAG + "/" + part["name"]
            request = urllib.request.Request(url, headers={"User-Agent": "Biko3-quicksave-research"})
            hashed, count = hashlib.sha256(), 0
            try:
                with urllib.request.urlopen(request, timeout=180) as source, temporary.open("xb") as output:
                    while chunk := source.read(1024 * 1024):
                        count += len(chunk)
                        if count > part["size"]:
                            raise ValueError("Resource release asset exceeds pinned size")
                        hashed.update(chunk)
                        output.write(chunk)
                if count != part["size"] or hashed.hexdigest() != part["sha256"]:
                    raise ValueError("Resource release asset size/SHA256 mismatch: " + part["name"])
                temporary.replace(target)
            finally:
                temporary.unlink(missing_ok=True)
        records.append({"name": part["name"], "size": part["size"], "sha256": part["sha256"]})
        print("Verified resource part " + part["name"], flush=True)
    return {"release_id": RELEASE_ID, "tag": RELEASE_TAG, "parts": records}


def screenshot(path, env, timeout=5):
    try:
        result = subprocess.run(["/usr/sbin/screencapture", "-x", str(path)], env=env,
                                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=timeout)
        return result.returncode == 0 and path.is_file()
    except (OSError, subprocess.SubprocessError):
        return False


def stop_process(process):
    if process.poll() is not None:
        return
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait(timeout=10)
        return
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:
        os.killpg(process.pid, signal.SIGKILL)
        process.wait(timeout=10)


def probe(args):
    out = args.out.resolve()
    if out.exists():
        raise ValueError("Research output must be fresh")
    evidence, private = out / "evidence", out / "private"
    evidence.mkdir(parents=True)
    report = {"schema": 1, "status": "failed", "platform": platform.system(), "architecture": platform.machine(),
              "runtime_verified": False, "gameplay_verified": False, "ios_device_execution": False,
              "gameplay_active_observed": False, "observation_seconds": args.seconds,
              "scope": "Real arm64 macOS original game with verified Data and scripted input; research only"}
    started = time.monotonic()
    try:
        validate_keys(args.keys)
        if sys.platform != "darwin" or platform.machine().lower() != "arm64":
            report.update(status="skipped", reason="requires_native_arm64_macos")
            return report
        staged, mvk = args.staged.resolve(), args.moltenvk_root.resolve()
        if out.is_relative_to(staged) or staged.is_relative_to(out):
            raise ValueError("Research output must be separate from the supplied staging tree")
        stage = json.loads((staged / "stage-report.json").read_text(encoding="utf-8"))
        bindings = staged / "rewritten-bindings"
        if stage.get("guest_sha256") != PINNED_SHA256 or digest(bindings / "main.patched.bin") != stage.get("patched_sha256"):
            raise ValueError("Staged guest is not the verified original and hook image")
        source = staged / "work/nxnative-staged"
        if "biko3_game_probe_tick" not in (source / "dm/games" / PROFILE / "Biko3HookTable.h").read_text():
            raise ValueError("Stage must be generated with --quicksave-probe")
        manifest = read_manifest(ROOT / "ios/resources/Biko3Data.manifest.json")
        if args.download_parts:
            report["download"] = download_parts(args.parts.resolve(), manifest)
        private.mkdir()
        game = private / "game"
        report["resource_restore"] = verify(args.parts.resolve(), manifest, restore=game)
        copied = private / "source"
        shutil.copytree(source, copied, ignore=shutil.ignore_patterns(".git", "__pycache__", "dmhost", "*.o"))
        copied_bindings = private / "bindings"
        shutil.copytree(bindings, copied_bindings)
        host = copied / "dm/host"
        front = host / "front_mac.m"
        front.write_text(windowed_frontend(front.read_text(encoding="utf-8")), encoding="utf-8")
        makefile = host / "Makefile.biko3-research"
        makefile.write_text(smoke_makefile((host / "Makefile").read_text(encoding="utf-8"), mvk), encoding="utf-8")
        env = {key: value for key, value in os.environ.items()
               if not key.startswith(("DM_", "BIKO3_", "GH_", "GITHUB_")) and
               not any(word in key.upper() for word in ("TOKEN", "SECRET", "PASSWORD"))}
        env["GAME"] = PROFILE
        with (evidence / "build.log").open("w", encoding="utf-8") as log:
            built = subprocess.run(["make", "-f", makefile.name, "-j", "3", "GAME=" + PROFILE, "dmhost"],
                                   cwd=host, env=env, stdout=log, stderr=subprocess.STDOUT, timeout=1200)
        report["build_exit_code"] = built.returncode
        if built.returncode:
            raise ValueError("Research host compilation failed; see build.log")
        saves, captures = private / "saves", private / "captures"
        saves.mkdir(); captures.mkdir()
        env.update(DM_WINDOW="1", BIKO3_MAC_SMOKE_EXIT="1", BIKO3_PROBE_TRACE="1", DM_KEYS=args.keys,
                   BIKO3_GAME_ROOT=str(game), BIKO3_SAVE_ROOT=str(saves), BIKO3_CAPTURE_ROOT=str(captures),
                   DM_ROMFS=str(game), DM_SAVE=str(saves), DM_TEMP=str(private), DM_AUDIO="1", DM_SVC="1")
        report.update(compiled=True, guest_sha256=stage["guest_sha256"], patched_sha256=stage["patched_sha256"],
                      host_sha256=digest(host / "dmhost"), keys=args.keys)
        run_log = evidence / "run.log"
        shots, process, deadline_reached = [], None, False
        with run_log.open("w", encoding="utf-8") as log:
            try:
                process = subprocess.Popen([str(host / "dmhost"), str(copied_bindings)], cwd=private,
                                           env=env, stdout=log, stderr=subprocess.STDOUT, start_new_session=True)
                running_since, next_shot = time.monotonic(), 20
                while process.poll() is None:
                    elapsed = time.monotonic() - running_since
                    if elapsed >= args.seconds:
                        deadline_reached = True
                        break
                    if elapsed >= next_shot:
                        path = evidence / f"screen-{next_shot:03d}.png"
                        if screenshot(path, env, min(5, args.seconds - elapsed)): shots.append(path.name)
                        next_shot += 20
                    time.sleep(0.25)
            finally:
                if process is not None: stop_process(process)
        report.update(exit_code=process.returncode, observation_window_elapsed=deadline_reached,
                      controlled_termination=deadline_reached, screenshots=shots,
                      observed_run_seconds=round(time.monotonic() - running_since, 3),
                      **observations(run_log.read_text(encoding="utf-8", errors="replace")))
        if not deadline_reached and process.returncode:
            log_text = run_log.read_text(encoding="utf-8", errors="replace")
            if "failed to create the MTLResidencySet" in log_text:
                report.update(status="inconclusive", reason="known_MoltenVK_residency_set_shutdown_failure",
                              gameplay_active_observed=False)
                return report
            report.update(status="failed", reason="game_process_exited_abnormally")
        elif report["gameplay_active_observed"] and report["renderer_ready"]:
            report.update(status="active_scene_observed", reason="mode2_live_scene_observed_not_full_gameplay_verification")
        else:
            report.update(status="inconclusive", reason="no_strict_live_scene_evidence")
        return report
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as error:
        report.update(status="failed", reason=str(error))
        return report
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        (evidence / "report.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--staged", type=Path, required=True)
    parser.add_argument("--moltenvk-root", type=Path, required=True)
    parser.add_argument("--parts", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--keys", default=DEFAULT_KEYS)
    parser.add_argument("--seconds", type=int, choices=(60, 90), default=90)
    parser.add_argument("--download-parts", action="store_true", help="Fetch fixed v0.1.0-dev.3 parts and verify pinned size/SHA256")
    result = probe(parser.parse_args(argv))
    print(json.dumps(result, indent=2))
    return 0 if result["status"] in {"active_scene_observed", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
