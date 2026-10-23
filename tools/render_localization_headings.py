"""Translate remaining Japanese headings while retaining non-Japanese text."""
from pathlib import Path
import argparse
import hashlib
import json
from PIL import Image, ImageDraw, ImageFont

# Insets are pixel coordinates on the original 1280x960 texture.
HEADINGS = {
    "ex_00.bmp": [("H重放  CG鉴赏", (913, 78, 1201, 136), 39, "panel")],
    "te_00.bmp": [("即时追踪动作游戏", (888, 53, 1204, 89), 23, "vertical"),
                   ("体验版", (635, 210, 804, 272), 40, "dark"),
                   ("猎人", (615, 264, 1225, 425), 85, "dark")],
    "te_03.bmp": [("即时追踪动作游戏", (711, 82, 1110, 127), 28, "vertical"),
                   ("价格未定", (822, 462, 985, 518), 34, "dark"),
                   ("还有许多其他女孩登场。", (70, 518, 497, 607), 34, "dark"),
                   ("预计冬季发售", (744, 631, 1096, 720), 45, "blue"),
                   ("未满18岁者不得购买。", (978, 786, 1243, 812), 19, "dark"),
                   ("有关尾行3的详细信息，请访问HP", (672, 817, 1212, 865), 26, "dark")],
}


def render(source, destination, specs, font_path):
    with Image.open(source) as image:
        original = image.convert("RGB")
    if original.size != (1280, 960):
        raise ValueError("unreviewed heading texture size")
    result = original.copy()
    for text, (left, top, right, bottom), size, mode in specs:
        if mode in ("panel", "blue", "dark"):
            fill = {"panel": (231, 123, 177), "blue": (25, 25, 240), "dark": (18, 18, 24)}[mode]
            ImageDraw.Draw(result).rectangle((left, top, right - 1, bottom - 1), fill=fill)
        else:
            for x in range(left, right):
                a, b = original.getpixel((x, top - 2)), original.getpixel((x, bottom + 2))
                for y in range(top, bottom):
                    t = (y - top + 1) / (bottom - top + 1)
                    result.putpixel((x, y), tuple(round(a[c] * (1 - t) + b[c] * t) for c in range(3)))
        draw = ImageDraw.Draw(result)
        while size > 8:
            font = ImageFont.truetype(str(font_path), size)
            try:
                font.set_variation_by_axes([700])
            except (AttributeError, OSError):
                pass
            bounds = draw.textbbox((0, 0), text, font=font, stroke_width=2)
            if bounds[2] - bounds[0] <= right - left - 8 and bounds[3] - bounds[1] <= bottom - top - 6:
                break
            size -= 1
        x = (left + right - bounds[2] + bounds[0]) / 2 - bounds[0]
        y = (top + bottom - bounds[3] + bounds[1]) / 2 - bounds[1]
        draw.text((x, y), text, font=font, fill="white", stroke_width=2,
                  stroke_fill=(200, 30, 65) if mode == "panel" else "black")
    result.save(destination)
    with Image.open(destination) as check:
        for y in range(960):
            for x in range(1280):
                if not any(a <= x < c and b <= y < d for _, (a, b, c, d), *_ in specs):
                    if check.getpixel((x, y)) != original.getpixel((x, y)):
                        raise ValueError("pixels outside heading rectangles changed")
    return {"archive": "bk3_00", "name": source.name, "file": destination.name,
            "effective_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "replacement_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            "translations": [s[0] for s in specs], "rectangles": [s[1] for s in specs],
            "visual_review": "pending", "runtime_verified": False,
            "preserve_japanese_character_names": True}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--font", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    rows = [render(args.source / name, args.output / name, specs, args.font) for name, specs in HEADINGS.items()]
    (args.output / "replacements.json").write_text(json.dumps({"schema": 1, "replacements": rows},
                                                               ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"rendered": len(rows), "runtime_verified": False}))
