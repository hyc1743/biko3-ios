"""Translate the title's Japanese subtitle, preserving character names/art."""
from pathlib import Path
import argparse
import hashlib
import json
from PIL import Image, ImageDraw, ImageFont

RECTANGLE = (708, 631, 1105, 674)


def render(source, destination, font_path):
    with Image.open(source) as image:
        original = image.convert("RGB")
    if original.size != (1280, 960):
        raise ValueError("unreviewed title image size")
    result = original.copy()
    left, top, right, bottom = RECTANGLE
    for x in range(left, right):
        a, b = original.getpixel((x, top - 3)), original.getpixel((x, bottom + 3))
        for y in range(top, bottom):
            t = (y - top + 1) / (bottom - top + 1)
            result.putpixel((x, y), tuple(round(a[c] * (1 - t) + b[c] * t) for c in range(3)))
    text = "即时追踪动作游戏"
    font = ImageFont.truetype(str(font_path), 29)
    try:
        font.set_variation_by_axes([700])
    except (AttributeError, OSError):
        pass
    draw = ImageDraw.Draw(result)
    box = draw.textbbox((0, 0), text, font=font, stroke_width=1)
    x = (left + right - box[2] + box[0]) / 2 - box[0]
    y = (top + bottom - box[3] + box[1]) / 2 - box[1]
    draw.text((x, y), text, font=font, fill="white", stroke_width=1, stroke_fill="black")
    result.save(destination)
    with Image.open(destination) as check:
        for y in range(original.height):
            for x in range(original.width):
                if not (left <= x < right and top <= y < bottom):
                    if check.getpixel((x, y)) != original.getpixel((x, y)):
                        raise ValueError("pixels outside subtitle changed")
    return {"archive": "bk3_00", "name": source.name, "file": destination.name,
            "effective_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "replacement_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            "translation": text, "rectangle": list(RECTANGLE),
            "visual_review": "pending", "runtime_verified": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--font", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    row = render(args.source / "op_00.bmp", args.output / "op_00.bmp", args.font)
    (args.output / "replacements.json").write_text(json.dumps({"schema": 1, "replacements": [row]},
                                                               ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"rendered": 1, "runtime_verified": False}))
