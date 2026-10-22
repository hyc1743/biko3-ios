#!/usr/bin/env python3
"""Decode Biko3's protected .tbl text/index streams for localization work."""
from __future__ import annotations

from pathlib import Path
import struct
import json

KEY = b"\x2f\xca\xd8\x35"
MAGIC_XOR = 0xA67F54CB

def decode_tbl(blob: bytes) -> bytes:
    if len(blob) < 5:
        raise ValueError("tbl stream is too short")
    target = struct.unpack_from("<I", blob)[0] ^ MAGIC_XOR
    if not (0x900 <= target < 0x1000000 and (target - 0x900) % 0x50 == 0):
        raise ValueError("invalid tbl decoded size")
    src = bytearray(blob[4:])
    for i in range(len(src)):
        src[i] ^= KEY[i & 3]
    ring = bytearray(0x1000)
    out = bytearray()
    ring_pos = 0xFEE
    pos = 0
    flags = 0
    bits = 0
    while pos < len(src) and len(out) < target - 0x100:
        if bits == 0:
            if pos >= len(src): break
            flags = src[pos] | 0xFF00
            pos += 1
            bits = 8
        if not (flags & 1):
            if pos + 1 >= len(src): raise ValueError("truncated tbl back-reference")
            a, b = src[pos], src[pos + 1]; pos += 2
            length = (b & 0x0F) + 3
            distance = ((b & 0xF0) << 4) | a
            for copy_index in range(length):
                value = ring[(distance + copy_index) & 0xFFF]
                out.append(value)
                ring[ring_pos] = value; ring_pos = (ring_pos + 1) & 0xFFF
                if len(out) == target - 0x100:
                    break
        else:
            value = src[pos]; pos += 1
            out.append(value)
            ring[ring_pos] = value; ring_pos = (ring_pos + 1) & 0xFFF
        if len(out) == target - 0x100:
            break
        flags >>= 1; bits -= 1
    if len(out) != target - 0x100:
        raise ValueError(f"tbl size mismatch: got {len(out)}, expected {target - 0x100}")
    return bytes(out)

def extract_tbl_tree(data: Path, out: Path) -> list[Path]:
    out.mkdir(parents=True, exist_ok=True)
    results=[]
    for source in sorted(data.glob("*.tbl")):
        target=out/source.name
        target.write_bytes(decode_tbl(source.read_bytes()))
        results.append(target)
    return results

def build_inventory(data: Path, out: Path) -> dict:
    """Write a small, source-free index of archive records for localization."""
    archives = []
    for tbl in sorted(data.glob("*.tbl")):
        pp = data / (tbl.stem + ".pp")
        if not pp.is_file():
            continue
        decoded = decode_tbl(tbl.read_bytes())
        rows = [{"name": n, "offset": o, "size": s}
                for n, o, s in index_entries(decoded, pp.stat().st_size)]
        archives.append({"archive": pp.name, "index": tbl.name, "entries": rows})
    result = {"schema": 1, "format": "biko3-localization-inventory-v1", "archives": archives}
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return result

def extract_archive_entries(data: Path, out: Path) -> int:
    """Extract bounds-checked PP records without changing their bytes."""
    count = 0
    out.mkdir(parents=True, exist_ok=True)
    for tbl in sorted(data.glob("*.tbl")):
        pp = data / (tbl.stem + ".pp")
        if not pp.is_file():
            continue
        rows = index_entries(decode_tbl(tbl.read_bytes()), pp.stat().st_size)
        with pp.open("rb") as source:
            for number, (name, offset, size) in enumerate(rows):
                source.seek(offset)
                payload = source.read(size)
                safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in name)
                (out / f"{tbl.stem}_{number:04d}_{safe}").write_bytes(payload)
                count += 1
    return count

def replace_entry(archive: Path, offset: int, original_size: int, replacement: bytes) -> None:
    """Replace one PP payload in place; size changes are rejected deliberately."""
    if len(replacement) != original_size:
        raise ValueError("in-place PP replacement must preserve payload size")
    with archive.open("r+b") as stream:
        stream.seek(offset)
        current = stream.read(original_size)
        if len(current) != original_size:
            raise ValueError("PP entry is truncated")
        stream.seek(offset)
        stream.write(replacement)

def index_entries(decoded: bytes, archive_size: int) -> list[tuple[str, int, int]]:
    """Validate every bucket and record, as the original archive loader does."""
    if len(decoded) < 0x800 or (len(decoded) - 0x800) % 0x50:
        raise ValueError("invalid index extent")
    found = set()
    names = set()
    expected_base = 0
    for bucket in range(256):
        base, count = struct.unpack_from("<II", decoded, bucket * 8)
        if base != expected_base or 0x800 + base + count * 0x50 > len(decoded):
            raise ValueError("invalid bucket extent")
        expected_base += count * 0x50
        for i in range(count):
            q = 0x800 + base + i * 0x50
            record_size, off, size, reserved = struct.unpack_from("<4I", decoded, q)
            raw_name = decoded[q + 0x10:q + 0x50]
            if b"\0" not in raw_name:
                raise ValueError("unterminated entry name")
            name = raw_name.split(b"\0", 1)[0]
            if (record_size != 0x50 or reserved or not name or name[0] != bucket
                    or b"/" in name or b"\\" in name or name in (b".", b"..")
                    or name.lower() in names or off + size > archive_size):
                raise ValueError("invalid archive record")
            names.add(name.lower())
            found.add((name.decode("cp932"), off, size))
    if expected_base != len(decoded) - 0x800:
        raise ValueError("unindexed record data")
    return sorted(found, key=lambda x: x[1])

