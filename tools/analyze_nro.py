"""Generate reproducible offline observations; never execute the guest image."""
from __future__ import annotations

import argparse
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import struct

from nro import Nro, NroError


CATEGORIES = {
    "identity": r"biko3|development build|development preview|native port bring",
    "platform": r"sdmc:|save:/|romfs:|libnx|audout|Horizon runtime|Save data .*commit",
    "renderer": r"Renderer ready|explicit Vulkan|Vulkan device:|skin palette|GPU skin",
    "flow": r"Scene switched|Flow load target|Original menu scene|Checkpoint menu|Story ending|Gallery ending",
    "resources": r"%s/Data|\.pp\b|\.b3f\b|XAN|AVI texture",
}


def analyze(image: Nro) -> dict:
    strings = []
    rodata = image.segments[1]
    for match in re.finditer(rb"[\x20-\x7e]{6,}", image.data[rodata.offset:rodata.end]):
        value = match.group().decode("ascii")
        categories = [name for name, pattern in CATEGORIES.items() if re.search(pattern, value, re.I)]
        if categories:
            strings.append({"address": rodata.offset + match.start(), "value": value,
                            "categories": categories})
    anchors = {s["address"] for s in strings}
    references, syscalls, system_registers = [], [], []
    text = image.segments[0]
    # Decode only specific instruction encodings. ADRP+ADD pairs are candidates:
    # intervening register writes/control flow are not modeled here.
    for offset in range(0x80, text.end - 3, 4):
        word = struct.unpack_from("<I", image.data, offset)[0]
        if word & 0xFFE0001F == 0xD4000001:
            syscalls.append({"address": offset, "immediate": (word >> 5) & 0xFFFF})
        if word & 0xFFF00000 == 0xD5300000 or word & 0xFFF00000 == 0xD5100000:
            system_registers.append({"address": offset, "word": f"{word:08x}"})
        if word & 0x9F000000 != 0x90000000:
            continue
        register = word & 31
        if register == 31:
            continue
        imm = ((word >> 29) & 3) | (((word >> 5) & 0x7FFFF) << 2)
        if imm & (1 << 20):
            imm -= 1 << 21
        page = (offset & ~0xFFF) + (imm << 12)
        for following in range(offset + 4, min(offset + 32, text.end - 3), 4):
            add = struct.unpack_from("<I", image.data, following)[0]
            if add & 0xFF800000 == 0x91000000 and (add >> 5) & 31 == register:
                target = page + (((add >> 10) & 0xFFF) << (12 if add & (1 << 22) else 0))
                if target in anchors:
                    references.append({"adrp": offset, "add": following, "string_address": target,
                                       "confidence": "candidate"})
    entry_disassembly = None
    try:
        from capstone import Cs, CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN
        disassembler = Cs(CS_ARCH_ARM64, CS_MODE_LITTLE_ENDIAN)
        end = min(image.entry + 0x70, text.end)
        entry_disassembly = [
            {"address": i.address, "mnemonic": i.mnemonic, "operands": i.op_str}
            for i in disassembler.disasm(image.data[image.entry:end], image.entry)
        ]
    except ImportError:
        pass
    relocations = image.relr()
    return {
        "schema": 1, "observed_at": datetime.now(timezone.utc).isoformat(),
        "authority": "local-file-static-analysis", "sha256": image.sha256,
        "file_size": len(image.data), "image_size": image.image_size, "memory_size": image.memory_size,
        "architecture": "AArch64", "entry": image.entry, "build_id": image.build_id,
        "segments": [asdict(s) for s in image.segments], "bss_size": image.bss_size,
        "mod0": image.mod0, "dynamic": image.dynamic_summary(),
        "relr": {"count": len(relocations), "file": "relocations.json"},
        "strings": strings, "candidate_string_references": references,
        "svc_candidates": syscalls, "system_register_candidates": system_registers,
        "entry_disassembly": entry_disassembly,
        "limitations": [
            "Original source and full function boundaries are not recovered.",
            "String contents describe the image; their code paths may be unused or outdated.",
            "Opcode scans include aligned data in the text segment; reachability is unknown.",
            "ADRP+ADD candidates do not model register clobbers or control flow; confirm in a disassembler.",
            "The ELF retains original addresses, bytes, and RELR metadata; no relocations are applied.",
            "No game was executed and no iOS compatibility or performance was measured.",
            "This is shell-tool evidence, not a REA decompilation result.",
        ],
    }


