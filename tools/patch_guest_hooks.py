"""Add build-bound static host hooks to a rewritten copy, never the source NRO."""
from __future__ import annotations
import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil
import struct
from nro import Nro, NroError

SOURCE_SHA = "31320a8ce44f8e1826fc90ed5b26cb714c551413b286f8c69adb03f9e8b07178"
REWRITTEN_SHA = "84d208ef9289ec36ef54148f453533aea3d8dd802deffb55aad5b9a402b4ff1a"
TABLE_OFF = 0xfac000
PATCH_OFF = 0xfb0000

def digest(data): return hashlib.sha256(data).hexdigest()

def branch(source, target):
    delta = target - source
    if delta % 4 or not -(1 << 27) <= delta < (1 << 27):
        raise NroError("hook branch outside AArch64 B reach")
    return struct.pack("<I", 0x14000000 | ((delta >> 2) & 0x3ffffff))

def stub(pc, slot):
    if not 0 <= slot < 2048: raise NroError("hook table slot out of range")
    address = TABLE_OFF + slot * 8
    pages = (address >> 12) - (pc >> 12)
    if not -(1 << 20) <= pages < (1 << 20): raise NroError("ADRP reach")
    adrp = 0x90000010 | ((pages & 3) << 29) | (((pages >> 2) & 0x7ffff) << 5)
    ldr = 0xf9400210 | (((address & 0xfff) >> 3) << 10)
    return struct.pack("<III", adrp, ldr, 0xd61f0200)

def apply_hooks(source, rewritten, boundaries, hooks, guard=True):
    image = Nro(source)
    if image.sha256 != SOURCE_SHA: raise NroError("unsupported source NRO SHA256")
    if digest(rewritten) != REWRITTEN_SHA: raise NroError("unsupported rewritten image SHA256")
    if boundaries["source_nro_sha256"] != SOURCE_SHA: raise NroError("FDE source mismatch")
    starts = {f["start"] for f in boundaries["functions"]}
    if image.memory_size > TABLE_OFF or len(rewritten) < PATCH_OFF:
        raise NroError("hook table overlaps original image/BSS or lies outside copy")
    if any(rewritten[TABLE_OFF:PATCH_OFF]): raise NroError("hook table page is not entirely empty")
    used_slots, used_addresses = set(), set()
    output = bytearray(rewritten)
    records = []
    for hook in hooks:
        address, slot = hook["offset"], hook["slot"]
        if isinstance(address, str): address = int(address, 0)
        if address not in starts or not 0x80 <= address < image.segments[0].end:
            raise NroError(f"hook is not a verified FDE text entry: {address:#x}")
        if slot in used_slots or address in used_addresses: raise NroError("duplicate hook slot or address")
        used_slots.add(slot); used_addresses.add(address)
        original = source[address:address+16].hex()
        if "expected_original" in hook and hook["expected_original"] != original:
            raise NroError("hook original bytes mismatch")
        output.extend(b"\0" * (-len(output) % 16))
        pc = len(output)
        output.extend(stub(pc, slot))
        before = rewritten[address:address+4].hex()
        output[address:address+4] = branch(address, pc)
        records.append(dict(hook, offset=address, expected_original=original,
                            rewritten_before=before, stub_offset=pc,
                            pointer_offset=TABLE_OFF + slot*8))
    guards = []
    if guard:
        # This is ONLY the driver-ID rejection. Features/pNext/queue requirements
        # remain checked by the real Vulkan implementation and bridge.
        pc = 0xd0ddc
        expected = bytes.fromhex("212f0054")
        if source[pc:pc+4] != expected or rewritten[pc:pc+4] != expected:
            raise NroError("Mesa NVK guard original instruction mismatch")
        output[pc:pc+4] = bytes.fromhex("1f2003d5")
        guards.append({"offset": pc, "original": expected.hex(), "replacement": "1f2003d5",
                       "reason": "replace driver-ID requirement with real Vulkan graphics/compute validation"})
    manifest = {"schema": 1, "source_nro_sha256": SOURCE_SHA,
                "rewritten_input_sha256": REWRITTEN_SHA, "output_sha256": digest(output),
                "table_offset": TABLE_OFF, "table_size": PATCH_OFF - TABLE_OFF,
                "hooks": records, "guards": guards, "runtime_verified": False}
    return bytes(output), manifest

def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--nro", type=Path, required=True)
    p.add_argument("--rewritten", type=Path, required=True)
    p.add_argument("--provenance", type=Path, required=True)
    p.add_argument("--boundaries", type=Path, required=True)
    p.add_argument("--hooks", type=Path, required=True)
    p.add_argument("--out", type=Path, required=True)
    args = p.parse_args(argv)
    provenance = json.loads(args.provenance.read_text(encoding="utf8"))
    artifacts = {key.replace("\\", "/"): value for key, value in provenance["artifacts"].items()}
    if provenance["source_nro_sha256"] != SOURCE_SHA or artifacts["rewritten/main.patched.bin"] != REWRITTEN_SHA:
        raise NroError("rewrite provenance identity mismatch")
    hooks = json.loads(args.hooks.read_text(encoding="utf8"))
    if isinstance(hooks, dict): hooks = hooks["hooks"]
    out, manifest = apply_hooks(args.nro.read_bytes(), args.rewritten.read_bytes(),
                               json.loads(args.boundaries.read_text(encoding="utf8")), hooks)
    args.out.mkdir(parents=True, exist_ok=False)
    for sibling in args.rewritten.parent.glob("main.*.tsv"):
        key = "rewritten/" + sibling.name
        if digest(sibling.read_bytes()) != artifacts.get(key): raise NroError("rewrite metadata hash mismatch")
        shutil.copyfile(sibling, args.out / sibling.name)
    (args.out / "main.patched.bin").write_bytes(out)
    (args.out / "hooks.json").write_text(json.dumps(manifest, indent=2)+"\n", encoding="utf8")
    print(f"Added {len(hooks)} static hooks; regenerate embedding from {args.out}")
    return 0

if __name__ == "__main__": raise SystemExit(main())
