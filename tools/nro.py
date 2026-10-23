"""Read an NRO without executing it; create an address-preserving analysis ELF.

The generated ELF is an analysis container, not a Linux executable or an iOS
port. Original image addresses and bytes are preserved. The NRO asset footer
is deliberately excluded from the ELF load image.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import struct


class NroError(ValueError):
    pass


@dataclass(frozen=True)
class Segment:
    name: str
    offset: int
    size: int
    permissions: str

    @property
    def end(self) -> int:
        return self.offset + self.size


class Nro:
    def __init__(self, data: bytes):
        self.data = data
        if len(data) < 0x80 or data[0x10:0x14] != b"NRO0":
            raise NroError("not an NRO0 image or truncated header")
        self.version, self.image_size, self.flags = struct.unpack_from("<III", data, 0x14)
        if self.version != 0:
            raise NroError(f"unsupported NRO version {self.version}")
        self.segments = tuple(
            Segment(name, *struct.unpack_from("<II", data, off), perms)
            for name, off, perms in (
                ("text", 0x20, "rx"), ("rodata", 0x28, "r"), ("data", 0x30, "rw")
            )
        )
        self.bss_size = struct.unpack_from("<I", data, 0x38)[0]
        self.build_id = data[0x40:0x60].hex()
        self.sha256 = hashlib.sha256(data).hexdigest()
        if self.image_size < 0x80 or self.image_size > len(data):
            raise NroError("declared NRO image size exceeds file bounds")
        previous_end = 0
        for seg in self.segments:
            if seg.size == 0 or seg.offset < previous_end or seg.end > self.image_size:
                raise NroError(f"invalid or overlapping {seg.name} segment")
            if seg.offset % 0x1000 or seg.size % 0x1000:
                raise NroError(f"unaligned {seg.name} segment; loader profile requires 4 KiB pages")
            previous_end = seg.end
        if self.segments[0].offset != 0 or self.segments[0].size < 0x100:
            raise NroError("text must contain the NRO header and entry stub")
        if previous_end != self.image_size:
            raise NroError("load segments do not end at declared image size")
        self.memory_size = self.segments[-1].end + self.bss_size
        self.mod0_offset = struct.unpack_from("<I", data, 4)[0]
        self.dynamic_offset = None
        self.dynamic_entries: list[tuple[int, int]] = []
        self.mod0 = None
        if self.mod0_offset:
            self._file_range(self.mod0_offset, 0x1C)
            if data[self.mod0_offset:self.mod0_offset + 4] != b"MOD0":
                raise NroError("entry header points to an invalid MOD0")
            fields = struct.unpack_from("<6i", data, self.mod0_offset + 4)
            names = ("dynamic", "bss_start", "bss_end", "eh_frame_start", "eh_frame_end", "module_object")
            self.mod0 = {
                name: self.mod0_offset + relative if relative else None
                for name, relative in zip(names, fields)
            }
            self.dynamic_offset = self.mod0["dynamic"]
            if self.dynamic_offset is not None:
                containing = next((s for s in self.segments if s.offset <= self.dynamic_offset < s.end), None)
                if containing is None:
                    raise NroError("dynamic table is outside file-backed segments")
                for off in range(self.dynamic_offset, containing.end - 15, 16):
                    tag, value = struct.unpack_from("<QQ", data, off)
                    self.dynamic_entries.append((tag, value))
                    if tag == 0:
                        break
                else:
                    raise NroError("unterminated dynamic table")
        self.entry = self._entry()

    def _file_range(self, offset: int, size: int) -> None:
        if offset < 0 or size < 0 or offset + size > self.image_size:
            raise NroError(f"range {offset:#x}+{size:#x} exceeds load image")

    def _entry(self) -> int:
        instruction = struct.unpack_from("<I", self.data)[0]
        if instruction & 0xFC000000 != 0x14000000:
            raise NroError("unrecognized NRO entry; expected AArch64 B immediate")
        immediate = instruction & 0x03FFFFFF
        if immediate & (1 << 25):
            immediate -= 1 << 26
        entry = immediate * 4
        if not 0x80 <= entry < self.segments[0].end:
            raise NroError("entry branch leaves the text segment or targets the header")
        return entry

    def tag(self, name: int) -> int | None:
        values = [v for t, v in self.dynamic_entries if t == name]
        if len(values) > 1:
            raise NroError(f"duplicate dynamic tag {name:#x}")
        return values[0] if values else None

    def read_memory(self, address: int, size: int) -> bytes:
        if address < 0 or size < 0 or address + size > self.memory_size:
            raise NroError(f"memory range {address:#x}+{size:#x} is outside the image")
        result = bytearray()
        cursor, end = address, address + size
        while cursor < end:
            segment = next((s for s in self.segments if s.offset <= cursor < s.end), None)
            if segment:
                limit = min(segment.end, end)
                result.extend(self.data[cursor:limit])
            elif self.image_size <= cursor < self.memory_size:
                limit = end
                result.extend(bytes(limit - cursor))
            else:
                raise NroError(f"memory range enters an unmapped gap at {cursor:#x}")
            cursor = limit
        return bytes(result)

    def relr(self) -> list[dict[str, int]]:
        """Decode DT_RELR targets, retaining their original in-place addends."""
        address, size, stride = self.tag(36), self.tag(35), self.tag(37)
        if address is None and size is None and stride is None:
            return []
        if address is None or size is None or stride != 8 or size % 8 or address % 8:
            raise NroError("incomplete or malformed ELF64 DT_RELR table")
        self._file_range(address, size)
        targets: list[int] = []
        base = None
        for off in range(address, address + size, 8):
            word = struct.unpack_from("<Q", self.data, off)[0]
            if not word & 1:
                if word % 8:
                    raise NroError("unaligned RELR address")
                targets.append(word)
                base = word + 8
            else:
                if base is None:
                    raise NroError("RELR bitmap precedes its address entry")
                targets.extend(base + bit * 8 for bit in range(63) if word & (1 << (bit + 1)))
                base += 63 * 8
        if len(set(targets)) != len(targets):
            raise NroError("duplicate RELR relocation targets")
        return [
            {"target": target, "addend": struct.unpack("<Q", self.read_memory(target, 8))[0]}
            for target in targets
        ]

    def dynamic_summary(self) -> dict:
        names = {0: "DT_NULL", 4: "DT_HASH", 5: "DT_STRTAB", 6: "DT_SYMTAB", 7: "DT_RELA",
                 8: "DT_RELASZ", 9: "DT_RELAENT", 10: "DT_STRSZ", 11: "DT_SYMENT",
                 12: "DT_INIT", 13: "DT_FINI", 25: "DT_INIT_ARRAY", 26: "DT_FINI_ARRAY",
                 27: "DT_INIT_ARRAYSZ", 28: "DT_FINI_ARRAYSZ", 35: "DT_RELRSZ",
                 36: "DT_RELR", 37: "DT_RELRENT"}
        # SysV hash nchain counts dynamic symbol entries, including symbol zero.
        count = None
        hash_address = self.tag(4)
        if hash_address is not None:
            self._file_range(hash_address, 8)
            _, count = struct.unpack_from("<II", self.data, hash_address)
            if count > self.image_size // 24:
                raise NroError("invalid SysV hash symbol count")
        symbols = []
        symtab, strtab, strsize = self.tag(6), self.tag(5), self.tag(10)
        if count is not None and symtab is not None and strtab is not None and strsize is not None:
            if self.tag(11) != 24:
                raise NroError("unexpected ELF64 symbol entry size")
            self._file_range(symtab, count * 24)
            self._file_range(strtab, strsize)
            for i in range(count):
                offset, info, other, section, value, size = struct.unpack_from("<IBBHQQ", self.data, symtab + i * 24)
                if offset >= strsize:
                    raise NroError("symbol name exceeds DT_STRTAB")
                end = self.data.find(b"\0", strtab + offset, strtab + strsize)
                if end < 0:
                    raise NroError("unterminated symbol name")
                name = self.data[strtab + offset:end].decode("utf-8", errors="replace")
                if name:
                    symbols.append({"name": name, "value": value, "size": size,
                                    "undefined": section == 0, "info": info, "visibility": other})
        return {"entries": [{"tag": hex(t), "name": names.get(t, "unknown"), "value": hex(v)}
                            for t, v in self.dynamic_entries],
                "symbol_count_including_null": count, "named_symbols": symbols,
                "limitation": "Dynamic symbols only; this does not prove absence of runtime-resolved APIs."}

    def to_elf(self) -> bytes:
        """ELF64/EM_AARCH64 ET_DYN, no relocation application or source invention."""
        payload_offset = 0x1000
        dynamic_size = len(self.dynamic_entries) * 16
        phnum = 3 + (1 if dynamic_size else 0)
        output = bytearray(payload_offset)
        output.extend(self.data[:self.image_size])
        # Overlapping dynamic/RELR sections are intentionally not fabricated.
        # PT_DYNAMIC retains the original dynamic metadata for capable loaders.
        sections = [("", 0, 0, 0, 0, 0, 0),
                    (".nro_header", 1, 2, 0, payload_offset, 0x80, 4),
                    (".text", 1, 6, 0x80, payload_offset + 0x80, self.segments[0].size - 0x80, 4),
                    (".rodata", 1, 2, self.segments[1].offset, payload_offset + self.segments[1].offset,
                     self.segments[1].size, 0x1000),
                    (".data", 1, 3, self.segments[2].offset, payload_offset + self.segments[2].offset,
                     self.segments[2].size, 0x1000),
                    (".bss", 8, 3, self.image_size, payload_offset + self.image_size, self.bss_size, 0x1000)]
        names = bytearray(b"\0")
        name_offsets = {"": 0}
        for name in [s[0] for s in sections[1:]] + [".shstrtab"]:
            name_offsets[name] = len(names)
            names.extend(name.encode() + b"\0")
        shstr_offset = len(output)
        output.extend(names)
        sections.append((".shstrtab", 3, 0, 0, shstr_offset, len(names), 1))
        output.extend(bytes((-len(output)) % 8))
        shoff = len(output)
        for index, (name, typ, flags, addr, off, size, align) in enumerate(sections):
            if index == 0:
                output.extend(bytes(64))
            else:
                output.extend(struct.pack("<IIQQQQIIQQ", name_offsets[name], typ, flags, addr, off,
                                          size, 0, 0, align, 0))
        ident = b"\x7fELF\x02\x01\x01" + bytes(9)
        output[:64] = struct.pack("<16sHHIQQQIHHHHHH", ident, 3, 183, 1, self.entry, 64, shoff, 0,
                                  64, 56, phnum, 64, len(sections), len(sections) - 1)
        for i, seg in enumerate(self.segments):
            flags = {"rx": 5, "r": 4, "rw": 6}[seg.permissions]
            memsz = seg.size + (self.bss_size if seg.name == "data" else 0)
            output[64 + i * 56:120 + i * 56] = struct.pack(
                "<IIQQQQQQ", 1, flags, payload_offset + seg.offset, seg.offset, seg.offset,
                seg.size, memsz, 0x1000)
        if dynamic_size:
            address = self.dynamic_offset
            flags = 6 if self.segments[2].offset <= address < self.segments[2].end else 4
            output[64 + 3 * 56:120 + 3 * 56] = struct.pack(
                "<IIQQQQQQ", 2, flags, payload_offset + address, address, address,
                dynamic_size, dynamic_size, 8)
        return bytes(output)

