"""Redraw remaining Japanese album notices without modifying character names."""
from pathlib import Path
import argparse
import hashlib
import json
from PIL import Image, ImageDraw, ImageFont


def font_at(path, size):
    font = ImageFont.truetype(str(path), size)
    try:
        font.set_variation_by_axes([700])
    except (AttributeError, OSError):
        pass
    return font


def centered(draw, text, font, bounds, fill, stroke, stroke_fill):
    box = draw.textbbox((0, 0), text, font=font, stroke_width=stroke)
    x = (bounds[0] + bounds[2] - box[2] + box[0]) / 2 - box[0]
    y = (bounds[1] + bounds[3] - box[3] + box[1]) / 2 - box[1]
    draw.text((x, y), text, font=font, fill=fill, stroke_width=stroke, stroke_fill=stroke_fill)


def render(source, destination, font_path):
    with Image.open(source) as image:
        original = image.convert("RGBA")
    result = original.copy()
    draw = ImageDraw.Draw(result)
    if source.name == "al_31.tga":
        if original.size != (1224, 200):
            raise ValueError("unreviewed album notice dimensions")
        rectangles = [(470, 0, 749, 65), (48, 67, 1168, 162)]
        for rectangle in rectangles:
            # The notice is a transparent overlay, not a white panel. Erase
            # old glyphs without introducing opaque rectangles behind them.
            draw.rectangle((rectangle[0], rectangle[1], rectangle[2] - 1, rectangle[3] - 1),
                           fill=(255, 255, 255, 0))
        centered(draw, "注意", font_at(font_path, 48), rectangles[0], "white", 2, "black")
        centered(draw, "游戏所在磁盘空间不足时，", font_at(font_path, 35), (48, 65, 1168, 112), "white", 2, "black")
        centered(draw, "相册功能可能无法使用。", font_at(font_path, 35), (48, 111, 1168, 160), "white", 2, "black")
        text = "注意：游戏所在磁盘空间不足时，相册功能可能无法使用。"
    else:
        rectangles = [(85, 82, 237, 142)]
        left, top, right, bottom = rectangles[0]
        # The original has a paper texture. Interpolate clear paper samples
        # immediately above/below the glyph rectangle, preserving its frame.
        for x in range(left, right):
            a, b = original.getpixel((x, top - 3)), original.getpixel((x, bottom + 3))
            for y in range(top, bottom):
                fraction = (y - top + 1) / (bottom - top + 1)
                result.putpixel((x, y), tuple(round(a[c] * (1 - fraction) + b[c] * fraction) for c in range(4)))
        draw = ImageDraw.Draw(result)
        centered(draw, "未体验", font_at(font_path, 39), rectangles[0], (245, 25, 5), 3, "white")
        text = "未体验"
    result.save(destination)
    with Image.open(destination) as image:
        check = image.convert("RGBA")
        if check.size != original.size:
            raise ValueError("notice dimensions changed")
        for y in range(original.height):
            for x in range(original.width):
                inside = any(a <= x < c and b <= y < d for a, b, c, d in rectangles)
                if not inside and check.getpixel((x, y)) != original.getpixel((x, y)):
                    raise ValueError("pixels outside notice text rectangles changed")
    return {"archive": "bk3_00", "name": source.name, "file": destination.name,
            "effective_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "replacement_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            "translation": text, "rectangles": rectangles, "visual_review": "pending", "runtime_verified": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--font", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    records = [render(args.source / name, args.output / name, args.font)
               for name in ("al_31.tga", "ex_23.tga", "ex_24.tga")]
    (args.output / "replacements.json").write_text(json.dumps({"schema": 1, "replacements": records},
                                                               ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"rendered": len(records), "runtime_verified": False}))
