#!/usr/bin/env python3
"""Stream original Data files into raw 512 MiB parts with an embedded-IPA manifest.

No compression, encryption changes, game execution, keys or NSP access occurs.
verify reads parts in stream order and checks both file and part hashes without
creating a second resource tree. --data additionally checks the original tree.
"""
from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import unicodedata

FORMAT = "biko3-resource-parts-v1"
MANIFEST = "Biko3Data.manifest.json"
PART_BYTES = 512 * 1024 * 1024
CHUNK = 1024 * 1024
MAX_MANIFEST = 8 * 1024 * 1024
HASH = re.compile(r"^[0-9a-f]{64}$")


class PackError(ValueError):
    pass


def signature(info):
    # Python 3.12 Windows lstat exposes creation time via st_ctime while fstat
    # can expose change time. st_birthtime is consistent across both APIs.
    clock = getattr(info, "st_birthtime_ns", 0) if os.name == "nt" else info.st_ctime_ns
    return (info.st_dev, info.st_ino, info.st_mode, info.st_size,
            info.st_mtime_ns, clock)


def checked_stat(path: Path, *, directory=False):
    info = path.lstat()
    if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
        raise PackError(f"symlink/reparse point is forbidden: {path}")
    expected = stat.S_ISDIR if directory else stat.S_ISREG
    if not expected(info.st_mode):
        raise PackError(f"not a regular {'directory' if directory else 'file'}: {path}")
    return info


def validate_path(value):
    if not isinstance(value, str) or not value.startswith("Data/"):
        raise PackError("resource path must begin with Data/")
    if "\\" in value or any(ord(c) < 32 or ord(c) == 127 for c in value):
        raise PackError("resource path contains a separator or control character")
    if unicodedata.normalize("NFC", value) != value:
        raise PackError("resource path must use NFC Unicode normalization")
    for component in value.split("/"):
        if not component or component in (".", "..") or component.endswith((".", " ")):
            raise PackError("resource path contains an unsafe component")
        if any(c in component for c in ':<>"|?*'):
            raise PackError("resource path contains a nonportable filename character")
        base = component.split(".", 1)[0].upper()
        if base in {"CON", "PRN", "AUX", "NUL"} or re.fullmatch(r"(?:COM|LPT)[1-9]", base):
            raise PackError("resource path contains a reserved filename")
    return value


def path_key(value):
    return unicodedata.normalize("NFC", value).casefold()


@dataclass(frozen=True)
class Source:
    path: str
    disk_path: Path
    snapshot: tuple

    @property
    def size(self): return self.snapshot[3]


def inventory(data: Path):
    data = Path(data).absolute()
    checked_stat(data, directory=True)
    records, entry_keys = [], set()

    def visit(directory, components):
        with os.scandir(directory) as entries:
            names = sorted(entry.name for entry in entries)
        for name in names:
            path = directory / name
            info = path.lstat()
            if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
                raise PackError(f"symlink/reparse point is forbidden: {path}")
            relative = "Data/" + "/".join([*components, name])
            validate_path(relative)
            key = path_key(relative)
            if key in entry_keys:
                raise PackError("duplicate or case-colliding resource file/directory path")
            entry_keys.add(key)
            if stat.S_ISDIR(info.st_mode):
                visit(path, [*components, name])
            elif stat.S_ISREG(info.st_mode):
                records.append(Source(relative, path, signature(info)))
            else:
                raise PackError(f"non-regular source entry: {path}")

    visit(data, [])
    records.sort(key=lambda record: record.path)
    seen = set()
    for record in records:
        key = path_key(record.path)
        if key in seen:
            raise PackError("duplicate or case-colliding resource path")
        seen.add(key)
        # File-vs-directory prefixes must not collide on case-insensitive iOS.
        for i in range(2, len(record.path.split("/"))):
            if path_key("/".join(record.path.split("/")[:i])) in seen:
                raise PackError("resource file/directory path collision")
    return records


def source_identity(files):
    values = [{"path": item["path"], "size": item["size"], "sha256": item["sha256"]} for item in files]
    return hashlib.sha256(json.dumps(values, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":")).encode("utf8")).hexdigest()


def integer(value, label, *, minimum=0, maximum=(1 << 63)-1):
    if type(value) is not int or not minimum <= value <= maximum:
        raise PackError(f"invalid {label}")
    return value


