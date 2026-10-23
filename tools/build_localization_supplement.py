"""Merge one or more hash-bound translation batches into a fresh patch archive."""
from pathlib import Path
import argparse
import hashlib
import json
import shutil
from biko3_assets import supplement_patch, legacy_entries

# Character-name sprites are explicitly excluded by the user's localization
# preference, including batches generated before that preference changed.
PRESERVED_NAMES = {"bk3_00." + name for name in (
    "al_01.tga", "al_02.tga", "al_03.tga", "al_04.tga", "al_05.tga", "al_06.tga",
    "al_07.tga", "al_08.tga", "al_09.tga", "al_10.tga",
    "pr_03.tga", "pr_05.tga", "pr_08.tga", "pr_10.tga", "pr_13.tga", "pr_15.tga",
    "pr_18.tga", "pr_20.tga", "pr_23.tga", "pr_25.tga")}


def contents(path):
    with path.open("rb") as source:
        records = {}
        for name, offset, size in legacy_entries(path):
            source.seek(offset)
            records[name] = source.read(size)
        return records


def build(data: Path, batches: list[Path], output: Path):
    if output.exists():
        raise ValueError("supplement output must be fresh")
    output.mkdir(parents=True)
    resources = output / "replacements"
    resources.mkdir()
    records, keys = [], set()
    for batch in batches:
        manifest = json.loads((batch / "replacements.json").read_text(encoding="utf-8"))
        if manifest.get("schema") != 1:
            raise ValueError("unsupported translation batch")
        for row in manifest["replacements"]:
            key = row["archive"] + "." + row["name"]
            if key in PRESERVED_NAMES:
                raise ValueError("character-name resources must retain their original Japanese: " + key)
            if key in keys:
                raise ValueError("duplicate replacement across batches")
            keys.add(key)
            source = (batch / row["file"]).resolve()
            if not source.is_relative_to(batch.resolve()) or not source.is_file():
                raise ValueError("translation source outside batch")
            if hashlib.sha256(source.read_bytes()).hexdigest() != row["replacement_sha256"]:
                raise ValueError("translation file hash mismatch")
            copied = dict(row, file=key)
            shutil.copyfile(source, resources / key)
            records.append(copied)
    manifest_path = resources / "replacements.json"
    manifest_path.write_text(json.dumps({"schema": 1, "replacements": records},
                                        ensure_ascii=False, indent=2), encoding="utf-8")
    patch = output / "patch.pp"
    report = supplement_patch(data, resources, manifest_path, patch)
    before, after = contents(data / "patch.pp"), contents(patch)
    if {name for name, payload in after.items() if before.get(name) != payload} != keys:
        raise ValueError("merged patch changes resources outside translation batches")
    if any(after.get(name) != payload for name, payload in before.items() if name not in keys):
        raise ValueError("existing patch content was not preserved")
    report.update(preserved_original_entries=sum(after.get(k) == v for k, v in before.items()),
                  visual_review="pending", changed_keys=sorted(keys), complete_localization=False)
    (output / "build-report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("data", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--batch", type=Path, action="append", required=True)
    args = parser.parse_args()
    print(json.dumps(build(args.data, args.batch, args.output), indent=2))
