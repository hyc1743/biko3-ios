"""Redraw reviewed Japanese dialog labels with a local Chinese font.

Uses Pillow, supplied font, and already patched UI exports. Original textures
are never changed. Outputs retain original file dimensions and alpha exactly.
"""
from pathlib import Path
import argparse
import hashlib
import json
import statistics
from PIL import Image, ImageDraw, ImageFont

DIALOGS = {
    "sy_11.tga": ("返回标题画面？", (60, 42, 580, 132)),
    "sy_12.tga": ("是否退出游戏？", (45, 42, 595, 132)),
    "sa_14.tga": ("是否保存？", (32, 23, 224, 90)),
    "lo_14.tga": ("是否读取存档？", (32, 23, 224, 90)),
    "ma_08.bmp": ("是否再试一次？", (60, 48, 580, 140)),
    "ma_09.bmp": ("是否保存？", (60, 48, 580, 140)),
}


def render(source: Path, destination: Path, text: str, box: tuple, font_path: Path):
    with Image.open(source) as image:
        original = image.convert("RGBA")
        original_mode = image.mode
    result = original.copy()
    left, top, right, bottom = box
    # These six panels have horizontally uniform scanline backgrounds. Sample
    # the clear left/right margins on each row rather than inventing a panel.
    pixels = original.load()
    draw = ImageDraw.Draw(result)
    for y in range(top, bottom):
        samples = [pixels[x, y] for x in range(25, 35)]
        samples += [pixels[x, y] for x in range(original.width - 35, original.width - 25)]
        color = tuple(int(statistics.median(pixel[c] for pixel in samples)) for c in range(4))
        draw.line((left, y, right - 1, y), fill=color)
    size = 48 if original.width == 640 else 26
    while size > 10:
        font = ImageFont.truetype(str(font_path), size)
        if hasattr(font, "get_variation_axes"):
            try:
                axes = font.get_variation_axes()
                font.set_variation_by_axes([min(axis["maximum"], max(axis["minimum"], 700))
                                            if axis["name"] == b"Weight" else axis["default"] for axis in axes])
            except OSError:
                pass
        bounds = draw.textbbox((0, 0), text, font=font, stroke_width=2)
        if bounds[2] - bounds[0] <= right - left - 10:
            break
        size -= 1
    width, height = bounds[2] - bounds[0], bounds[3] - bounds[1]
    x = (left + right - width) / 2 - bounds[0]
    y = (top + bottom - height) / 2 - bounds[1]
    draw.text((x + 2, y + 3), text, font=font, fill=(70, 70, 100), stroke_width=2)
    draw.text((x, y), text, font=font, fill=(35, 25, 245), stroke_width=2, stroke_fill="white")
    result.putalpha(original.getchannel("A"))
    if original_mode == "RGB":
        result = result.convert("RGB")
    result.save(destination)
    with Image.open(destination) as check:
        check = check.convert("RGBA")
        if check.size != original.size or check.getchannel("A").tobytes() != original.getchannel("A").tobytes():
            raise ValueError("dialog dimensions or alpha changed")
        for y in range(original.height):
            for x in range(original.width):
                if not (left <= x < right and top <= y < bottom) and check.getpixel((x, y)) != original.getpixel((x, y)):
                    raise ValueError("pixels outside reviewed text rectangle changed")
    return {"archive": "bk3_00", "name": source.name, "file": destination.name,
            "effective_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "replacement_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            "translation": text, "rectangle": list(box), "visual_review": "pending",
            "runtime_verified": False}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--font", required=True, type=Path)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    records = [render(args.source / name, args.output / name, text, box, args.font)
               for name, (text, box) in DIALOGS.items()]
    spec = {"schema": 1, "replacements": records,
            "font_sha256": hashlib.sha256(args.font.read_bytes()).hexdigest()}
    (args.output / "replacements.json").write_text(json.dumps(spec, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"rendered": len(records), "visual_review": "pending"}))


if __name__ == "__main__":
    main()
