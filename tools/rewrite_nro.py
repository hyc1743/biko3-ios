"""Execute the complete reviewed nxnative rewriter through a bounded NRO overlay.

Produces offline artifacts only. No guest code, build scripts, or device actions run.
"""
from __future__ import annotations

import argparse
from collections import Counter
import difflib
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys

from nro import NroError
from prepare_nxnative import REVIEWED_SOURCES, recover_eh_frame, validated_nro, verify_nso

COMMIT = "677345b0933f5f8754aeec3125d14df32683ca97"
SOURCES = dict(REVIEWED_SOURCES, **{
    "dm/ios/tools/gen_embed.py": "0660a4eafacca6a9437f36830cea580fcb74e8e0639fd5515e70e243149ef81b"})
START = "# ---- Function boundaries (eh_frame_hdr) ----"
END = "# ---- Linear scan"
SCAN = "DATA_BLOCKS = nso_code.data_blocks(IMG, TEXT_END, md)"


def sha(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def reviewed_sources(root: Path) -> dict:
    snapshot = json.loads((root / "snapshot.json").read_text(encoding="utf8"))
    if snapshot["commit"] != COMMIT or snapshot["repository"] != "seedds/nxnative":
        raise NroError("unexpected nxnative snapshot identity")
    blobs = {x["path"]: x["sha"] for x in json.loads((root / "tree.json").read_text())["tree"] if x["type"] == "blob"}
    records = {}
    for name, expected in SOURCES.items():
        raw = (root / name).read_bytes()
        git = hashlib.sha1(b"blob " + str(len(raw)).encode() + b"\0" + raw).hexdigest()
        if sha(raw) != expected or git != blobs.get(name):
            raise NroError(f"reviewed source/Git blob mismatch: {name}")
        records[name] = {"sha256": expected, "git_blob_sha": git}
    return records


def make_overlay(source: str, boundaries: dict, carrier_sha: str) -> str:
    if source.count(START) != 1 or source.count(END) != 1 or source.count(SCAN) != 1:
        raise NroError("unexpected reviewed source anchors")
    starts = sorted({f["start"] for f in boundaries["functions"]})
    block = START + "\n" + (
        "# Biko3 overlay: FDE ranges are independently recovered and hash-bound.\n"
        "import hashlib, json\n"
        f"assert hashlib.sha256(d).hexdigest() == {carrier_sha!r}, 'carrier hash mismatch'\n"
        "with open(os.path.join(os.path.dirname(__file__), 'function-boundaries.json'), encoding='utf8') as _f:\n"
        "    _fde = json.load(_f)\n"
        f"assert _fde['source_nro_sha256'] == {boundaries['source_nro_sha256']!r}, 'NRO hash mismatch'\n"
        f"assert hashlib.sha256(json.dumps(_fde, sort_keys=True).encode()).hexdigest() == {sha(json.dumps(boundaries, sort_keys=True).encode())!r}, 'FDE manifest mismatch'\n"
        "starts = sorted({f['start'] for f in _fde['functions']})\n"
        "assert starts and all(0x80 <= a < TEXT_END and a % 4 == 0 for a in starts)\n\n")
    if not starts:
        raise NroError("no recovered FDE function starts")
    result = source[:source.index(START)] + block + source[source.index(END):]
    # Union the header with any upstream-detected data intervals without changing offsets.
    replacement = SCAN + "\n" + (
        "_merged = []\n"
        "for _lo, _hi in sorted(DATA_BLOCKS + [(0, 0x80)]):\n"
        "    if _merged and _lo <= _merged[-1][1]: _merged[-1] = (_merged[-1][0], max(_merged[-1][1], _hi))\n"
        "    else: _merged.append((_lo, _hi))\n"
        "DATA_BLOCKS = _merged")
    return result.replace(SCAN, replacement)


def verify_outputs(out: Path, name: str, image) -> dict:
    patched = (out / f"{name}.patched.bin").read_bytes()
    layout = dict(line[2:].split() for line in (out / f"{name}.fixups.tsv").read_text().splitlines())
    for key, value in (("TEXT_END", image.segments[0].end), ("RO_OFF", image.segments[1].offset), ("DATA_OFF", image.segments[2].offset)):
        if int(layout[key], 16) != value:
            raise NroError(f"rewriter changed {key}")
    if patched[:0x80] != image.data[:0x80] or layout["BUILD_ID"] != image.build_id[:32]:
        raise NroError("rewriter changed header/build identity")
    for seg in image.segments[1:]:
        if patched[seg.offset:seg.end] != image.data[seg.offset:seg.end]:
            raise NroError("rewriter changed original rodata/data")
    for relocation in image.relr():
        address = relocation["target"]
        if address < (image.segments[0].end + 0x3FFF) & ~0x3FFF:
            raise NroError("RELR target points into signed executable pages")
        if address < image.image_size and patched[address:address + 8] != image.data[address:address + 8]:
            raise NroError("rewriter altered original RELR addend")
    sites = Counter()
    for line in (out / f"{name}.sites.tsv").read_text().splitlines():
        address, kind, *_ = line.split("\t")
        if int(address, 16) < 0x80:
            raise NroError("NRO header was scanned as instructions")
        sites[kind] += 1
    gates = Counter(line.split("\t")[1] for line in (out / f"{name}.gates.tsv").read_text().splitlines())
    return {"layout": layout, "site_counts": dict(sites), "gate_counts": dict(gates),
            "preserved_relr_targets": len(image.relr()), "header_and_data_preserved": True}


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("nro", type=Path)
    parser.add_argument("--prepared", type=Path, required=True)
    parser.add_argument("--nxnative-root", type=Path, required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--name", default="main")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,7}", args.name):
        parser.error("invalid module name")
    root, out, prepared = args.nxnative_root.resolve(), args.out.resolve(), args.prepared.resolve()
    if out.is_relative_to(root):
        raise NroError("output must be outside upstream source")
    records = reviewed_sources(root)
    image = validated_nro(args.nro.read_bytes())
    manifest = json.loads((prepared / f"{args.name}.preparation.json").read_text(encoding="utf8"))
    carrier = prepared / f"{args.name}.nso"
    raw = carrier.read_bytes()
    verify_nso(raw, image)
    if manifest["source_nro"]["sha256"] != image.sha256 or manifest["carrier"]["sha256"] != sha(raw):
        raise NroError("preparation manifest/input identity mismatch")
    fde = json.loads((prepared / f"{args.name}.function-boundaries.json").read_text(encoding="utf8"))
    if fde != recover_eh_frame(image):
        raise NroError("FDE manifest does not match independently recovered ranges")
    out.mkdir(parents=True, exist_ok=False)
    overlay = out / "overlay"
    overlay.mkdir()
    source = (root / "dm/tools/patch_nso.py").read_text(encoding="utf8")
    adapted = make_overlay(source, fde, sha(raw))
    (overlay / "patch_nso.py").write_text(adapted, encoding="utf8")
    (overlay / "nso_code.py").write_bytes((root / "dm/tools/nso_code.py").read_bytes())
    (overlay / "function-boundaries.json").write_text(json.dumps(fde, indent=2), encoding="utf8")
    (overlay / "gen_embed.py").write_bytes((root / "dm/ios/tools/gen_embed.py").read_bytes())
    (out / "overlay.diff").write_text("".join(difflib.unified_diff(source.splitlines(True), adapted.splitlines(True), fromfile="upstream/patch_nso.py", tofile="overlay/patch_nso.py")), encoding="utf8")
    result = {"schema": 1, "status": "running", "source_nro_sha256": image.sha256,
              "carrier_sha256": sha(raw), "upstream_commit": COMMIT, "reviewed_sources": records,
              "overlay_sha256": sha((overlay / "patch_nso.py").read_bytes()),
              "function_count": fde["function_count"], "execution": "Complete external patch_nso.py with two bounded source edits; no guest execution",
              "limitation": "Offline rewrite only; libnx startup/self-relocation and host hooks remain unimplemented. Assembly generation was verified, not compilation; regenerate its absolute incbin paths on the macOS build host."}
    provenance = out / "provenance.json"
    def save():
        provenance.write_text(json.dumps(result, indent=2), encoding="utf8")
    save()
    env = {k: v for k, v in os.environ.items() if not k.startswith("DM_")}
    env["PYTHONIOENCODING"] = "utf-8"
    rewritten = out / "rewritten"
    command = [sys.executable, "-I", "-B", str(overlay / "patch_nso.py"), str(carrier), str(rewritten), args.name]
    result["command"] = command
    print("Executing complete reviewed external rewriter through the recorded overlay.", flush=True)
    try:
        with (out / "rewrite.stdout.txt").open("w", encoding="utf8") as stdout, (out / "rewrite.stderr.txt").open("w", encoding="utf8") as stderr:
            run = subprocess.run(command, cwd=out, env=env, stdout=stdout, stderr=stderr, timeout=900, check=False)
        result["returncode"] = run.returncode
        if run.returncode:
            result["status"] = "rewrite_failed"
            save()
            return 2
        result["validation"] = verify_outputs(rewritten, args.name, image)
        assembly = out / "guest_embed.S"
        embed = subprocess.run([sys.executable, "-I", "-B", str(overlay / "gen_embed.py"), str(rewritten), str(assembly), args.name], cwd=out, env=env, capture_output=True, text=True, check=False, timeout=60)
        (out / "embed.stdout.txt").write_text(embed.stdout, encoding="utf8")
        (out / "embed.stderr.txt").write_text(embed.stderr, encoding="utf8")
        if embed.returncode or not assembly.is_file():
            raise NroError("reviewed gen_embed.py failed")
        result["status"] = "rewritten_and_embedding_generated_offline_only"
        result["artifacts"] = {p.relative_to(out).as_posix(): sha(p.read_bytes()) for p in sorted(rewritten.iterdir()) if p.is_file()}
        result["artifacts"]["guest_embed.S"] = sha(assembly.read_bytes())
    except Exception as error:
        result["status"] = "validation_or_execution_failed"
        result["error"] = f"{type(error).__name__}: {error}"
        save()
        raise
    save()
    print(json.dumps({"status": result["status"], "validation": result["validation"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
