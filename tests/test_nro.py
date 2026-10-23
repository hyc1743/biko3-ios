import struct
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from nro import Nro, NroError


def sample(relr_words=(), mod0=True):
    data = bytearray(0x3000)
    struct.pack_into("<II", data, 0, 0x14000020, 0xF0 if mod0 else 0)
    data[0x10:0x14] = b"NRO0"
    struct.pack_into("<III", data, 0x14, 0, len(data), 0)
    for index in range(3):
        struct.pack_into("<II", data, 0x20 + index * 8, index * 0x1000, 0x1000)
    struct.pack_into("<I", data, 0x38, 0x1000)
    if mod0:
        data[0xF0:0xF4] = b"MOD0"
        struct.pack_into("<6i", data, 0xF4, 0x2100 - 0xF0, 0, 0, 0, 0, 0)
        tags = [(36, 0x1100), (35, len(relr_words) * 8), (37, 8), (0, 0)]
        for index, tag in enumerate(tags):
            struct.pack_into("<QQ", data, 0x2100 + index * 16, *tag)
        for index, word in enumerate(relr_words):
            struct.pack_into("<Q", data, 0x1100 + index * 8, word)
    return data


class NroTests(unittest.TestCase):
    def test_real_input_compatible_layout_and_entry(self):
        image = Nro(bytes(sample()))
        self.assertEqual(image.entry, 0x80)
        self.assertEqual(image.memory_size, 0x4000)

    def test_relr_bitmap_stride_and_original_addends(self):
        data = sample((0x2300, 1 | (1 << 1) | (1 << 3), 1 | (1 << 1)))
        for address in (0x2300, 0x2308, 0x2318, 0x2500):
            struct.pack_into("<Q", data, address, address + 17)
        relocations = Nro(bytes(data)).relr()
        self.assertEqual([r["target"] for r in relocations], [0x2300, 0x2308, 0x2318, 0x2500])
        self.assertTrue(all(r["addend"] == r["target"] + 17 for r in relocations))

    def test_bss_addend_is_zero_and_footer_is_not_memory(self):
        data = sample((0x3000,)) + b"ASSET FOOTER MUST NOT BECOME BSS"
        self.assertEqual(Nro(bytes(data)).relr(), [{"target": 0x3000, "addend": 0}])

    def test_bitmap_cannot_precede_address(self):
        with self.assertRaisesRegex(NroError, "precedes"):
            Nro(bytes(sample((3,)))).relr()

    def test_relocation_cannot_leave_memory(self):
        with self.assertRaisesRegex(NroError, "outside"):
            Nro(bytes(sample((0x4000,)))).relr()

    def test_duplicate_relocations_rejected(self):
        with self.assertRaisesRegex(NroError, "duplicate"):
            Nro(bytes(sample((0x2300, 0x2300)))).relr()

    def test_truncation_bad_magic_and_segment_overlap_rejected(self):
        bad_magic = sample()
        bad_magic[0x10] = 0
        overlap = sample()
        struct.pack_into("<I", overlap, 0x28, 0)
        for value in (b"", bytes(sample()[:100]), bytes(bad_magic), bytes(overlap)):
            with self.subTest(length=len(value)), self.assertRaises(NroError):
                Nro(value)

    def test_unterminated_dynamic_table_rejected(self):
        data = sample()
        for offset in range(0x2100, 0x3000, 16):
            struct.pack_into("<QQ", data, offset, 1, 1)
        with self.assertRaisesRegex(NroError, "unterminated"):
            Nro(bytes(data))

    def test_elf_preserves_every_load_segment_and_bss(self):
        original = bytes(sample((0x2300,))) + b"ASSET FOOTER"
        image = Nro(original)
        elf = image.to_elf()
        self.assertEqual(elf[:7], b"\x7fELF\x02\x01\x01")
        self.assertEqual(struct.unpack_from("<H", elf, 18)[0], 183)
        self.assertEqual(struct.unpack_from("<Q", elf, 24)[0], 0x80)
        for index, segment in enumerate(image.segments):
            typ, flags, off, addr, physical, filesz, memsz, align = struct.unpack_from("<IIQQQQQQ", elf, 64 + index * 56)
            self.assertEqual(typ, 1)
            self.assertEqual(addr, segment.offset)
            self.assertEqual(physical, addr)
            self.assertEqual(filesz, segment.size)
            self.assertEqual(off % align, addr % align)
            self.assertEqual(memsz - filesz, image.bss_size if index == 2 else 0)
            self.assertEqual(elf[off:off + filesz], original[addr:addr + filesz])
        self.assertNotIn(b"ASSET FOOTER", elf)
        self.assertEqual(image.data, original)

    def test_optional_dynamic_table_absent(self):
        image = Nro(bytes(sample(mod0=False)))
        self.assertEqual(image.relr(), [])
        self.assertEqual(struct.unpack_from("<H", image.to_elf(), 56)[0], 3)


if __name__ == "__main__":
    unittest.main()