def legacy_entries(archive: Path) -> list[tuple[str, int, int]]:
    """Validate the inline PP directory; names and payloads use byte negation."""
    extent = archive.stat().st_size
    with archive.open("rb") as stream:
        header = stream.read(8)
        if len(header) != 8:
            raise ValueError("truncated legacy PP header")
        count, payload_size = struct.unpack("<II", header)
        offset = 8 + count * 36
        if count > 100000 or offset + payload_size != extent:
            raise ValueError("invalid legacy PP extent")
        names = []
        for _ in range(count):
            raw = bytes((-value) & 255 for value in stream.read(32))
            if len(raw) != 32 or b"\0" not in raw:
                raise ValueError("invalid legacy PP name")
            name = raw.split(b"\0", 1)[0].decode("cp932")
            if not name or name in (".", "..") or "/" in name or "\\" in name:
                raise ValueError("unsafe legacy PP name")
            names.append(name)
        if len({name.lower() for name in names}) != count:
            raise ValueError("duplicate legacy PP name")
        sizes = struct.unpack("<" + "I" * count, stream.read(4 * count))
        rows = []
        for name, size in zip(names, sizes):
            rows.append((name, offset, size))
            offset += size
        if offset != extent:
            raise ValueError("legacy PP payload sizes mismatch")
        return rows

def export_scenarios(data: Path, out: Path) -> dict:
    """Recover final CP932 scenarios, applying verified full-replacement patches."""
    import hashlib
    import zlib
    base_archive, patch_archive = data / "bk3_05.pp", data / "patch.pp"
    base_rows = legacy_entries(base_archive)
    patch_rows = {name: (offset, size) for name, offset, size in legacy_entries(patch_archive)}
    out.mkdir(parents=True, exist_ok=False)
    records = []
    with base_archive.open("rb") as base, patch_archive.open("rb") as patch:
        for name, offset, size in base_rows:
            base.seek(offset)
            original = bytes((-v) & 255 for v in base.read(size))
            final = original
            encoding = "cp932"
            patch_name = "bk3_05." + name
            if patch_name in patch_rows:
                patch_offset, patch_size = patch_rows[patch_name]
                patch.seek(patch_offset)
                packed = bytes((-v) & 255 for v in patch.read(patch_size))
                decoded_size = struct.unpack_from("<I", packed)[0]
                decoder = zlib.decompressobj()
                decoded = decoder.decompress(packed[4:], 0x10000001)
                if (not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
                        or len(decoded) != decoded_size or len(decoded) < 20):
                    raise ValueError("invalid scenario patch compression")
                source_size, target_size, count, start, length = struct.unpack_from("<5I", decoded)
                if (source_size != len(original) or count != 1 or start != 0
                        or length != target_size or len(decoded) != 20 + length):
                    raise ValueError("unsupported scenario patch structure")
                final = decoded[20:]
                encoding = "gbk"
            text = final.decode(encoding)
            (out / name).write_bytes(text.encode("utf-8"))
            records.append({"name": name, "source_bytes": len(original), "final_bytes": len(final),
                            "patched": final != original, "lines": len(text.splitlines()),
                            "characters": len(text), "sha256": hashlib.sha256(final).hexdigest(),
                            "encoding": encoding,
                            "translation_status": "existing_patch_requires_review" if encoding == "gbk" else "untranslated"})
    report = {"schema": 1, "base_encoding": "cp932", "patch_encoding": "gbk", "export_encoding": "utf-8",
              "scenarios": records, "runtime_verified": False}
    (out / "manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report

if __name__ == "__main__":
    import argparse
    p=argparse.ArgumentParser(); p.add_argument("data", type=Path); p.add_argument("out", type=Path)
    p.add_argument("--inventory", action="store_true")
    p.add_argument("--extract-entries", action="store_true")
    p.add_argument("--export-scenarios", action="store_true")
    args=p.parse_args()
    if args.export_scenarios:
        result = export_scenarios(args.data, args.out)
        print(json.dumps({"scenarios": len(result["scenarios"]),
                          "characters": sum(r["characters"] for r in result["scenarios"])}))
    elif args.inventory:
        print(json.dumps(build_inventory(args.data, args.out), ensure_ascii=False, indent=2))
    elif args.extract_entries:
        print(extract_archive_entries(args.data, args.out))
    else:
        print("\n".join(map(str, extract_tbl_tree(args.data,args.out))))
