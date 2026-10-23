"""Replace nine Japanese area labels while preserving the original panel edges."""
from pathlib import Path
import argparse
import hashlib
import json
from PIL import Image, ImageDraw, ImageFont


def render(source: Path, destination: Path, number: int, font_path: Path):
    with Image.open(source) as image:
        original = image.convert("RGBA")
    if original.size != (128, 64):
        raise ValueError("unreviewed area label dimensions")
    result = original.copy()
    box = (10, 17, 118, 49)
    pixels = original.load()
    draw = ImageDraw.Draw(result)
    for y in range(box[1], box[3]):
        # Clear text with its same-row panel color, leaving round corners intact.
        draw.line((box[0], y, box[2] - 1, y), fill=pixels[5, y])
    text = f"区域{number:02d}"
    font = ImageFont.truetype(str(font_path), 25)
    bounds = draw.textbbox((0, 0), text, font=font)
    x = (128 - bounds[2] + bounds[0]) / 2 - bounds[0]
    y = (64 - bounds[3] + bounds[1]) / 2 - bounds[1]
    draw.text((x, y), text, font=font, fill=(0, 0, 0, 255))
    result.save(destination)
    with Image.open(destination) as check:
        check = check.convert("RGBA")
        assert check.size == original.size
        # Old text has a stronger alpha than the panel. Reusing that alpha
        # resurrects Japanese glyph silhouettes even after replacing RGB.
        for y in range(64):
            for x in range(128):
                if not (box[0] <= x < box[2] and box[1] <= y < box[3]):
                    assert check.getpixel((x, y)) == original.getpixel((x, y))
    return {"archive": "bk3_00", "name": source.name, "file": destination.name,
            "effective_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "replacement_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            "translation": text, "rectangle": list(box), "visual_review": "pending",
            "runtime_verified": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--font", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    rows = [render(args.source / f"lo_{73 + n}.tga", args.output / f"lo_{73 + n}.tga", n, args.font)
            for n in range(1, 10)]
    (args.output / "replacements.json").write_text(json.dumps({"schema": 1, "replacements": rows},
                                                               ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"rendered": len(rows), "runtime_verified": False}))
