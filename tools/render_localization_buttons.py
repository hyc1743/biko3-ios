"""Replace reviewed Japanese text rectangles on existing menu backgrounds."""
from pathlib import Path
import argparse
import hashlib
import json
from PIL import Image, ImageDraw, ImageFont

BUTTONS = {
    "al_11.tga": ("幻灯片播放", (23, 12, 190, 43), "blue"),
    "al_12.tga": ("幻灯片播放", (23, 12, 190, 43), "orange"),
    "al_13.tga": ("删除", (92, 11, 163, 43), "blue"),
    "al_14.tga": ("删除", (92, 11, 163, 43), "orange"),
}


def render(source, destination, text, rectangle, color, font_path):
    with Image.open(source) as image:
        original = image.convert("RGBA")
    result = original.copy()
    left, top, right, bottom = rectangle
    pixels = original.load()
    draw = ImageDraw.Draw(result)
    for y in range(top, bottom):
        # Sample clear horizontal margins so glyph shadows cannot create
        # vertical seams in the replacement background.
        a, b = pixels[left - 3, y], pixels[right + 3, y]
        for x in range(left, right):
            fraction = (x - left + 1) / (right - left + 1)
            result.putpixel((x, y), tuple(round(a[c] * (1 - fraction) + b[c] * fraction) for c in range(4)))
    draw = ImageDraw.Draw(result)
    size = 26
    while size > 8:
        font = ImageFont.truetype(str(font_path), size)
        try:
            font.set_variation_by_axes([700])
        except (AttributeError, OSError):
            pass
        bounds = draw.textbbox((0, 0), text, font=font, stroke_width=1)
        if bounds[2] - bounds[0] <= right - left - 4 and bounds[3] - bounds[1] <= bottom - top - 4:
            break
        size -= 1
    x = (left + right - bounds[2] + bounds[0]) / 2 - bounds[0]
    y = (top + bottom - bounds[3] + bounds[1]) / 2 - bounds[1]
    fill = (75, 95, 235, 255) if color == "blue" else (245, 110, 35, 255)
    draw.text((x, y), text, font=font, fill=fill, stroke_width=1, stroke_fill="white")
    result.save(destination)
    with Image.open(destination) as image:
        check = image.convert("RGBA")
        if check.size != original.size:
            raise ValueError("button dimensions changed")
        for y in range(original.height):
            for x in range(original.width):
                if not (left <= x < right and top <= y < bottom) and check.getpixel((x, y)) != pixels[x, y]:
                    raise ValueError("button pixels outside translation rectangle changed")
    return {"archive": "bk3_00", "name": source.name, "file": destination.name,
            "effective_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "replacement_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            "translation": text, "rectangle": list(rectangle), "visual_review": "pending",
            "runtime_verified": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--font", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    records = [render(args.source / name, args.output / name, text, box, color, args.font)
               for name, (text, box, color) in BUTTONS.items()]
    (args.output / "replacements.json").write_text(json.dumps({"schema": 1, "replacements": records},
                                                               ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"rendered": len(records), "visual_review": "pending"}))