def validate_manifest(manifest):
    if (not isinstance(manifest, dict) or type(manifest.get("schema")) is not int or
            manifest.get("schema") != 1 or manifest.get("format") != FORMAT or manifest.get("game") != "biko3"):
        raise PackError("unsupported resource manifest")
    limit = integer(manifest.get("part_bytes"), "part_bytes", minimum=1, maximum=PART_BYTES)
    total = integer(manifest.get("total_bytes"), "total_bytes")
    count = integer(manifest.get("file_count"), "file_count", maximum=100000)
    parts, files = manifest.get("parts"), manifest.get("files")
    if not isinstance(parts, list) or not isinstance(files, list) or len(files) != count or len(parts) > 10000:
        raise PackError("invalid part/file inventory")
    part_total = 0
    for i, part in enumerate(parts):
        if not isinstance(part, dict) or part.get("name") != f"Biko3Data.part{i+1:03d}.b3data":
            raise PackError("noncanonical or unsafe part name")
        size = integer(part.get("size"), "part size", minimum=1, maximum=limit)
        if i < len(parts)-1 and size != limit:
            raise PackError("nonfinal part does not fill part_bytes")
        if not isinstance(part.get("sha256"), str) or not HASH.fullmatch(part["sha256"]):
            raise PackError("invalid part SHA256")
        part_total += size
    if part_total != total:
        raise PackError("part sizes do not equal total_bytes")
    seen, previous, prefixes = set(), None, {}
    part_index, part_offset, file_total = 0, 0, 0
    all_keys = set()
    for item in files:
        if not isinstance(item, dict): raise PackError("invalid file record")
        path = validate_path(item.get("path"))
        components = path.split("/")
        for i in range(1, len(components)+1):
            prefix = "/".join(components[:i])
            folded = path_key(prefix)
            if folded in prefixes and prefixes[folded] != prefix:
                raise PackError("case-colliding resource directory/file path")
            prefixes[folded] = prefix
        key = path_key(path)
        if key in seen or (previous is not None and path <= previous):
            raise PackError("unsorted, duplicate or case-colliding file path")
        seen.add(key); all_keys.add(key); previous = path
        size = integer(item.get("size"), "file size")
        if not isinstance(item.get("sha256"), str) or not HASH.fullmatch(item["sha256"]):
            raise PackError("invalid file SHA256")
        segments = item.get("segments")
        if not isinstance(segments, list) or (size == 0 and segments):
            raise PackError("invalid file segments")
        covered = 0
        for segment in segments:
            if not isinstance(segment, dict): raise PackError("invalid segment")
            part = integer(segment.get("part"), "segment part")
            offset = integer(segment.get("offset"), "segment offset")
            length = integer(segment.get("length"), "segment length", minimum=1)
            if part != part_index or offset != part_offset or part >= len(parts):
                raise PackError("segments overlap, leave a gap or are out of stream order")
            if length > parts[part]["size"] - offset:
                raise PackError("segment extends beyond its part")
            covered += length; part_offset += length
            if part_offset == parts[part]["size"]:
                part_index += 1; part_offset = 0
        if covered != size:
            raise PackError("segments do not cover the file size")
        file_total += size
    for item in files:
        components = item["path"].split("/")
        if any(path_key("/".join(components[:i])) in all_keys for i in range(2, len(components))):
            raise PackError("resource file/directory path collision")
    if file_total != total or part_index != len(parts) or part_offset:
        raise PackError("files do not cover every part exactly once")
    if "source_identity_sha256" in manifest and manifest["source_identity_sha256"] != source_identity(files):
        raise PackError("source identity does not match the file inventory")
    return manifest


def open_source(record):
    if signature(checked_stat(record.disk_path)) != record.snapshot:
        raise PackError(f"source changed before read: {record.path}")
    flags = os.O_RDONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    descriptor = os.open(record.disk_path, flags)
    stream = os.fdopen(descriptor, "rb")
    if signature(os.fstat(stream.fileno())) != record.snapshot:
        stream.close(); raise PackError(f"source changed while opening: {record.path}")
    return stream


def finish_source(stream, record):
    if (stream.read(1) or signature(os.fstat(stream.fileno())) != record.snapshot or
            signature(checked_stat(record.disk_path)) != record.snapshot):
        raise PackError(f"source changed during read: {record.path}")


