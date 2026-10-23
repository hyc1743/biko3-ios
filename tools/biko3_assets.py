#!/usr/bin/env python3
"""Decode Biko3's protected .tbl text/index streams for localization work."""
from __future__ import annotations

from pathlib import Path
import struct
import json

KEY = b"\x2f\xca\xd8\x35"
MAGIC_XOR = 0xA67F54CB

def preview_texture(source: Path, destination: Path) -> dict:
    """Convert unsupported texture previews to PNG without editing the source."""
    import hashlib
    from PIL import Image
    if destination.suffix.lower() != ".png" or destination.exists():
        raise ValueError("preview requires a fresh PNG destination")
    if source.resolve() == destination.resolve():
        raise ValueError("preview must not overwrite the source")
    source_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    with Image.open(source) as texture:
        texture.load()
        pixels = texture.convert("RGBA")
    destination.parent.mkdir(parents=True, exist_ok=True)
    pixels.save(destination, format="PNG")
    with Image.open(destination) as check:
        if check.size != pixels.size or check.convert("RGBA").tobytes() != pixels.tobytes():
            raise ValueError("texture preview pixel mismatch")
    if hashlib.sha256(source.read_bytes()).hexdigest() != source_hash:
        raise ValueError("source changed during preview")
    return {"source_sha256": source_hash, "width": pixels.width, "height": pixels.height,
            "preview": str(destination), "source_unchanged": True}

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
    for pp in sorted(data.glob("*.pp")):
        tbl = pp.with_suffix(".tbl")
        decoded = decode_tbl(tbl.read_bytes()) if tbl.is_file() else None
        rows = [{"name": n, "offset": o, "size": s}
                for n, o, s in (index_entries(decoded, pp.stat().st_size)
                                 if decoded is not None else legacy_entries(pp))]
        archives.append({"archive": pp.name, "index": tbl.name if decoded is not None else None,
                         "payload_negated": decoded is None, "entries": rows})
    loose = [{"name": path.name, "size": path.stat().st_size, "review": "pending"}
             for path in sorted(data.iterdir()) if path.is_file() and path.suffix.lower() not in (".pp", ".tbl")]
    result = {"schema": 1, "format": "biko3-localization-inventory-v1", "archives": archives,
              "loose_files": loose, "complete_localization": False}
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

def apply_resource_patch(original: bytes, packed: bytes) -> bytes:
    """Apply the original BKPT v2 replacement/range format (5ce9f0)."""
    import zlib
    if len(packed) < 5:
        raise ValueError("truncated patch stream")
    declared = struct.unpack_from("<I", packed)[0]
    if not 12 <= declared <= 0x10000000:
        raise ValueError("invalid patch output limit")
    decoder = zlib.decompressobj()
    decoded = decoder.decompress(packed[4:], declared + 1)
    if (not decoder.eof or decoder.unused_data or decoder.unconsumed_tail
            or len(decoded) != declared):
        raise ValueError("invalid patch compression")
    old_size, new_size, count = struct.unpack_from("<III", decoded)
    if old_size != len(original) or not new_size or not count:
        raise ValueError("patch base identity/size mismatch")
    position = 12
    spans = []
    for _ in range(count):
        if position + 8 > len(decoded):
            raise ValueError("truncated patch record")
        start, size = struct.unpack_from("<II", decoded, position)
        position += 8
        if start + size > new_size or position + size > len(decoded):
            raise ValueError("patch record outside output")
        spans.append((start, decoded[position:position + size]))
        position += size
    if position != len(decoded):
        raise ValueError("trailing patch data")
    if new_size != old_size and (count != 1 or spans[0][0] != 0 or len(spans[0][1]) != new_size):
        raise ValueError("resized patch must replace the complete resource")
    result = bytearray(original if new_size == old_size else bytes(new_size))
    for start, payload in spans:
        result[start:start + len(payload)] = payload
    return bytes(result)

def encode_resource_patch(original: bytes, replacement: bytes) -> bytes:
    """Build a BKPT v2 full replacement accepted by the original loader."""
    import zlib
    if not replacement or len(replacement) > 0x10000000 - 20:
        raise ValueError("replacement resource is empty or too large")
    decoded = struct.pack("<5I", len(original), len(replacement), 1, 0, len(replacement)) + replacement
    packed = struct.pack("<I", len(decoded)) + zlib.compress(decoded)
    if apply_resource_patch(original, packed) != replacement:
        raise ValueError("replacement failed round-trip validation")
    return packed

