"""Extract six hash-bound Biko3 SPIR-V modules using static create-info sizes.

Lengths come from ARM64 immediate stores, never padding or the first FunctionEnd.
This is a bounded static inventory, not runtime validation or all game shaders.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import struct

from capstone import Cs, CS_ARCH_ARM64, CS_MODE_ARM
from capstone.arm64 import ARM64_OP_IMM, ARM64_OP_MEM
from nro import NroError
from prepare_nxnative import recover_eh_frame, validated_nro

NRO_SHA256 = "31320a8ce44f8e1826fc90ed5b26cb714c551413b286f8c69adb03f9e8b07178"
MAGIC = 0x07230203
STAGES = {0: "vert", 1: "tesc", 2: "tese", 3: "geom", 4: "frag", 5: "comp"}
# source, size MOV, size STR, pointer expression instruction chain, pointer STR,
# create-info stack offset, call site, role inferred from static symbols/assertions.
PROFILES = [
    (0x6B8B90, 0xD1B98, 0xD1BA0, [0xD18D8, 0xD1B18, 0xD1BA4], 0xD1BA8, 0x2C0, 0xD1BB0, "vert"),
    (0x6B9120, 0xD1BB8, 0xD1BCC, [0xD18D8, 0xD1B18, 0xD1BBC], 0xD1BD4, 0x2C0, 0xD1BD8, "frag"),
    (0x6B93C0, 0xD1C0C, 0xD1C1C, [0xD18D8, 0xD1C20, 0xD1C24], 0xD1C28, 0x2C0, 0xD1C30, "lit_frag"),
    (0x6BA330, 0xCDDF0, 0xCDDF8, [0xCDDFC, 0xCDE00], 0xCDE0C, 0xB0, 0xCDE18, "transfer_vertex"),
    (0x6BB7C0, 0xD19F8, 0xD1A00, [0xD1A04, 0xD1A08], 0xD1A10, 0x5F8, 0xD1A24, "skin"),
    (0x6BD030, 0xD1BE0, 0xD1BF0, [0xD1BF4, 0xD1BF8], 0xD1BFC, 0x2C0, 0xD1C04, "lit_vert"),
]
ARGUMENT_SETUP = {
    0xD1BB0: [0xD1B88], 0xD1BD8: [0xD1BC8], 0xD1C30: [0xD1C18],
    0xCDE18: [0xCDDDC], 0xD1A24: [0xD1A14, 0xD1A18], 0xD1C04: [0xD1BEC],
}


def sha(raw):
    return hashlib.sha256(raw).hexdigest()


def string_operand(words):
    raw = struct.pack(f"<{len(words)}I", *words)
    if b"\0" not in raw:
        raise NroError("unterminated SPIR-V string")
    return raw.split(b"\0", 1)[0].decode("utf8")


def parse_module(raw: bytes) -> dict:
    if len(raw) < 20 or len(raw) % 4:
        raise NroError("SPIR-V size is not a complete word-aligned module")
    words = list(struct.unpack(f"<{len(raw)//4}I", raw))
    if words[0] != MAGIC or words[1] != 0x10000 or not words[3] or words[4]:
        raise NroError("unsupported SPIR-V header")
    i, depth, function_count, last_op = 5, 0, 0, None
    entries, names, decorations, variables, types, modes = [], {}, {}, [], {}, []
    instruction_count = 0
    while i < len(words):
        header = words[i]
        count, opcode = header >> 16, header & 0xFFFF
        if header == MAGIC or not count or i + count > len(words):
            raise NroError(f"invalid SPIR-V instruction boundary at word {i}")
        args = words[i+1:i+count]
        if opcode == 54:
            if depth or len(args) != 4:
                raise NroError("invalid nested SPIR-V function")
            depth, function_count = 1, function_count + 1
        elif opcode == 56:
            if depth != 1 or args:
                raise NroError("unmatched SPIR-V FunctionEnd")
            depth = 0
        elif opcode == 15:
            if len(args) < 3:
                raise NroError("invalid EntryPoint")
            entries.append({"execution_model": args[0], "stage": STAGES.get(args[0], str(args[0])),
                            "function_id": args[1], "name": string_operand(args[2:])})
        elif opcode == 16:
            modes.append({"entry_id": args[0], "mode": args[1], "operands": args[2:]})
        elif opcode == 5:
            names[args[0]] = string_operand(args[1:])
        elif opcode == 71:
            decorations.setdefault(args[0], {})[args[1]] = args[2:]
        elif opcode == 59:
            if len(args) < 3:
                raise NroError("invalid Variable")
            variables.append({"type_id": args[0], "id": args[1], "storage_class": args[2]})
        elif 19 <= opcode <= 39 and args:
            types[args[0]] = {"opcode": opcode, "operands": args[1:]}
        instruction_count += 1
        last_op = opcode
        i += count
    if depth or not function_count or last_op != 56 or not entries:
        raise NroError("incomplete SPIR-V function/module boundary")
    resources = []
    for variable in variables:
        dec = decorations.get(variable["id"], {})
        if variable["storage_class"] not in (0, 2, 9, 12):
            continue
        record = dict(variable, name=names.get(variable["id"], ""))
        record["decorations"] = {str(k): v for k, v in dec.items()}
        if 33 in dec: record["binding"] = dec[33][0]
        if 34 in dec: record["descriptor_set"] = dec[34][0]
        pointer = types.get(variable["type_id"], {})
        if pointer.get("opcode") == 32:
            record["pointee_type_id"] = pointer["operands"][1]
            record["pointee_name"] = names.get(pointer["operands"][1], "")
        resources.append(record)
    return {"version": "1.0", "generator_word": words[2], "id_bound": words[3],
            "entry_points": entries, "execution_modes": modes,
            "function_count": function_count, "instruction_count": instruction_count,
            "resources_declared": resources, "debug_names": list(names.values()),
            "validation": "Instruction boundaries, function nesting and selected declarations only; no spirv-val or runtime validation."}


def reference_evidence(data: bytes, profile) -> dict:
    offset, size_at, size_store, chain, pointer_store, base, call_at, role = profile
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    md.detail = True
    instructions = {}
    def instruction(at):
        ins = next(md.disasm(data[at:at+4], at), None)
        if ins is None:
            raise NroError(f"cannot decode reference instruction {at:#x}")
        instructions[at] = {"offset": at, "bytes": data[at:at+4].hex(), "assembly": f"{ins.mnemonic} {ins.op_str}"}
        return ins
    mov = instruction(size_at)
    if mov.mnemonic != "mov" or mov.operands[1].type != ARM64_OP_IMM:
        raise NroError("codeSize is not a verified immediate MOV")
    size, size_register = mov.operands[1].imm, mov.operands[0].reg
    def store(at, expected_register, displacement):
        ins = instruction(at)
        if (ins.mnemonic != "str" or ins.operands[0].reg != expected_register
                or ins.operands[1].type != ARM64_OP_MEM
                or ins.reg_name(ins.operands[1].mem.base) != "sp"
                or ins.operands[1].mem.disp != displacement):
            raise NroError("create-info field store does not match reviewed ABI")
    store(size_store, size_register, base + 24)
    register, value = None, None
    for at in chain:
        ins = instruction(at)
        if register is None:
            if ins.mnemonic != "adrp" or ins.operands[1].type != ARM64_OP_IMM:
                raise NroError("shader pointer does not begin with ADRP")
            register, value = ins.operands[0].reg, ins.operands[1].imm
        else:
            if (ins.mnemonic != "add" or ins.operands[1].reg != register
                    or ins.operands[2].type != ARM64_OP_IMM):
                raise NroError("shader pointer ADD chain changed")
            register, value = ins.operands[0].reg, value + ins.operands[2].imm
    if value != offset:
        raise NroError("pCode reference does not identify the candidate")
    store(pointer_store, register, base + 32)
    call = instruction(call_at)
    if call.mnemonic != "bl" or call.operands[0].imm != 0xD2760:
        raise NroError("unexpected shader creation wrapper")
    # The actual x1 argument points to the same stack structure whose +24/+32
    # fields were recovered above; one caller uses add x0,sp; mov x1,x0.
    if call_at in ARGUMENT_SETUP:
        register = None
        for at in ARGUMENT_SETUP[call_at]:
            ins = instruction(at)
            if register is None:
                if (ins.mnemonic != "add" or ins.reg_name(ins.operands[1].reg) != "sp"
                        or ins.operands[2].imm != base):
                    raise NroError("create-info argument base changed")
            elif ins.mnemonic != "mov" or ins.operands[1].reg != register:
                raise NroError("create-info argument move changed")
            register = ins.operands[0].reg
        if ins.reg_name(register) != "x1":
            raise NroError("create-info is not passed in x1")
    return {"size_bytes": size, "pCode_virtual_offset": value, "create_info_sp_offset": base,
            "codeSize_field_offset": 24, "pCode_field_offset": 32,
            "wrapper_virtual_offset": 0xD2760, "role_inferred": role,
            "instructions": [instructions[k] for k in sorted(instructions)]}


def extract(data: bytes, out: Path) -> dict:
    if sha(data) != NRO_SHA256:
        raise NroError("NRO differs from the reviewed static reference profile")
    image = validated_nro(data)
    functions = recover_eh_frame(image)["functions"]
    out.mkdir(parents=True, exist_ok=False)
    records = []
    for profile in PROFILES:
        start = profile[0]
        evidence = reference_evidence(data, profile)
        size = evidence["size_bytes"]
        raw = data[start:start+size]
        parsed = parse_module(raw)
        key = sha(raw)
        stage = parsed["entry_points"][0]["stage"]
        filename = f"{key[:16]}.{stage}.spv"
        (out / filename).write_bytes(raw)
        records.append({"path": filename, "source_range": {"start": start, "end": start+size, "size": size},
                        "sha256": key, "nxnative_key": key[:16], "static_reference": evidence,
                        "containing_function": next(f for f in functions if f["start"] <= profile[6] < f["start"]+f["size"]),
                        **parsed})
        # Preserve original instructions around each reference and its entire FDE.
    md = Cs(CS_ARCH_ARM64, CS_MODE_ARM)
    md.detail = True
    for start, end in [(0xCD720, 0xCDFE4), (0xD0A20, 0xD23A8), (0xD2760, 0xD276C)]:
        (out / f"references-{start:x}.asm").write_text("\n".join(
            f"{i.address:08x} {i.bytes.hex():8} {i.mnemonic} {i.op_str}" for i in md.disasm(data[start:end], start)) + "\n", encoding="utf8")
    assertions = []
    for address in [0x604560, 0x6055D0, 0x605608, 0x605640, 0x605678]:
        assertions.append({"offset": address, "string": data[address:data.index(0, address)].decode()})
    result = {"schema": 1, "source_nro_sha256": image.sha256,
              "extractor_sha256": sha(Path(__file__).read_bytes()),
              "status": "six_statically_referenced_modules_extracted",
              "module_count": len(records), "runtime_verified": False, "complete_inventory": False,
              "classification": "Game renderer candidates: explicit shader creation assertion strings and Transform/Lighting/fog/skin debug names support this inference; not attributed merely from magic bytes.",
              "assertion_strings": assertions, "modules": records,
              "limits": ["No game execution, shader compiler, spirv-val, Metal tool or upstream script was executed.",
                         "Only six reviewed embedded modules are covered; dynamic/resource shaders may exist.",
                         "Two modules declare compute entry points. Current upstream vk_shaders.py skips every stage except vert/frag; its Vulkan host also lacks compute binding support.",
                         "Resource declarations describe SPIR-V metadata, not proven active bindings or Metal reflection."]}
    (out / "manifest.json").write_text(json.dumps(result, indent=2), encoding="utf8")
    (out / "assessment.md").write_text("# Biko3 embedded shader extraction\n\nSix embedded modules have exact sizes corroborated by ARM64 VkShaderModuleCreateInfo field stores and complete SPIR-V instruction boundaries. The fifth is 6256 bytes; it ends directly before the sixth module without padding.\n\nThe renderer creation code passes codeSize/pCode at create-info offsets 24/32 and calls 0xd2760, which jumps through device slot 0x5a8. Original FDE disassemblies and all evidence offsets are retained. Renderer assertion strings identify vert/frag/lit_vert/lit_frag. Transform, lighting, fog and skin names support attribution to the game renderer rather than Mesa internal shaders; this remains static inference.\n\nThere are two vertex, two fragment and two compute modules. Compute shaders include transfer_vertex and skin/palette operations. The reviewed upstream dm/gfx/tools/vk_shaders.py:83 skips compute; dm/gfx/vk/hle_vk.m:1006 does not support compute binding. A complete port needs that path, even if the four graphics shaders translate successfully.\n\nThese are six static candidates, not the shader inventory and not a first-frame result. No upstream translator or Metal tool was executed.\n", encoding="utf8")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("nro", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    args = parser.parse_args(argv)
    result = extract(args.nro.read_bytes(), args.out)
    print(json.dumps({"status": result["status"], "modules": [
        {"offset": hex(m["source_range"]["start"]), "size": m["source_range"]["size"], "stage": m["entry_points"][0]["stage"], "sha256": m["sha256"]} for m in result["modules"]]}, indent=2))


if __name__ == "__main__":
    main()