def pack(data: Path, output: Path, *, part_bytes=PART_BYTES, progress=None):
    integer(part_bytes, "part_bytes", minimum=1, maximum=PART_BYTES)
    data, output = Path(data).absolute(), Path(output).absolute()
    if output.resolve().is_relative_to(data.resolve()):
        raise PackError("package output must be outside the source Data tree")
    records = inventory(data)
    total = sum(record.size for record in records)
    if len(records) > 100000 or (total + part_bytes - 1) // part_bytes > 10000:
        raise PackError("resource inventory exceeds supported file/part counts")
    output.mkdir(parents=True, exist_ok=False)
    parts, files = [], []
    part_stream, part_hash, part_size, temporary = None, None, 0, None

    def close_part():
        nonlocal part_stream
        part_stream.flush(); os.fsync(part_stream.fileno()); part_stream.close(); part_stream = None
        name = f"Biko3Data.part{len(parts)+1:03d}.b3data"
        temporary.rename(output / name)
        entry = {"name": name, "size": part_size, "sha256": part_hash.hexdigest()}
        parts.append(entry)
        if progress: progress(entry)

    try:
        for record in records:
            file_hash, segments, remaining = hashlib.sha256(), [], record.size
            with open_source(record) as source:
                while remaining:
                    if part_stream is None:
                        temporary = output / f"Biko3Data.part{len(parts)+1:03d}.b3data.partial"
                        part_stream = temporary.open("xb")
                        part_hash, part_size = hashlib.sha256(), 0
                    wanted = min(CHUNK, remaining, part_bytes-part_size)
                    chunk = source.read(wanted)
                    if len(chunk) != wanted:
                        raise PackError(f"source truncated during packing: {record.path}")
                    if part_stream.write(chunk) != len(chunk): raise PackError("short part write")
                    file_hash.update(chunk); part_hash.update(chunk)
                    if segments and segments[-1]["part"] == len(parts):
                        segments[-1]["length"] += len(chunk)
                    else:
                        segments.append({"part": len(parts), "offset": part_size, "length": len(chunk)})
                    part_size += len(chunk); remaining -= len(chunk)
                    if part_size == part_bytes: close_part()
                finish_source(source, record)
            files.append({"path": record.path, "size": record.size, "sha256": file_hash.hexdigest(), "segments": segments})
        if part_stream is not None: close_part()
        if inventory(data) != records:
            raise PackError("source tree changed during packing")
        manifest = {"schema": 1, "format": FORMAT, "game": "biko3",
                    "total_bytes": sum(record.size for record in records), "file_count": len(files),
                    "part_bytes": part_bytes, "parts": parts, "files": files,
                    "source_identity_sha256": source_identity(files)}
        validate_manifest(manifest)
        encoded = (json.dumps(manifest, ensure_ascii=False, indent=2)+"\n").encode("utf8")
        if len(encoded) > MAX_MANIFEST: raise PackError("manifest exceeds supported size")
        with (output / (MANIFEST+".partial")).open("xb") as target:
            target.write(encoded); target.flush(); os.fsync(target.fileno())
        (output / (MANIFEST+".partial")).rename(output / MANIFEST)
        return manifest
    finally:
        if part_stream is not None: part_stream.close()


def read_manifest(path):
    checked_stat(Path(path))
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_MANIFEST+1)
    if len(raw) > MAX_MANIFEST: raise PackError("manifest exceeds supported size")

    def unique_keys(pairs):
        result = {}
        for key, value in pairs:
            if key in result: raise PackError("duplicate JSON manifest key")
            result[key] = value
        return result

    return validate_manifest(json.loads(raw, object_pairs_hook=unique_keys))


