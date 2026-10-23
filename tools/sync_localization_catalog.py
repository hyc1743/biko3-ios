"""Sync ios/localization/ja-zh-CN.json with the merged replacement manifest.

One catalog entry per replacement row, bound to the replacement and effective
source hashes. Character-name resources stay in the preserved list and are
never merged. The catalog records visual review and runtime evidence states;
it cannot mark runtime verification without an actual game run.
"""
from pathlib import Path
import argparse
import hashlib
import json

ROOT = Path(__file__).resolve().parents[1]
CATALOG = ROOT / "ios/localization/ja-zh-CN.json"

SCOPE = "Translate remaining Japanese only; preserve other languages and existing Chinese"
PRESERVED_NAMES = [
    "al_05.tga", "al_06.tga", "al_07.tga", "al_08.tga",
    "pr_13.tga", "pr_15.tga", "pr_18.tga", "pr_20.tga",
]
REMAINING_AUDITS = [
    "Remaining UI textures",
    "World textures",
    "Executable text",
    "Scenario source comparison",
    "Movie subtitles",
    "Runtime rendering and upgrade installation",
]


def merged_rows(replacements_json: Path) -> dict:
    manifest = json.loads(replacements_json.read_text(encoding="utf-8"))
    if manifest.get("schema") != 1:
        raise ValueError("replacement manifest schema changed")
    rows = {}
    for row in manifest["replacements"]:
        key = row["archive"] + "." + row["name"]
        if key in rows:
            raise ValueError("duplicate replacement: " + key)
        rows[key] = row
    return rows


def sync(replacements_json: Path, preserve_japanese_names: bool = True):
    rows = merged_rows(replacements_json)
    entries = []
    for key in sorted(rows):
        row = rows[key]
        name = row["name"]
        if name in PRESERVED_NAMES:
            raise ValueError("preserved character-name resource must not be replaced: " + key)
        translation = row.get("translation")
        if translation is None:
            translation = row.get("translations")
        entry = {
            "archive": row["archive"],
            "name": name,
            "effective_sha256": row["effective_sha256"],
            "replacement_sha256": row["replacement_sha256"],
            "translation": translation,
            "visual_review": row.get("visual_review", "pending"),
            "runtime_verified": bool(row.get("runtime_verified")),
        }
        entries.append(entry)
    catalog = {
        "schema": 1,
        "scope": SCOPE,
        "runtime_verified": False,
        "preserve_japanese_character_names": bool(preserve_japanese_names),
        "preserved_name_resources": list(PRESERVED_NAMES),
        "entries": entries,
        "remaining_audits": list(REMAINING_AUDITS),
    }
    return catalog


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("replacements_json", type=Path,
                        help="Merged replacements manifest, e.g. out/localization-104-v1/replacements/replacements.json")
    args = parser.parse_args(argv)
    catalog = sync(args.replacements_json)
    CATALOG.write_text(json.dumps(catalog, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"catalog": str(CATALOG), "entries": len(catalog["entries"])}, indent=2))


if __name__ == "__main__":
    main()
