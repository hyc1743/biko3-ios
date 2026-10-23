"""Render reviewed Japanese UI labels using a user-selected local font."""
from pathlib import Path
import argparse
import hashlib
import json
from PIL import Image, ImageDraw, ImageFont

LABELS = {
    "hs_11.tga": "切换视角", "hs_12.tga": "切换视角",
    "hs_21.tga": "自动循环", "hs_22.tga": "自动循环", "hs_23.tga": "自动循环",
    "hs_28.tga": "切换位置", "hs_29.tga": "切换位置",
    "hs_30.tga": "自动", "hs_31.tga": "自动",
    "hs_32.tga": "重置", "hs_33.tga": "重置",
    "hs_34.tga": "饰品1", "hs_35.tga": "饰品1",
    "hs_36.tga": "饰品2", "hs_37.tga": "饰品2",
    "hs_38.tga": "显示男性", "hs_39.tga": "显示男性",
    "hs_40.tga": "显示背景", "hs_41.tga": "显示背景",
    "hs_44.tga": "返回标题", "hs_45.tga": "返回标题",
    "hs_46.tga": "退出游戏", "hs_47.tga": "退出游戏",
    "ih_01.tga": "离开", "ih_02.tga": "离开",
    "hs_51.tga": "正常位", "hs_52.tga": "抬起腿", "hs_53.tga": "抱住双腿",
    "hs_54.tga": "后背位", "hs_55.tga": "抓住腰部", "hs_56.tga": "让她撑住双手",
    "hs_57.tga": "骑乘位", "hs_58.tga": "向后仰", "hs_60.tga": "抓住腿",
    "hs_61.tga": "抓住头部", "hs_62.tga": "揉捏胸部", "hs_63.tga": "让她躺在地上",
    "hs_64.tga": "抓住胸部", "hs_65.tga": "分开双腿", "hs_66.tga": "抓住臀部",
    "hs_67.tga": "乳交", "hs_68.tga": "按住", "hs_71.tga": "身体后仰",
    "hs_74.tga": "松开", "hs_75.tga": "停止", "hs_76.tga": "结束",
    "hs_77.tga": "插入", "hs_78.tga": "殴打", "hs_80.tga": "褪下内裤",
    "hs_81.tga": "跪下", "hs_82.tga": "注入", "hs_83.tga": "剪开内裤",
    "hs_84.tga": "按压", "hs_85.tga": "脱掉内裤", "hs_86.tga": "剥开护符",
    "hs_87.tga": "让她俯卧", "hs_88.tga": "让她仰卧", "hs_89.tga": "抓住下体",
    "hs_90.tga": "撕开连裤袜", "hs_91.tga": "拉下拉链", "hs_92.tga": "褪下裙子",
    "hs_93.tga": "产卵", "hs_94.tga": "插入管子", "hs_95.tga": "抬起腰部",
    "hs_96.tga": "抱住",
}


def render(source: Path, destination: Path, text: str, font_path: Path):
    with Image.open(source) as image:
        original = image.convert("RGBA")
    # These resources are isolated text sprites; retain their existing color
    # state (orange/blue/disabled) while replacing the glyph alpha entirely.
    source_pixels = list(original.getdata())
    opacity = max(pixel[3] for pixel in source_pixels)
    colors = [pixel for pixel in source_pixels if pixel[3] >= opacity * 0.6
              and max(pixel[:3]) - min(pixel[:3]) > 20]
    if colors:
        color = tuple(sorted(p[c] for p in colors)[len(colors) // 2] for c in range(3))
    else:
        color = (150, 150, 150)
    result = Image.new("RGBA", original.size)
    draw = ImageDraw.Draw(result)
    size = min(original.height - 8, 30)
    while size > 8:
        font = ImageFont.truetype(str(font_path), size)
        try:
            font.set_variation_by_axes([700])
        except (AttributeError, OSError):
            pass
        box = draw.textbbox((0, 0), text, font=font, stroke_width=2)
        if box[2] - box[0] <= original.width - 6 and box[3] - box[1] <= original.height - 6:
            break
        size -= 1
    x = (original.width - box[2] + box[0]) / 2 - box[0]
    y = (original.height - box[3] + box[1]) / 2 - box[1]
    draw.text((x + 1, y + 2), text, font=font, fill=(65, 65, 65, 230), stroke_width=2)
    draw.text((x, y), text, font=font, fill=(*color, 255), stroke_width=2, stroke_fill="white")
    if opacity < 255:
        result.putalpha(result.getchannel("A").point(lambda value: round(value * opacity / 255)))
    result.save(destination)
    with Image.open(destination) as check:
        if check.size != original.size or check.mode != "RGBA":
            raise ValueError("text sprite format mismatch")
    return {"archive": "bk3_00", "name": source.name, "file": destination.name,
            "effective_sha256": hashlib.sha256(source.read_bytes()).hexdigest(),
            "replacement_sha256": hashlib.sha256(destination.read_bytes()).hexdigest(),
            "translation": text, "visual_review": "pending", "runtime_verified": False}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--font", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=False)
    records = [render(args.source / name, args.output / name, text, args.font) for name, text in LABELS.items()]
    (args.output / "replacements.json").write_text(json.dumps({"schema": 1, "replacements": records},
                                                               ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps({"rendered": len(records), "runtime_verified": False}))
