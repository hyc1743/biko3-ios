#!/usr/bin/env python3
"""Extract reviewed game SPIR-V and compile its two real compute pipelines.

Windows supports --extract-only. macOS additionally links and runs the native
MoltenVK probe. Pipeline creation is evidence of compilation, never rendering
or correct kernel execution. All extracted game bytes remain in --out.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import platform
import struct
import subprocess
import sys

from extract_embedded_spirv import extract, parse_module
from nro import NroError

ROOT = Path(__file__).resolve().parents[1]
EXPECTED = {
    "4fd9100445dd4fac80cf2c52fdfb405fcc6797ee1ee61d1db3ced5898bbd0c4a":
        {"role": "transfer_vertex", "bindings": [0, 1, 2], "push_size": 72},
    "4c881ab8c917782e20d774452c64cb5f3b90b3061ea6f33dd876d25f7b678e0f":
        {"role": "skin", "bindings": [0, 1, 2, 3], "push_size": 0},
}


def reflect_compute(raw: bytes) -> dict:
    """Reflect only layout forms we can prove from SPIR-V 1.0 decorations.

    Both game modules use scalar/vector/matrix buffer blocks. Unsupported
    resources or ambiguous sizes fail instead of receiving guessed layouts.
    """
    parsed = parse_module(raw)
    entries = parsed["entry_points"]
    if len(entries) != 1 or entries[0]["stage"] != "comp":
        raise NroError("expected exactly one compute entry point")
    words = struct.unpack(f"<{len(raw)//4}I", raw)
    types, constants, decorations, members, variables, names = {}, {}, {}, {}, [], {}
    capabilities = []
    index = 5
    while index < len(words):
        count, op = words[index] >> 16, words[index] & 65535
        args = list(words[index+1:index+count])
        if op == 5:
            names[args[0]] = struct.pack(f"<{len(args)-1}I", *args[1:]).split(b"\0", 1)[0].decode("utf8")
        elif op == 17:
            capabilities.append(args[0])
        elif 19 <= op <= 39:
            types[args[0]] = (op, args[1:])
        elif op == 43:
            constants[args[1]] = (args[0], args[2:])
        elif op == 59:
            variables.append(args[:3])
        elif op in (71, 72):
            if op == 71:
                destination, kind, value = decorations.setdefault(args[0], {}), args[1], args[2:]
            else:
                destination, kind, value = members.setdefault((args[0], args[1]), {}), args[2], args[3:]
            if kind in destination and destination[kind] != value:
                raise NroError("conflicting SPIR-V decoration")
            destination[kind] = value
        index += count

    def one(table, key, label):
        value = table.get(key)
        if value is None or len(value) != 1:
            raise NroError(f"missing or ambiguous {label}")
        return value[0]

    def constant(identifier):
        type_id, value = constants.get(identifier, (None, None))
        if types.get(type_id) not in ((21, [32, 0]), (21, [32, 1])) or len(value or []) != 1:
            raise NroError("descriptor/array count is not a 32-bit literal constant")
        if not value[0] or value[0] > 65536:
            raise NroError("unsupported descriptor/array count")
        return value[0]

    sizing = set()

    def extent(type_id, member=None):
        if type_id in sizing:
            raise NroError("recursive SPIR-V layout type")
        sizing.add(type_id)
        try:
            op, args = types.get(type_id, (None, []))
            if op in (21, 22):
                if args[0] != 32:
                    raise NroError("unsupported non-32-bit block scalar")
                return 4
            if op == 23:
                return extent(args[0]) * args[1]
            if op == 24:
                if member is None:
                    raise NroError("matrix has no member stride/major decoration")
                stride = one(member, 7, "MatrixStride")
                vector_op, vector_args = types.get(args[0], (None, []))
                if vector_op != 23 or (4 in member) == (5 in member):
                    raise NroError("matrix has no unambiguous RowMajor/ColMajor layout")
                if 5 in member:
                    elements, last = args[1], extent(args[0])
                else:
                    elements, last = vector_args[1], extent(vector_args[0]) * args[1]
                if stride < last:
                    raise NroError("matrix stride is shorter than its major vector")
                return stride * (elements - 1) + last
            if op == 28:
                length = constant(args[1])
                stride = one(decorations.get(type_id, {}), 6, "ArrayStride")
                last = extent(args[0], member)
                if stride < last:
                    raise NroError("array stride is shorter than element extent")
                return stride * (length - 1) + last
            if op == 30:
                last_end = 0
                for i, child in enumerate(args):
                    layout = members.get((type_id, i), {})
                    offset = one(layout, 35, "member Offset")
                    size = extent(child, layout)
                    if offset < last_end:
                        raise NroError("overlapping or unordered block members")
                    last_end = offset + size
                return last_end
            raise NroError(f"unsupported push-constant type opcode {op}")
        finally:
            sizing.remove(type_id)

    bindings, pushes = [], []
    for pointer, identifier, storage in variables:
        if storage not in (0, 2, 9, 12):
            continue
        pointer_op, pointer_args = types.get(pointer, (None, []))
        if pointer_op != 32 or pointer_args[0] != storage:
            raise NroError("resource pointer storage-class mismatch")
        child = pointer_args[1]
        if storage == 9:
            if types.get(child, (None, []))[0] != 30 or 2 not in decorations.get(child, {}):
                raise NroError("push constants must point to a Block struct")
            size = extent(child)
            if not size or size % 4:
                raise NroError("push-constant extent is not a nonzero multiple of four")
            pushes.append({"offset": 0, "size": size,
                           "members": [{"offset": one(members.get((child, i), {}), 35, "Offset"),
                                        "size": extent(t, members.get((child, i), {}))}
                                       for i, t in enumerate(types[child][1])]})
            continue
        descriptor_count = 1
        child_op, child_args = types.get(child, (None, []))
        while child_op == 28:
            descriptor_count *= constant(child_args[1])
            child = child_args[0]
            child_op, child_args = types.get(child, (None, []))
        if child_op != 30 or storage not in (2, 12):
            raise NroError("unsupported resource: only explicit buffer blocks are implemented")
        block = decorations.get(child, {})
        if storage == 2 and 3 in block and 2 not in block:
            descriptor_type = 7  # VK_DESCRIPTOR_TYPE_STORAGE_BUFFER / BufferBlock
        elif storage == 2 and 2 in block and 3 not in block:
            descriptor_type = 6  # VK_DESCRIPTOR_TYPE_UNIFORM_BUFFER / Block
        elif storage == 12 and 2 in block and 3 not in block:
            descriptor_type = 7
        else:
            raise NroError("missing or ambiguous buffer Block/BufferBlock decoration")
        variable = decorations.get(identifier, {})
        descriptor_set = one(variable, 34, "DescriptorSet")
        binding = one(variable, 33, "Binding")
        if descriptor_set != 0:
            raise NroError("this probe supports only the observed descriptor set zero")
        bindings.append({"set": descriptor_set, "binding": binding, "descriptor_type": descriptor_type,
                         "count": descriptor_count, "name": names.get(identifier, ""),
                         "struct_type_id": child, "storage_class": storage})
    bindings.sort(key=lambda value: (value["set"], value["binding"]))
    if len({(b["set"], b["binding"]) for b in bindings}) != len(bindings):
        raise NroError("duplicate descriptor binding")
    if len(pushes) > 1:
        raise NroError("multiple push-constant blocks are unsupported")
    modes = [m for m in parsed["execution_modes"] if m["entry_id"] == entries[0]["function_id"] and m["mode"] == 17]
    if len(modes) != 1 or len(modes[0]["operands"]) != 3:
        raise NroError("missing literal compute LocalSize")
    return {"entry_point": entries[0]["name"], "stage": "comp", "bindings": bindings,
            "push_constants": pushes, "local_size": modes[0]["operands"], "capabilities": capabilities}


def prepare_inputs(nro: Path, output: Path) -> dict:
    output.mkdir(parents=True, exist_ok=False)
    manifest = extract(nro.read_bytes(), output / "shaders")
    cases = []
    for module in manifest["modules"]:
        if module["entry_points"][0]["stage"] != "comp":
            continue
        path = (output / "shaders" / module["path"]).resolve()
        raw = path.read_bytes()
        sha = hashlib.sha256(raw).hexdigest()
        if sha not in EXPECTED:
            raise NroError("unreviewed compute module hash")
        layout = reflect_compute(raw)
        expected = EXPECTED[sha]
        if (layout["entry_point"] != "main" or layout["local_size"] != [64, 1, 1] or
            [b["binding"] for b in layout["bindings"]] != expected["bindings"] or
            any(b["descriptor_type"] != 7 or b["count"] != 1 for b in layout["bindings"]) or
            sum(p["size"] for p in layout["push_constants"]) != expected["push_size"]):
            raise NroError("compute reflection differs from the reviewed game layout")
        cases.append({"path": str(path), "sha256": sha, "role": expected["role"], **layout})
    if len(cases) != 2 or {case["sha256"] for case in cases} != set(EXPECTED):
        raise NroError("both original compute modules must be present exactly once")
    header = ["/* Generated path/hash/layout metadata only; no embedded game code. */"]
    for i, case in enumerate(cases):
        header.append(f"static const ComputeBinding bindings_{i}[] = {{")
        for binding in case["bindings"]:
            header.append("    {%d, %d, %d, %d}," % (binding["set"], binding["binding"], binding["descriptor_type"], binding["count"]))
        header.append("};")
    header.append("static const ComputeCase compute_cases[] = {")
    for i, case in enumerate(cases):
        strings = ", ".join(json.dumps(case[key]) for key in ("path", "sha256", "role", "entry_point"))
        header.append("    {%s, bindings_%d, %d, %d, {%s}}," % (
            strings, i, len(case["bindings"]), sum(p["size"] for p in case["push_constants"]),
            ", ".join(str(x) for x in case["local_size"])))
    header.append("};")
    (output / "game_compute_inputs.h").write_text("\n".join(header)+"\n", encoding="utf8")
    result = {"schema": 1, "source_nro_sha256": manifest["source_nro_sha256"],
              "extracted_module_count": manifest["module_count"], "compute_cases": cases,
              "verified": False, "status": "extracted_only",
              "scope": "Actual game compute shader pipeline compilation; no kernel dispatch or game rendering"}
    (output / "inputs.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf8")
    return result


def run_native(moltenvk: Path, output: Path, logs: Path, result: dict) -> dict:
    if platform.system() != "Darwin":
        raise NroError("native pipeline probe requires macOS; use --extract-only on this host")
    library = moltenvk / "static/MoltenVK.xcframework/macos-arm64_x86_64/libMoltenVK.a"
    if not library.is_file() or not (moltenvk / "include/vulkan/vulkan.h").is_file():
        raise NroError("--moltenvk-root must contain the macOS static xcframework and include directory")
    logs.mkdir(parents=True, exist_ok=True)
    executable = output / "game-compute-smoke"
    command = ["xcrun", "--sdk", "macosx", "clang", "-std=c11", "-O1",
               "-Werror=implicit-function-declaration", "-I", str(moltenvk / "include"), "-I", str(output),
               str(ROOT / "ios/runtime/Biko3Vulkan.c"), str(ROOT / "tests/native/game_compute_smoke.c"),
               str(library), "-lc++"]
    for framework in ("Metal", "QuartzCore", "IOSurface", "Foundation", "AppKit", "CoreGraphics", "IOKit"):
        command += ["-framework", framework]
    command += ["-o", str(executable)]
    result["build_command"] = command
    with (logs / "game-compute-build.log").open("w", encoding="utf8") as log:
        built = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT, timeout=180)
    if built.returncode:
        raise NroError("native game-compute probe build failed; see game-compute-build.log")
    with (logs / "game-compute-run.log").open("w", encoding="utf8") as log:
        native = subprocess.run([str(executable)], stdout=log, stderr=subprocess.STDOUT, timeout=180)
    text = (logs / "game-compute-run.log").read_text(encoding="utf8", errors="replace")
    result["native_exit_code"] = native.returncode
    compiled = [json.loads(line[len("BIKO3_COMPUTE_RESULT "):]) for line in text.splitlines()
                if line.startswith("BIKO3_COMPUTE_RESULT ")]
    result["pipeline_results"] = compiled
    if native.returncode == 77:
        reasons = [line[len("BIKO3_COMPUTE_SKIP "):] for line in text.splitlines()
                   if line.startswith("BIKO3_COMPUTE_SKIP ")]
        if (len(reasons) != 1 or reasons[0] not in {"incompatible_vulkan_driver", "zero_physical_devices", "no_graphics_compute_queue"}
                or compiled):
            raise NroError("exit 77 lacks an explicit no-GPU reason")
        result.update(status="skipped", verified=False, reason="runner_gpu_unavailable", native_skip_reason=reasons[0])
    elif native.returncode:
        raise NroError("real game-compute shader/pipeline creation failed; see game-compute-run.log")
    elif (len(compiled) != 2 or {case.get("sha256") for case in compiled} != set(EXPECTED) or
          any(case.get("stage") != "comp" or case.get("entry_point") != "main" or
              case.get("role") != EXPECTED.get(case.get("sha256"), {}).get("role") or
              case.get("pipeline_created") is not True for case in compiled)):
        raise NroError("native probe did not prove both original compute pipelines were created")
    else:
        result.update(status="passed", verified=True)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--nro", type=Path, required=True)
    parser.add_argument("--moltenvk-root", type=Path)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--logs", type=Path)
    parser.add_argument("--extract-only", action="store_true")
    args = parser.parse_args(argv)
    if not args.extract_only and args.moltenvk_root is None:
        parser.error("--moltenvk-root is required unless --extract-only is selected")
    output = args.out.resolve()
    if output.exists():
        parser.error("--out must be a fresh directory; previous evidence is never overwritten")
    result = {"status": "failed", "verified": False}
    try:
        result = prepare_inputs(args.nro, output)
        if not args.extract_only:
            result = run_native(args.moltenvk_root.resolve(), output, (args.logs or output / "logs").resolve(), result)
    except (NroError, OSError, subprocess.SubprocessError, ValueError) as error:
        result.update(status="failed", verified=False, error=str(error))
        if output.exists():
            (output / "result.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf8")
        print(str(error), file=sys.stderr)
        return 1
    (output / "result.json").write_text(json.dumps(result, indent=2)+"\n", encoding="utf8")
    print(json.dumps({"status": result["status"], "verified": result["verified"],
                      "result": str(output / "result.json")}, indent=2))
    return 77 if result["status"] == "skipped" else 0


if __name__ == "__main__": raise SystemExit(main())
