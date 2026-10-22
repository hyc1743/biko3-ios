"""Recover guest export hooks from reviewed, hash-bound Ghidra name lookup."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import struct
from nro import NroError
from patch_guest_hooks import SOURCE_SHA

LOOKUP_SHA = "54ec412915f04908e26bd784a9ae3a4d284926e9f745e99b54388b2ae0330690"

def recover_hooks(source, assembly):
    if hashlib.sha256(source).hexdigest() != SOURCE_SHA: raise NroError("source SHA mismatch")
    # Each repeated sequence compares a literal API name, then branches to a
    # block that loads the relocated function pointer from read/write data.
    pattern = (r"adrp x1,0x([0-9a-f]+)\n0x[0-9a-f]+  add x1,x1,#0x([0-9a-f]+)"
               r"(?:\n[^\n]*){0,3}?\n0x[0-9a-f]+  bl 0x005da700\n0x[0-9a-f]+  cbz w0,0x([0-9a-f]+)")
    exports = {}
    for page, imm, target in re.findall(pattern, assembly):
        address = int(page, 16) + int(imm, 16)
        name = source[address:source.index(b"\0", address)].decode("ascii")
        block = assembly[assembly.index(f"{int(target, 16):#x}  "):]
        m = re.search(r"adrp x0,0x([0-9a-f]+)\n0x[0-9a-f]+  ldr x0,\[x0, #0x([0-9a-f]+)\]", block)
        if not m: raise NroError("unrecognized lookup result block")
        pointer = int(m[1],16) + int(m[2],16)
        function = struct.unpack_from("<Q", source, pointer)[0]
        exports[function] = {"name": name, "name_offset": address, "pointer_offset": pointer}
    if len(exports) < 700: raise NroError("incomplete Vulkan name lookup export inventory")
    # Independently analyzed renderer thunks and NN setup calls confirm these
    # global/core exports not present in the repeated lookup shape.
    for address, name in ((0xd5340,"vkCreateInstance"), (0xd23d0,"vkEnumeratePhysicalDevices"),
                          (0xd23c0,"vkDestroyInstance")):
        exports[address] = {"name":name, "evidence":"renderer Ghidra call arguments and wrapper dispatch"}
    called = {}
    # Entire game/front-end prefix ends where the Vulkan export wrapper block
    # begins. B/BL are both examined; driver-internal calls are excluded.
    for pc in range(0x80, 0xd23c0, 4):
        word = struct.unpack_from("<I", source, pc)[0]
        if word & 0x7c000000 == 0x14000000:
            imm = word & 0x3ffffff
            if imm & (1 << 25): imm -= 1 << 26
            address = pc + imm*4
            if address in exports: called.setdefault(address, []).append(pc)
    for address in (0xdb480, 0xdb3e0): called.setdefault(address, [])
    hooks = []
    for slot, address in enumerate(sorted(called, key=lambda a: (a not in (0xdb480,0xdb3e0), -a if a in (0xdb480,0xdb3e0) else a))):
        record = exports[address]
        hooks.append({"offset": address, "slot": slot, "symbol": record["name"], "kind":"vulkan",
                      "expected_original":source[address:address+16].hex(), "call_sites":called[address],
                      "export_evidence":record})
    return hooks

def main(argv=None):
    p=argparse.ArgumentParser(description=__doc__)
    p.add_argument("--nro",type=Path,required=True); p.add_argument("--assembly",type=Path,required=True)
    p.add_argument("--out",type=Path,required=True); p.add_argument("--exports",type=Path,required=True)
    args=p.parse_args(argv)
    raw=args.assembly.read_bytes()
    if LOOKUP_SHA and hashlib.sha256(raw).hexdigest()!=LOOKUP_SHA: raise NroError("lookup assembly SHA mismatch")
    hooks=recover_hooks(args.nro.read_bytes(),raw.decode("utf8"))
    result={"schema":1,"source_nro_sha256":SOURCE_SHA,"assembly_sha256":hashlib.sha256(raw).hexdigest(),
            "coverage":"All statically decoded B/BL from game prefix [0x80,0xd23c0) to mapped Vulkan exports; dynamic procedure calls use resolver hooks",
            "hooks":hooks}
    args.out.parent.mkdir(parents=True,exist_ok=True)
    args.out.write_text(json.dumps(result,indent=2)+"\n",encoding="utf8")
    special={"vkCreateViSurfaceNN"}
    args.exports.write_text("/* Generated from hash-bound game API calls; contains API names only. */\n"+
        "".join(f'BIKO3_VK_EXPORT({h["symbol"]})\n' for h in hooks if h["symbol"] not in special),encoding="utf8")
    print(f"Recovered {len(hooks)} direct Vulkan hooks including real compute commands")

if __name__=="__main__": main()
