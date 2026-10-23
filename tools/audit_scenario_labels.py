"""Compare original and patched scenario control labels without changing data."""
from pathlib import Path
import argparse
import json
import re

from biko3_assets import legacy_entries, apply_resource_patch


def audit(data: Path) -> dict:
    patch = data / "patch.pp"
    patches = {name: (offset, size) for name, offset, size in legacy_entries(patch)}
    records = []
    with (data / "bk3_05.pp").open("rb") as base, patch.open("rb") as stream:
        for name, offset, size in legacy_entries(data / "bk3_05.pp"):
            base.seek(offset)
            original = bytes((-v) & 255 for v in base.read(size))
            patch_offset, patch_size = patches["bk3_05." + name]
            stream.seek(patch_offset)
            final = apply_resource_patch(original, bytes((-v) & 255 for v in stream.read(patch_size)))
            before, after = original.decode("cp932"), final.decode("gbk")
            labels_before = re.findall(r"(?m)^#[^\r\n]*", before)
            labels_after = re.findall(r"(?m)^#[^\r\n]*", after)
            records.append({"name": name, "base_labels": len(labels_before),
                            "patched_labels": len(labels_after),
                            "labels_identical": labels_before == labels_after,
                            "base_only_labels": sorted(set(labels_before) - set(labels_after)),
                            "patched_only_labels": sorted(set(labels_after) - set(labels_before)),
                            "kana_line_numbers": [number for number, line in enumerate(after.splitlines(), 1)
                                                  if re.search(r"[\u3041-\u3096\u30a1-\u30fa]", line)]})
    return {"schema": 1, "records": records, "runtime_verified": False,
            "scope": "Control labels and kana only; neither absence of kana nor matching labels proves full translation"}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path)
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    result = audit(args.data)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"scenarios": len(result["records"]),
                      "label_differences": sum(not record["labels_identical"] for record in result["records"])}))