def audit_existing_patch(data: Path, output: Path) -> dict:
    """Apply every existing patch against its actual base without changing Data."""
    import hashlib
    archive_cache = {}
    records = []
    loose = {p.name.lower(): p for p in data.iterdir() if p.is_file()}
    patch = data / "patch.pp"
    with patch.open("rb") as stream:
        for key, offset, size in legacy_entries(patch):
            stream.seek(offset)
            packed = bytes((-v) & 255 for v in stream.read(size))
            if key == "patch.cfg":
                if packed != b"BKPT\x02\0\0\0\x03\0\0\0":
                    raise ValueError("unknown existing patch configuration")
                continue
            namespace, name = key.split(".", 1)
            if namespace == "fonts":
                original = loose[name.lower()].read_bytes()
            else:
                archive = data / (namespace + ".pp")
                tbl = archive.with_suffix(".tbl")
                if namespace not in archive_cache:
                    rows = (index_entries(decode_tbl(tbl.read_bytes()), archive.stat().st_size)
                            if tbl.is_file() else legacy_entries(archive))
                    archive_cache[namespace] = {n.lower(): (o, s) for n, o, s in rows}
                base_offset, base_size = archive_cache[namespace][name.lower()]
                with archive.open("rb") as base:
                    base.seek(base_offset)
                    original = base.read(base_size)
                if not tbl.is_file():
                    original = bytes((-v) & 255 for v in original)
            effective = apply_resource_patch(original, packed)
            records.append({"key": key, "base_bytes": len(original), "effective_bytes": len(effective),
                            "base_sha256": hashlib.sha256(original).hexdigest(),
                            "effective_sha256": hashlib.sha256(effective).hexdigest(),
                            "format_validated": True, "language_review": "pending"})
    result = {"schema": 1, "patch_sha256": hashlib.sha256(patch.read_bytes()).hexdigest(),
              "records": records, "runtime_verified": False,
              "scope": "Patch format and base matching only; not Japanese coverage proof"}
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    return result

def write_legacy_archive(destination: Path, entries: list[tuple[str, bytes]]) -> None:
    """Write a fresh legacy PP, preserving supplied entry order and payloads."""
    if len(entries) > 100000 or destination.exists():
        raise ValueError("archive destination must be fresh and count within engine limit")
    encoded_names = []
    seen = set()
    for name, payload in entries:
        encoded = name.encode("cp932")
        if (not encoded or len(encoded) >= 32 or b"\0" in encoded or "/" in name or "\\" in name
                or name in (".", "..") or name.lower() in seen or len(payload) > 0x10000000):
            raise ValueError("invalid legacy archive entry")
        seen.add(name.lower())
        encoded_names.append(encoded.ljust(32, b"\0"))
    total = sum(len(payload) for _, payload in entries)
    if total + 8 + 36 * len(entries) >= 0x100000000:
        raise ValueError("legacy archive exceeds 32-bit engine extent")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as stream:
        stream.write(struct.pack("<II", len(entries), total))
        for name in encoded_names:
            stream.write(bytes((-v) & 255 for v in name))
        for _, payload in entries:
            stream.write(struct.pack("<I", len(payload)))
        for _, payload in entries:
            stream.write(bytes((-v) & 255 for v in payload))
    legacy_entries(destination)

