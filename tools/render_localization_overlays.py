"""Replace Japanese captions inside reviewed rectangles, retaining UI icons."""
from pathlib import Path
import argparse
import hashlib
import json
from PIL import Image, ImageDraw, ImageFont

SPECS = {
    "hs_18.tga": [("通常", (48, 4, 110, 44), "clear", (255, 120, 35), 26)],
    "hs_19.tga": [("通常", (107, 9, 160, 44), "clear", (110, 140, 235), 17)],
    "hs_20.tga": [("通常", (0, 9, 49, 44), "clear", (110, 140, 235), 17)],
    "hs_24.tga": [("随机", (107, 8, 160, 48), "clear", (110, 140, 235), 17)],
    "hs_25.tga": [("无", (110, 9, 153, 44), "clear", (110, 140, 235), 20)],
    "hs_26.tga": [("随机", (0, 8, 49, 48), "clear", (110, 140, 235), 17)],
    "hs_27.tga": [("无", (6, 9, 46, 44), "clear", (110, 140, 235), 20),
                   ("随机", (48, 0, 109, 48), "clear", (255, 120, 35), 22)],
    "hs_09.tga": [("旋转", (25, 14, 73, 38), "row", (30, 40, 245), 18)],
    "hs_105.tga": [("拔出", (11, 14, 60, 49), "row", (255, 25, 5), 21),
                    ("插入", (411, 14, 460, 49), "row", (255, 25, 5), 21)],
    **{f"ex_{i}.tga": [("即时演出", (6, 130, 201, 155), "dark", (255, 110, 25), 23)]
       for i in range(25, 30)},
}


def render(source, destination, specs, font_path):
    with Image.open(source) as image:
        original = image.convert("RGBA")
    result = original.copy()
    for text, (left, top, right, bottom), mode, color, font_size in specs:
        draw = ImageDraw.Draw(result)
        for y in range(top, bottom):
            fill = ((0, 0, 0, 0) if mode == "clear" else
                    original.getpixel((left - 2, y)) if mode == "row" else (15, 15, 15, 230))
            draw.line((left, y, right - 1, y), fill=fill)
        font = ImageFont.truetype(str(font_path), font_size)
        try:
            font.set_variation_by_axes([700])
        except (AttributeError, OSError):
            pass
        bounds = draw.textbbox((0, 0), text, font=font, stroke_width=1)
        x = (left + right - bounds[2] + bounds[0]) / 2 - bounds[0]
        y = (top + bottom - bounds[3] + bounds[1]) / 2 - bounds[1]
        draw.text((x, y), text, font=font, fill=color, stroke_width=1, stroke_fill="white")
    result.save(destination)
    with Image.open(destination) as image:
        check = image.convert("RGBA")
        if check.size != original.size:
            raise ValueError("overlay dimensions changed")
        for y in range(original.height):
            for x in range(original.width):
                if not any(a <= x < c and b <= y < d for _, (a, b, c, d), *_ in specs):
                    if check.getpixel((x, y)) != original.getpixel((x, y)):
                        raise ValueError("pixels outside caption rectangles changed")
    return {"archive": "bk3_00", "name": source.name, "file": destination.name,
            "effective_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "replacement_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            "translations": [s[0] for s in specs], "rectangles": [s[1] for s in specs],
            "visual_review": "pending", "runtime_verified": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--font", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    records = [render(args.source / name, args.output / name, specs, args.font)
               for name, specs in SPECS.items()]
    (args.output / "replacements.json").write_text(json.dumps({"schema": 1, "replacements": records},
                                                               ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"rendered": len(records), "runtime_verified": False}))
