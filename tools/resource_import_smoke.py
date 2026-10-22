#!/usr/bin/env python3
"""Compile and execute the Foundation resource importer against synthetic bytes."""
from __future__ import annotations
import argparse
import json
from pathlib import Path
import re
import subprocess
import sys
import time

from pack_resources import pack, MANIFEST

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "cross_part_reassembly_and_hashes", "receipt_without_parts",
    "same_size_corruption_and_completed_file_retry", "existing_manual_data_without_parts",
    "missing_part_is_retryable", "part_sha256_failure", "manifest_traversal_rejected",
    "symlink_destination_rejected", "symlink_part_rejected",
    "interrupted_partial_is_reused_safely", "file_sha_failure_keeps_atomic_output",
}


def smoke(prefix):
    prefix = prefix.resolve()
    prefix.parent.mkdir(parents=True, exist_ok=True)
    report_path = Path(str(prefix) + ".json")
    build_log = Path(str(prefix) + ".build.log")
    run_log = Path(str(prefix) + ".run.log")
    report = {"schema": 1, "status": "failed", "verified": False,
              "scope": "native Foundation resource import using synthetic cross-part fixtures",
              "original_game_data_used": False, "game_executed": False,
              "build_log": build_log.name, "run_log": run_log.name}
    started = time.monotonic()
    try:
        if sys.platform != "darwin":
            report.update(status="skipped", reason="native_foundation_requires_macos")
            return report
        work = Path(str(prefix) + ".work")
        work.mkdir(exist_ok=False)
        data = work / "Data"
        (data / "nested").mkdir(parents=True)
        (data / "bk3_00.pp").write_bytes(b"synthetic cross-volume sample\x00\xff")
        (data / "nested/other.bin").write_bytes(b"another independent fixture payload\x01\x02")
        (data / "zero.bin").write_bytes(b"")
        parts = work / "parts"
        manifest = pack(data, parts, part_bytes=9)
        report.update(fixture_files=manifest["file_count"], fixture_parts=len(manifest["parts"]),
                      fixture_bytes=manifest["total_bytes"])
        executable = work / "resource-import-test"
        command = ["xcrun", "--sdk", "macosx", "clang", "-O1", "-g", "-fobjc-arc", "-fblocks",
                   "-fsanitize=address,undefined", "-fno-omit-frame-pointer",
                   "-Werror=implicit-function-declaration", "-I", str(ROOT / "ios/import"),
                   str(ROOT / "ios/import/Biko3ResourceInstaller.m"),
                   str(ROOT / "tests/native/resource_import.m"), "-framework", "Foundation",
                   "-o", str(executable)]
        with build_log.open("w", encoding="utf8") as output:
            compiled = subprocess.run(command, stdout=output, stderr=subprocess.STDOUT, timeout=180)
        report["build_exit_code"] = compiled.returncode
        if compiled.returncode:
            report["reason"] = "foundation_importer_compile_failed"
            return report
        with run_log.open("w", encoding="utf8") as output:
            executed = subprocess.run([str(executable), str(parts / MANIFEST), str(parts),
                                       str(data), str(work / "cases")], stdout=output,
                                      stderr=subprocess.STDOUT, timeout=120)
        report["exit_code"] = executed.returncode
        log = run_log.read_text(encoding="utf8", errors="replace")
        passed = set(re.findall(r"^RESOURCE_IMPORT_PASS (\S+)$", log, flags=re.MULTILINE))
        report.update(checks={name: name in passed for name in sorted(EXPECTED)},
                      verified=executed.returncode == 0 and EXPECTED.issubset(passed)
                               and "RESOURCE_IMPORT_ALL_PASSED" in log)
        report["status"] = "passed" if report["verified"] else "failed"
        if not report["verified"]:
            report["reason"] = "native_resource_import_test_failed"
        return report
    except subprocess.TimeoutExpired as error:
        report.update(reason="native_resource_import_timeout", timeout_seconds=error.timeout)
        return report
    except (OSError, ValueError) as error:
        report["reason"] = str(error)
        return report
    finally:
        report["elapsed_seconds"] = round(time.monotonic() - started, 3)
        report_path.write_text(json.dumps(report, indent=2) + "\n", encoding="utf8")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True, help="report/log filename prefix")
    args = parser.parse_args(argv)
    report = smoke(args.out)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] in {"passed", "skipped"} else 1


if __name__ == "__main__":
    raise SystemExit(main())
