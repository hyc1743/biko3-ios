"""Translate the Japanese mouse-instruction paragraphs, preserving English."""
from pathlib import Path
import argparse
import hashlib
import json
from PIL import Image, ImageDraw, ImageFont

# Only the original Japanese paragraphs. Mouse icons, Action, NOW LOADING,
# character image, title and all other areas remain byte-equivalent pixels.
PARAGRAPHS = [
    ((260, 116, 1245, 248), "按住鼠标左键向前奔跑。\n双击后按住则向前行走。"),
    ((260, 302, 1245, 437), "按住鼠标右键后退。\n双击鼠标右键蹲下。"),
    ((260, 493, 1245, 626), "左右移动鼠标，\n可以改变身体朝向。"),
    ((531, 654, 1245, 781), "在 Action 标记附近按下，\n可以躲藏或执行其他动作。"),
]


def render(source, destination, font_path):
    with Image.open(source) as image:
        original = image.convert("RGB")
    if original.size != (1280, 960):
        raise ValueError("instruction texture size changed")
    result = original.copy()
    for (left, top, right, bottom), text in PARAGRAPHS:
        # Retain a scanline/gradient approximation from clear rows bracketing
        # each paragraph. The replaced rectangles are recorded for review.
        for x in range(left, right):
            a = original.getpixel((x, top - 4))
            b = original.getpixel((x, bottom + 4))
            for y in range(top, bottom):
                t = (y - top + 1) / (bottom - top + 1)
                result.putpixel((x, y), tuple(round(a[c] * (1 - t) + b[c] * t) for c in range(3)))
        draw = ImageDraw.Draw(result)
        size = 45
        while size > 10:
            font = ImageFont.truetype(str(font_path), size)
            try:
                font.set_variation_by_axes([700])
            except (AttributeError, OSError):
                pass
            box = draw.multiline_textbbox((0, 0), text, font=font, spacing=8, stroke_width=2)
            if box[2] - box[0] < right - left - 12 and box[3] - box[1] < bottom - top - 10:
                break
            size -= 1
        draw.multiline_text((left + 8 - box[0], (top + bottom - box[3] + box[1]) / 2 - box[1]),
                            text, font=font, fill="white", stroke_width=2, stroke_fill="black", spacing=8)
    result.save(destination)
    with Image.open(destination) as check:
        if check.size != original.size:
            raise ValueError("instruction output size mismatch")
        for y in range(960):
            for x in range(1280):
                if not any(a <= x < c and b <= y < d for (a, b, c, d), _ in PARAGRAPHS):
                    if check.getpixel((x, y)) != original.getpixel((x, y)):
                        raise ValueError("instruction pixels outside Japanese paragraphs changed")
    return {"archive": "bk3_00", "name": source.name, "file": destination.name,
            "effective_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "replacement_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            "rectangles": [list(box) for box, _ in PARAGRAPHS],
            "translation": [text for _, text in PARAGRAPHS], "visual_review": "pending",
            "remaining_japanese": ["bottom title subtitle and trial logo"], "runtime_verified": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--font", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    row = render(args.source / "te_01.bmp", args.output / "te_01.bmp", args.font)
    (args.output / "replacements.json").write_text(json.dumps({"schema": 1, "replacements": [row]},
                                                               ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"rendered": 1, "remaining_japanese": row["remaining_japanese"]}))
