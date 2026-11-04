# Generate the multi-touch hint bitmap shown when the Touch app opens:
# factory/gui/assets/widgets/hint_multitouch.png.
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
# Usage: python tools/gen_touch_hint.py
import os

from PIL import Image, ImageDraw, ImageFont

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FONT_PATH = os.path.join(ROOT, "tools", "fontsrc", "Nunito-Bold.ttf")
OUT_PATH = os.path.join(ROOT, "factory", "gui", "assets", "widgets",
                        "hint_multitouch.png")

TEXT = "5-point multi-touch"
SIZE = 40               # half of 80px; this text is ~368px wide at 40px, an easy fit in 800px
CANVAS_W = 800          # same width as the Touch app canvas, so it can be blitted centred
PAD_Y = 12
BG = (0x18, 0x20, 0x29)     # canvas colour R,G,B (0x182029 in app_touch)
FG = (0x45, 0x4E, 0x5D)     # hint grey R,G,B (HINT_COLOR in app_touch)


def main():
    font = ImageFont.truetype(FONT_PATH, SIZE)
    probe = ImageDraw.Draw(Image.new("RGB", (1, 1)))
    left, top, right, bottom = probe.textbbox((0, 0), TEXT, font=font)
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
    draw.text(((CANVAS_W - tw) // 2 - left, PAD_Y - top), TEXT, font=font, fill=FG)
    img.save(OUT_PATH)
    print("hint_multitouch.png {}x{} text={}x{} -> {}".format(
        CANVAS_W, height, tw, th, OUT_PATH))


if __name__ == "__main__":
    main()