def supplement_patch(data: Path, replacement_directory: Path, manifest_path: Path, destination: Path) -> dict:
    """Merge reviewed Japanese replacements into the existing patch unchanged elsewhere.

    The manifest binds each replacement to its original archive resource and to
    both the effective (already translated) source and the new payload SHA256.
    """
    import hashlib
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("replacement manifest must be an object")
    records = manifest.get("replacements")
    if manifest.get("schema") != 1 or not isinstance(records, list) or not records:
        raise ValueError("replacement manifest must contain reviewed replacements")
    patch = data / "patch.pp"
    old_entries = []
    with patch.open("rb") as stream:
        for name, offset, size in legacy_entries(patch):
            stream.seek(offset)
            old_entries.append((name, bytes((-v) & 255 for v in stream.read(size))))
    old = dict(old_entries)
    if old.get("patch.cfg") != b"BKPT\x02\0\0\0\x03\0\0\0":
        raise ValueError("requires the reviewed original BKPT v2 Chinese patch")
    updates = {}
    for record in records:
        namespace, name = record["archive"], record["name"]
        if (not namespace.startswith("bk3_") or len(namespace) != 6
                or not namespace[4:].isdigit() or Path(name).name != name or "\\" in name):
            raise ValueError("invalid replacement resource identity")
        key = namespace + "." + name
        if key in updates:
            raise ValueError("duplicate replacement resource")
        archive = data / (namespace + ".pp")
        tbl = archive.with_suffix(".tbl")
        rows = (index_entries(decode_tbl(tbl.read_bytes()), archive.stat().st_size)
                if tbl.exists() else legacy_entries(archive))
        row = next((r for r in rows if r[0] == name), None)
        if row is None:
            raise ValueError("replacement resource absent from archive")
        with archive.open("rb") as stream:
            stream.seek(row[1]); original = stream.read(row[2])
        if not tbl.exists():
            original = bytes((-v) & 255 for v in original)
        effective = apply_resource_patch(original, old[key]) if key in old else original
        if hashlib.sha256(effective).hexdigest() != record["effective_sha256"]:
            raise ValueError("effective source hash mismatch")
        relative = Path(record["file"])
        source = (replacement_directory / relative).resolve()
        if relative.is_absolute() or not source.is_relative_to(replacement_directory.resolve()):
            raise ValueError("replacement file outside reviewed directory")
        replacement = source.read_bytes()
        if hashlib.sha256(replacement).hexdigest() != record["replacement_sha256"]:
            raise ValueError("replacement hash mismatch")
        updates[key] = encode_resource_patch(original, replacement)
    merged = [(name, updates.get(name, payload)) for name, payload in old_entries]
    merged.extend((name, payload) for name, payload in updates.items() if name not in old)
    write_legacy_archive(destination, merged)
    return {"schema": 1, "replacements": len(updates), "entries": len(merged),
            "sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            "runtime_verified": False}

def export_effective_ui(data: Path, out: Path) -> dict:
    """Export the UI archive after applying existing patches, for Japanese audit."""
    import hashlib
    archive = data / "bk3_00.pp"
    rows = index_entries(decode_tbl(archive.with_suffix(".tbl").read_bytes()), archive.stat().st_size)
    patch_archive = data / "patch.pp"
    patches = {name: (offset, size) for name, offset, size in legacy_entries(patch_archive)}
    out.mkdir(parents=True, exist_ok=False)
    records = []
    with archive.open("rb") as source, patch_archive.open("rb") as patch:
        for name, offset, size in rows:
            source.seek(offset)
            payload = source.read(size)
            original_hash = hashlib.sha256(payload).hexdigest()
            key = "bk3_00." + name
            if key in patches:
                poffset, psize = patches[key]
                patch.seek(poffset)
                packed = bytes((-v) & 255 for v in patch.read(psize))
                payload = apply_resource_patch(payload, packed)
            (out / name).write_bytes(payload)
            records.append({"name": name, "base_sha256": original_hash,
                            "effective_sha256": hashlib.sha256(payload).hexdigest(),
                            "existing_patch_applied": key in patches,
                            "japanese_review": "pending"})
    report = {"schema": 1, "archive": archive.name, "records": records,
              "scope": "UI archive only; world textures and executable remain to audit"}
    (out / "manifest.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report

if __name__ == "__main__":
    import argparse
    p=argparse.ArgumentParser(); p.add_argument("data", type=Path); p.add_argument("out", type=Path)
    p.add_argument("--inventory", action="store_true")
    p.add_argument("--extract-entries", action="store_true")
    p.add_argument("--export-scenarios", action="store_true")
    p.add_argument("--effective-ui", action="store_true")
    p.add_argument("--audit-patch", action="store_true")
    p.add_argument("--preview-texture", action="store_true", help="Read one texture and write a lossless PNG preview; requires Pillow")
    args=p.parse_args()
    if args.preview_texture:
        print(json.dumps(preview_texture(args.data, args.out)))
    elif args.audit_patch:
        result = audit_existing_patch(args.data, args.out)
        print(json.dumps({"validated_patch_records": len(result["records"]), "runtime_verified": False}))
    elif args.effective_ui:
        result = export_effective_ui(args.data, args.out)
        print(json.dumps({"resources": len(result["records"]), "patched": sum(r["existing_patch_applied"] for r in result["records"])}))
    elif args.export_scenarios:
        result = export_scenarios(args.data, args.out)
        print(json.dumps({"scenarios": len(result["scenarios"]),
                          "characters": sum(r["characters"] for r in result["scenarios"])}))
    elif args.inventory:
        print(json.dumps(build_inventory(args.data, args.out), ensure_ascii=False, indent=2))
    elif args.extract_entries:
        print(extract_archive_entries(args.data, args.out))
    else:
        print("\n".join(map(str, extract_tbl_tree(args.data,args.out))))
