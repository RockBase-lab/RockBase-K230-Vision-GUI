# Generate the multi-touch hint bitmaps shown when the Touch app opens:
# factory/gui/assets/widgets/hint_multitouch.png (en) and hint_multitouch_zh.png.
#
# Why a bitmap instead of runtime tiny_ttf:
# LVGL runs RENDER_MODE.FULL (every refresh paints the whole buffer), so tiny_ttf
# re-rasterises each frame; a 26-character string at 80px measured 229.6 ms/frame on
# board, which is unusable. Baked into a PNG it is a single blit with negligible cost.
# Same approach as clock_*.png / timer_*.png.
#
# The background is filled with the canvas colour (COL_CANVAS) instead of an alpha
# channel: this firmware decodes PNGs through lodepng and transparency does not work
# on every path, so filling the colour guarantees no black boxes appear.
#
# The texts come from the "touch.hint" pair in core/i18n.py, so the bitmaps cannot
# drift from the fallback label. The zh half needs Source Han Sans (Nunito has no
# CJK glyphs); place the git-ignored SourceHanSansCN-Regular.ttf in tools/fontsrc/.
#
# Usage: python tools/gen_touch_hint.py
import io
import os
import re

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONTSRC = os.path.join(ROOT, "tools", "fontsrc")
OUT_DIR = os.path.join(ROOT, "factory", "gui", "assets", "widgets")

SIZE = 40               # half of 80px; the en text is ~368px wide at 40px, an easy fit in 800px
CANVAS_W = 800          # same width as the Touch app canvas, so it can be blitted centred
PAD_Y = 12
BG = (0x18, 0x20, 0x29)     # canvas colour R,G,B (0x182029 in app_touch)
FG = (0x45, 0x4E, 0x5D)     # hint grey R,G,B (HINT_COLOR in app_touch)

# (output file, text source, font file)
def _variants():
    src = io.open(os.path.join(ROOT, "factory", "gui", "core", "i18n.py"),
                  encoding="utf-8").read()
    m = re.search(r'"touch\.hint": \("([^"]*)", "([^"]*)"\)', src)
    if not m:
        raise SystemExit('no "touch.hint" pair in core/i18n.py')
    en, zh = m.group(1), m.group(2)
    return (("hint_multitouch.png", en, "Nunito-Bold.ttf"),
            ("hint_multitouch_zh.png", zh, "SourceHanSansCN-Regular.ttf"))


def _bake(out_name, text, font_file):
    font_path = os.path.join(FONTSRC, font_file)
    if not os.path.isfile(font_path):
        raise SystemExit("missing source font: {}".format(font_path))
    font = ImageFont.truetype(font_path, SIZE)
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    left, top, right, bottom = probe.textbbox((0, 0), text, font=font)
    tw = right - left
    th = bottom - top
    if tw > CANVAS_W:
        raise SystemExit("text width {} exceeds the canvas width {}; shorten the "
                         "text or reduce the font size".format(tw, CANVAS_W))

    height = th + PAD_Y * 2
    img = Image.new("RGB", (CANVAS_W, height), BG)
    draw = ImageDraw.Draw(img)
    # textbbox's top can be negative (glyph overhang); correcting by the bbox is what
    # makes it look centred
    draw.text(((CANVAS_W - tw) // 2 - left, PAD_Y - top), text, font=font, fill=FG)
    out_path = os.path.join(OUT_DIR, out_name)
    img.save(out_path)
    print("{} {}x{} text={}x{} -> {}".format(out_name, CANVAS_W, height, tw, th,
                                             out_path))


def main():
    for out_name, text, font_file in _variants():
        _bake(out_name, text, font_file)


if __name__ == "__main__":
    main()