def verify(parts_directory: Path, manifest, *, data=None, restore=None, progress=None):
    manifest = validate_manifest(manifest)
    root = Path(parts_directory).absolute()
    checked_stat(root, directory=True)
    sources = None
    if data is not None:
        source_inventory = inventory(Path(data))
        if [(r.path, r.size) for r in source_inventory] != [(f["path"], f["size"]) for f in manifest["files"]]:
            raise PackError("source tree inventory/size differs from the manifest")
        sources = {record.path: record for record in source_inventory}
    destination = Path(restore).absolute() if restore is not None else None
    if destination is not None:
        destination.mkdir(parents=True, exist_ok=False)
        (destination / ".biko3-unpack-temp").mkdir()
    part_stream, part_digest, part_before, current_part, consumed = None, None, None, None, 0
    try:
        for index, item in enumerate(manifest["files"]):
            file_digest, source_digest = hashlib.sha256(), hashlib.sha256()
            source = open_source(sources[item["path"]]) if sources is not None else None
            restored = None
            temporary = destination / ".biko3-unpack-temp" / f"{index:06d}.partial" if destination else None
            try:
                if temporary is not None: restored = temporary.open("xb")
                for segment in item["segments"]:
                    descriptor = manifest["parts"][segment["part"]]
                    path = root / descriptor["name"]
                    if part_stream is None:
                        part_before = signature(checked_stat(path))
                        if part_before[3] != descriptor["size"]: raise PackError(f"part size/truncation mismatch: {path.name}")
                        part_stream = path.open("rb")
                        if signature(os.fstat(part_stream.fileno())) != part_before: raise PackError("part changed while opening")
                        part_digest, consumed, current_part = hashlib.sha256(), 0, segment["part"]
                    if current_part != segment["part"] or consumed != segment["offset"]:
                        raise PackError("unexpected part stream position")
                    remaining = segment["length"]
                    while remaining:
                        wanted = min(CHUNK, remaining)
                        chunk = part_stream.read(wanted)
                        if len(chunk) != wanted: raise PackError(f"part truncated during verification: {path.name}")
                        file_digest.update(chunk); part_digest.update(chunk)
                        if source is not None:
                            original_bytes = source.read(wanted)
                            if len(original_bytes) != wanted: raise PackError(f"source truncated: {item['path']}")
                            source_digest.update(original_bytes)
                        if restored is not None and restored.write(chunk) != len(chunk): raise PackError("short restore write")
                        remaining -= len(chunk); consumed += len(chunk)
                    if consumed == descriptor["size"]:
                        if (part_stream.read(1) or signature(os.fstat(part_stream.fileno())) != part_before or
                                signature(checked_stat(path)) != part_before):
                            raise PackError(f"part changed during verification: {path.name}")
                        if part_digest.hexdigest() != descriptor["sha256"]: raise PackError(f"part SHA256 mismatch: {path.name}")
                        part_stream.close(); part_stream = None
                        if progress: progress(descriptor)
                if file_digest.hexdigest() != item["sha256"]: raise PackError(f"file SHA256 mismatch: {item['path']}")
                if source is not None:
                    finish_source(source, sources[item["path"]])
                    if source_digest.hexdigest() != item["sha256"]: raise PackError(f"source SHA256 mismatch: {item['path']}")
                if restored is not None:
                    restored.flush(); os.fsync(restored.fileno()); restored.close(); restored = None
                    target = destination.joinpath(*item["path"].split("/"))
                    target.parent.mkdir(parents=True, exist_ok=True)
                    # The restore root is fresh; refuse any injected symlink.
                    current = destination
                    for component in item["path"].split("/")[:-1]:
                        current /= component; checked_stat(current, directory=True)
                    if target.exists() or target.is_symlink(): raise PackError("restore target already exists")
                    temporary.rename(target)
            finally:
                if source is not None: source.close()
                if restored is not None: restored.close()
        if part_stream is not None: raise PackError("unconsumed trailing part data")
        if sources is not None and inventory(Path(data)) != source_inventory:
            raise PackError("source tree changed during verification")
        if destination is not None: (destination / ".biko3-unpack-temp").rmdir()
        return {"verified": True, "source_verified": sources is not None,
                "source_identity_sha256": source_identity(manifest["files"]),
                "total_bytes": manifest["total_bytes"], "file_count": manifest["file_count"],
                "part_count": len(manifest["parts"]), "restored": destination is not None}
    finally:
        if part_stream is not None: part_stream.close()


def main(argv=None):
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments and arguments[0].startswith("--") and arguments[0] not in ("--help",):
        arguments.insert(0, "pack")
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    create = commands.add_parser("pack", help="pack an original Data directory")
    create.add_argument("--data", type=Path, required=True)
    create.add_argument("--out", type=Path, required=True)
    create.add_argument("--part-bytes", type=int, default=PART_BYTES)
    create.add_argument("--manifest-copy", type=Path)
    for command in ("verify", "restore"):
        check = commands.add_parser(command)
        check.add_argument("--parts", type=Path, required=True)
        check.add_argument("--manifest", type=Path)
        check.add_argument("--data", type=Path)
        if command == "restore": check.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(arguments)
    try:
        if args.command == "pack":
            if args.manifest_copy and args.manifest_copy.resolve().is_relative_to(args.data.resolve()):
                raise PackError("manifest copy must not modify the source Data tree")
            manifest = pack(args.data, args.out, part_bytes=args.part_bytes,
                            progress=lambda part: print(json.dumps({"packed": part}), flush=True))
            if args.manifest_copy:
                args.manifest_copy.parent.mkdir(parents=True, exist_ok=True)
                shutil.copyfile(args.out / MANIFEST, args.manifest_copy)
            print(json.dumps({"manifest": str(args.out / MANIFEST), "file_count": manifest["file_count"],
                              "total_bytes": manifest["total_bytes"], "parts": manifest["parts"],
                              "source_identity_sha256": manifest["source_identity_sha256"]}, indent=2))
        else:
            manifest = read_manifest(args.manifest or args.parts / MANIFEST)
            result = verify(args.parts, manifest, data=args.data,
                            restore=args.out if args.command == "restore" else None,
                            progress=lambda part: print(json.dumps({"verified_part": part["name"]}), flush=True))
            print(json.dumps(result, indent=2))
    except (OSError, ValueError, TypeError, KeyError) as error:
        print(f"Resource pack failed: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__": raise SystemExit(main())
