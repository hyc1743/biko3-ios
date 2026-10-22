"""Offline, address-preserving NRO -> uncompressed NSO analysis carrier.

This does not implement the libnx startup contract or produce a runnable port.
Only the two reviewed upstream rewriter source hashes below may be executed.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
import subprocess
import sys
import tempfile

from nro import Nro, NroError


REVIEWED_SOURCES = {
    "dm/tools/patch_nso.py": "566fb4e365c4e11a554d72f15910a8e9bde8055c0a8e94ce229c106d9b3e7d32",
    "dm/tools/nso_code.py": "0b9d0317438b2cf729030ccc5fa37fbc8abb9fad72d1d8babe448faebdc1d0bb",
}
LIMITATIONS = [
    "An NSO0 carrier is not a runnable iOS executable or an IPA.",
    "Original NRO header and entry branch are retained; libnx startup, argv, heap, TLS and service contracts need adaptation.",
    "No relocation is applied; MOD0, dynamic tables, RELR and in-place addends remain at their original virtual addresses.",
    "The original NRO footer is excluded; assets must be supplied separately.",
    "Graphics, audio, input, filesystem and save integration still require platform hooks.",
]


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def validated_nro(data: bytes) -> Nro:
    image = Nro(data)
    if image.flags:
        raise NroError("unsupported NRO flags")
    if image.mod0 is None or image.dynamic_offset is None:
        raise NroError("nxnative preparation requires MOD0 and a dynamic table")
    # Trigger structural validation instead of merely copying unchecked tables.
    image.relr()
    image.dynamic_summary()
    for key in ("bss_start", "bss_end", "eh_frame_start", "eh_frame_end", "module_object"):
        address = image.mod0[key]
        if address is not None and not 0 <= address <= image.memory_size:
            raise NroError(f"MOD0 {key} is outside the image")
    for start, end in (("bss_start", "bss_end"), ("eh_frame_start", "eh_frame_end")):
        lo, hi = image.mod0[start], image.mod0[end]
        if (lo is None) != (hi is None) or (lo is not None and lo > hi):
            raise NroError(f"invalid MOD0 {start}/{end} range")
    return image


def to_nso(image: Nro) -> bytes:
    """Keep memory offsets; file offsets point beyond a new 0x100-byte header."""
    output = bytearray(0x100)
    output[:4] = b"NSO0"
    output[0x40:0x60] = bytes.fromhex(image.build_id)
    for i, seg in enumerate(image.segments):
        file_offset = len(output)
        raw = image.data[seg.offset:seg.end]
        struct.pack_into("<III", output, 0x10 + i * 0x10, file_offset, seg.offset, seg.size)
        struct.pack_into("<I", output, 0x60 + i * 4, seg.size)
        output[0xA0 + i * 0x20:0xC0 + i * 0x20] = hashlib.sha256(raw).digest()
        output.extend(raw)
    struct.pack_into("<I", output, 0x3C, image.bss_size)
    result = bytes(output)
    verify_nso(result, image)
    return result


def verify_nso(data: bytes, image: Nro) -> None:
    if len(data) < 0x100 or data[:4] != b"NSO0" or struct.unpack_from("<I", data, 0xC)[0] != 0:
        raise NroError("invalid or compressed NSO carrier")
    if data[0x40:0x60].hex() != image.build_id or struct.unpack_from("<I", data, 0x3C)[0] != image.bss_size:
        raise NroError("NSO build ID or BSS mismatch")
    cursor = 0x100
    for i, original in enumerate(image.segments):
        fo, mo, size = struct.unpack_from("<III", data, 0x10 + i * 0x10)
        stored = struct.unpack_from("<I", data, 0x60 + i * 4)[0]
        if (fo, mo, size, stored) != (cursor, original.offset, original.size, original.size):
            raise NroError("NSO segment layout mismatch")
        raw = data[fo:fo + size]
        if raw != image.data[mo:mo + size] or hashlib.sha256(raw).digest() != data[0xA0 + i * 0x20:0xC0 + i * 0x20]:
            raise NroError("NSO segment bytes or hash mismatch")
        cursor += size
    if len(data) != cursor:
        raise NroError("NSO contains unexpected trailing bytes")


def compatibility(image: Nro) -> dict:
    """Check known assumptions in the reviewed patch_nso and loader sources."""
    blockers = []
    lo, hi = image.mod0["eh_frame_start"], image.mod0["eh_frame_end"]
    eh = {"start": lo, "end": hi}
    if lo is None or hi is None or hi - lo < 12:
        blockers.append("patch_nso.py:655-676 unconditionally parses eh_frame_hdr; MOD0 has no usable header range")
    else:
        header = image.read_memory(lo, 4)
        eh["header_hex"] = header.hex()
        if header[0] != 1 or any((enc & 15) not in (3, 11, 4) for enc in header[1:]):
            blockers.append("patch_nso.py:659-670 does not support this eh_frame_hdr version/encoding")
    sym, string = image.tag(6), image.tag(5)
    count = image.dynamic_summary()["symbol_count_including_null"]
    inferred = None if sym is None or string is None else (string - sym) // 24
    loader_ok = (sym is not None and string is not None and string >= sym
                 and (string - sym) % 24 == 0 and count is not None and inferred == count)
    return {
        "rewrite_blockers": blockers,
        "eh_frame_hdr": eh,
        "loader_symbol_count": {"sysv_hash_count": count, "upstream_inferred_count": inferred,
                                "matches": loader_ok},
        "runtime_warnings": ([] if loader_ok else ["loader.c parse_dynamic infers symbol count from strtab-symtab; adaptation required"])
        + ["The linear rewriter scans the retained NRO header as text; header bytes must be checked after rewriting.",
           "Successful rewriting does not validate libnx entry/startup or any runtime service."],
    }


def recover_eh_frame(image: Nro) -> dict:
    """Recover bounded FDE ranges from this NRO's empty-header/.eh_frame layout.

    Parse structural CIE augmentation only; no unwind program is evaluated and
    no function is inferred from instruction patterns.
    """
    marker = image.mod0["eh_frame_start"]
    if marker is None or marker != image.mod0["eh_frame_end"]:
        raise NroError("FDE recovery requires the empty eh_frame_hdr profile")
    if image.read_memory(marker, 4) != bytes(4):
        raise NroError("expected zero word before .eh_frame CIE")
    start = marker + 4
    segment = next((s for s in image.segments if s.offset <= start < s.end), None)
    if segment is None:
        raise NroError(".eh_frame is outside file-backed segments")
    raw, limit = image.data, segment.end
    cies, functions = {}, []

    def leb(pos, end, signed=False):
        value = shift = 0
        while pos < end and shift < 70:
            byte = raw[pos]; pos += 1
            value |= (byte & 127) << shift
            shift += 7
            if not byte & 128:
                if signed and byte & 64:
                    value -= 1 << shift
                return value, pos
        raise NroError("truncated or oversized DWARF LEB128")

    def pointer(pos, enc, end, relative=True):
        formats = {0: ("<Q", 8), 3: ("<I", 4), 4: ("<Q", 8), 11: ("<i", 4), 12: ("<q", 8)}
        if enc & 15 not in formats or (relative and enc & 0xF0 not in (0, 0x10)):
            raise NroError(f"unsupported FDE pointer encoding {enc:#x}")
        fmt, size = formats[enc & 15]
        if pos + size > end:
            raise NroError("truncated DWARF encoded pointer")
        value = struct.unpack_from(fmt, raw, pos)[0]
        if relative and enc & 0x70 == 0x10:
            value += pos
        return value, pos + size

    pos = start
    while pos + 4 <= limit:
        length = struct.unpack_from("<I", raw, pos)[0]
        if length == 0:
            end = pos + 4
            break
        if length == 0xFFFFFFFF or length < 4 or pos + 4 + length > limit:
            raise NroError(f"invalid/unsupported .eh_frame record length at {pos:#x}")
        record_end = pos + 4 + length
        cie_delta = struct.unpack_from("<I", raw, pos + 4)[0]
        cursor = pos + 8
        if cie_delta == 0:
            if cursor >= record_end:
                raise NroError("truncated CIE")
            version = raw[cursor]; cursor += 1
            nul = raw.find(b"\0", cursor, record_end)
            if nul < 0 or version not in (1, 3):
                raise NroError("unsupported CIE version or augmentation string")
            augmentation = raw[cursor:nul]
            if not augmentation.startswith(b"z") or any(c not in b"PLR" for c in augmentation[1:]):
                raise NroError(f"unsupported CIE augmentation {augmentation!r}")
            cursor = nul + 1
            _, cursor = leb(cursor, record_end)
            _, cursor = leb(cursor, record_end, signed=True)
            if version == 1:
                if cursor >= record_end:
                    raise NroError("truncated CIE return register")
                cursor += 1
            else:
                _, cursor = leb(cursor, record_end)
            auglen, cursor = leb(cursor, record_end)
            augend = cursor + auglen
            if augend > record_end:
                raise NroError("CIE augmentation exceeds record")
            encoding = 0
            for letter in augmentation[1:]:
                if cursor >= augend:
                    raise NroError("truncated CIE augmentation")
                enc = raw[cursor]; cursor += 1
                if letter == ord("R"):
                    encoding = enc
                elif letter == ord("P"):
                    # Personality pointers may be indirect: consume, never dereference.
                    _, cursor = pointer(cursor, enc & 15, augend, relative=False)
            if cursor != augend:
                raise NroError("unexpected CIE augmentation payload")
            cies[pos] = encoding
        else:
            cie = pos + 4 - cie_delta
            if cie not in cies:
                raise NroError(f"FDE references unknown CIE at {cie:#x}")
            pc, cursor = pointer(cursor, cies[cie], record_end)
            size, cursor = pointer(cursor, cies[cie] & 15, record_end, relative=False)
            auglen, cursor = leb(cursor, record_end)
            if cursor + auglen > record_end:
                raise NroError("FDE augmentation exceeds record")
            if size <= 0 or pc < 0x80 or pc + size > image.segments[0].end or pc % 4:
                raise NroError(f"FDE has invalid text range {pc:#x}+{size:#x}")
            functions.append({"start": pc, "size": size, "fde_offset": pos, "cie_offset": cie})
        pos = record_end
    else:
        raise NroError("unterminated .eh_frame section")
    if not cies or not functions:
        raise NroError("no validated CIE/FDE records recovered")
    return {"schema": 1, "source_nro_sha256": image.sha256,
            "eh_frame_start": start, "eh_frame_end": end,
            "eh_frame_sha256": digest(raw[start:end]), "cie_count": len(cies),
            "function_count": len(functions),
            "functions": sorted(functions, key=lambda f: (f["start"], f["size"])),
            "limitation": "FDE-described ranges only; not a complete function inventory or unwind implementation."}


def source_record(root: Path) -> dict:
    paths = {name: (root / name).read_bytes() for name in REVIEWED_SOURCES}
    record = {"root": str(root.resolve()), "sha256": {name: digest(raw) for name, raw in paths.items()}}
    record["matches_reviewed_sources"] = record["sha256"] == REVIEWED_SOURCES
    result = subprocess.run(["git", "-C", str(root), "rev-parse", "--show-toplevel"], capture_output=True, text=True, check=False)
    if result.returncode == 0 and Path(result.stdout.strip()).resolve() == root.resolve():
        record["git_commit"] = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    return record


def rewrite(root: Path, carrier: Path, destination: Path, name: str, image: Nro, source: dict) -> dict:
    if not source["matches_reviewed_sources"]:
        raise NroError("upstream source hashes differ from the reviewed, pinned source pair")
    blockers = compatibility(image)["rewrite_blockers"]
    if blockers:
        return {"status": "blocked", "reason": blockers, "executed": False}
    # Isolate output and disable bytecode writes in the upstream source directory.
    with tempfile.TemporaryDirectory(prefix="nxnative-", dir=destination) as staging:
        stage = Path(staging)
        run = subprocess.run([sys.executable, "-I", "-B", str(root / "dm/tools/patch_nso.py"),
                              str(carrier), str(stage), name], cwd=stage, capture_output=True,
                             text=True, errors="replace", timeout=600, check=False)
        report = {"status": "failed", "executed": True, "returncode": run.returncode,
                  "stdout": run.stdout, "stderr": run.stderr}
        if run.returncode:
            return report
        expected = [f"{name}.{ext}" for ext in ("patched.bin", "fixups.tsv", "gates.tsv", "sites.tsv", "cuts.tsv", "obs.tsv")]
        expected.append("build_src.sha256")
        for filename in expected:
            if not (stage / filename).is_file():
                raise NroError(f"rewriter did not produce {filename}")
        layout = dict(line[2:].split() for line in (stage / f"{name}.fixups.tsv").read_text().splitlines())
        for key, value in (("TEXT_END", image.segments[0].end), ("RO_OFF", image.segments[1].offset),
                           ("DATA_OFF", image.segments[2].offset)):
            if int(layout[key], 16) != value:
                raise NroError(f"rewriter changed {key}")
        patched = (stage / f"{name}.patched.bin").read_bytes()
        if patched[:0x80] != image.data[:0x80] or layout["BUILD_ID"] != image.build_id[:32]:
            raise NroError("rewriter changed the NRO entry/header or build ID")
        for seg in image.segments[1:]:
            if patched[seg.offset:seg.end] != image.data[seg.offset:seg.end]:
                raise NroError("rewriter changed original rodata/data")
        target = destination / "rewritten"
        target.mkdir(exist_ok=False)
        report["artifacts"] = {}
        for filename in expected:
            raw = (stage / filename).read_bytes()
            (target / filename).write_bytes(raw)
            report["artifacts"][filename] = digest(raw)
        report["status"] = "rewritten_offline_only"
        return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("nro", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--name", default="main")
    parser.add_argument("--nxnative-root", type=Path, help="already downloaded local source; never downloaded by this tool")
    parser.add_argument("--rewrite", action="store_true", help="execute only reviewed hashes, after compatibility checks")
    args = parser.parse_args(argv)
    if not re.fullmatch(r"[a-z][a-z0-9_]{0,7}", args.name):
        parser.error("--name must be 1-8 lowercase letters/digits/underscores, starting with a letter")
    if args.rewrite and args.nxnative_root is None:
        parser.error("--rewrite requires --nxnative-root")
    image = validated_nro(args.nro.read_bytes())
    nso = to_nso(image)
    out = args.out.resolve()
    if args.nxnative_root and out.is_relative_to(args.nxnative_root.resolve()):
        raise NroError("output must be outside the upstream source directory")
    out.mkdir(parents=True, exist_ok=True)
    carrier = out / f"{args.name}.nso"
    manifest_path = out / f"{args.name}.preparation.json"
    if carrier.exists() or manifest_path.exists():
        raise NroError("output files already exist; choose a fresh --out directory")
    carrier.write_bytes(nso)
    manifest = {"schema": 1, "status": "converted_offline_only", "source_nro": {
        "path": str(args.nro.resolve()), "sha256": image.sha256, "file_size": len(image.data),
        "image_size": image.image_size, "excluded_footer_size": len(image.data) - image.image_size,
        "entry_offset": image.entry, "build_id": image.build_id, "bss_size": image.bss_size,
        "memory_size": image.memory_size, "mod0": image.mod0, "relr_target_count": len(image.relr())},
        "carrier": {"path": str(carrier), "sha256": digest(nso), "file_size": len(nso),
                    "segments": [{"name": s.name, "virtual_offset": s.offset, "size": s.size,
                                  "sha256": digest(image.data[s.offset:s.end])} for s in image.segments]},
        "compatibility": compatibility(image), "limitations": LIMITATIONS}
    try:
        boundaries = recover_eh_frame(image)
        boundary_path = out / f"{args.name}.function-boundaries.json"
        boundary_path.write_text(json.dumps(boundaries, indent=2) + "\n", encoding="utf-8")
        manifest["function_boundaries"] = {"path": str(boundary_path), "sha256": digest(boundary_path.read_bytes()),
                                            "function_count": boundaries["function_count"],
                                            "cie_count": boundaries["cie_count"]}
    except NroError as error:
        manifest["function_boundaries"] = {"status": "unavailable", "reason": str(error)}
    exit_code = 0
    try:
        if args.nxnative_root:
            root = args.nxnative_root.resolve()
            manifest["nxnative_source"] = source_record(root)
            if args.rewrite:
                manifest["rewrite"] = rewrite(root, carrier, out, args.name, image, manifest["nxnative_source"])
                if manifest["rewrite"]["status"] != "rewritten_offline_only":
                    exit_code = 2
    except (OSError, NroError, subprocess.SubprocessError, KeyError, ValueError) as error:
        manifest["rewrite"] = {"status": "failed", "error": str(error)}
        exit_code = 2
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"carrier": str(carrier), "manifest": str(manifest_path),
                      "rewrite": manifest.get("rewrite", {"status": "not_requested"})}))
    return exit_code


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except (OSError, NroError) as error:
        print(f"error: {error}", file=sys.stderr)
        raise SystemExit(1)