def resource_inventory(root: Path) -> dict:
    counts, sizes = Counter(), Counter()
    files = 0
    for path in root.rglob("*"):
        if path.is_file():
            extension = path.suffix.lower() or "<none>"
            counts[extension] += 1
            sizes[extension] += path.stat().st_size
            files += 1
    return {"root": str(root), "file_count": files, "bytes": sum(sizes.values()),
            "extensions": [{"extension": e, "count": counts[e], "bytes": sizes[e]}
                           for e in sorted(counts)]}


def markdown(report: dict) -> str:
    dynamic = report["dynamic"]
    lines = ["# Biko3 NRO 静态分析", "", "这是迁移前置分析，不代表 iOS 移植已完成。", "",
             f"- SHA-256：`{report['sha256']}`",
             f"- 映像：AArch64，入口 `{report['entry']:#x}`，文件 {report['file_size']:,} 字节。",
             f"- RELR：{report['relr']['count']:,} 个目标；完整记录见 `relocations.json`。",
             f"- 动态符号项（含空项）：{dynamic['symbol_count_including_null']}；具名项：{len(dynamic['named_symbols'])}。",
             f"- SVC 指令候选：{len(report['svc_candidates'])}；系统寄存器指令候选：{len(report['system_register_candidates'])}。",
             "", "## 段", "", "| 段 | 起始地址 | 文件大小 | 权限 |", "|---|---:|---:|---|"]
    for seg in report["segments"]:
        lines.append(f"| {seg['name']} | `{seg['offset']:#x}` | `{seg['size']:#x}` | {seg['permissions']} |")
    lines.extend(["", "## 字符串锚点", "", "原文可能包含旧提示或诊断路径，不能当作运行时完成度结论。", ""])
    for category in CATEGORIES:
        lines.extend([f"### {category}", ""])
        matches = [s for s in report["strings"] if category in s["categories"]]
        for item in matches[:12]:
            lines.append(f"- `{item['address']:#x}`：{item['value'].replace('`', chr(39))[:350]}")
        if len(matches) > 12:
            lines.append(f"- 其余 {len(matches) - 12} 条见 `analysis.json`。")
        lines.append("")
    lines.extend(["## 证据限制", ""] + [f"- {x}" for x in report["limitations"]] + [""])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("image", type=Path)
    parser.add_argument("--output", type=Path, default=Path("work/biko3-ios/evidence/nro"))
    parser.add_argument("--resources", type=Path)
    args = parser.parse_args()
    try:
        image = Nro(args.image.read_bytes())
        report = analyze(image)
        if args.resources:
            if not args.resources.is_dir():
                raise NroError("resource directory does not exist")
            report["resources"] = resource_inventory(args.resources)
        generated = {"analysis.json": (json.dumps(report, indent=2, ensure_ascii=False) + "\n").encode("utf-8"),
                     "relocations.json": (json.dumps(image.relr(), indent=2) + "\n").encode(),
                     "report.md": markdown(report).encode("utf-8"),
                     "biko3-analysis.elf": image.to_elf()}
        source = args.image.resolve()
        for name in generated:
            if (args.output / name).resolve() == source:
                raise NroError("refusing to overwrite the source image")
        args.output.mkdir(parents=True, exist_ok=True)
        for name, data in generated.items():
            (args.output / name).write_bytes(data)
        manifests = [{"file": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest()}
                     for name, data in generated.items()]
        (args.output / "manifest.json").write_text(json.dumps(manifests, indent=2) + "\n", encoding="utf-8")
        print(f"Analysis written to {args.output}; input SHA-256 {image.sha256}")
        print(f"Entry {image.entry:#x}; RELR targets {report['relr']['count']}; no guest execution")
        return 0
    except (OSError, NroError) as error:
        parser.exit(1, f"Analysis failed: {error}\n")


if __name__ == "__main__":
    raise SystemExit(main())
