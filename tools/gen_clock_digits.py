# Generate the status bar clock digit bitmaps (Nunito Bold, white on the bar colour,
# opaque).
#
# Why not enlarge the clock with tiny_ttf: under this firmware's RENDER_MODE.FULL any
# change repaints the whole screen and tiny_ttf re-rasterises every frame, so a clock
# that ticks every second stutters by ~150 ms every second. Switching a digit bitmap
# is just a blit.
#
# Fixed cell width: digits differ in width in a proportional font, and a fixed cell
# keeps the whole row from wobbling left and right as the time ticks over.

import os
import re

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
import sys
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import _gui_source as gui_source
ROOT = os.path.dirname(HERE)
ASSETS = os.path.join(ROOT, "factory", "gui", "assets")
FONT_PATH = os.path.join(ASSETS, "fonts", "Nunito-Bold-700.ttf")
GUI_DIR = os.path.join(ROOT, "factory", "gui")


def _hex(name, txt):
    v = int(re.search(r"\b{}\s*=\s*0x([0-9A-Fa-f]{{6}})".format(name), txt).group(1), 16)
    return ((v >> 16) & 255, (v >> 8) & 255, v & 255)


_gui_txt = gui_source.all_text(GUI_DIR)
BAR = _hex("COL_BAR", _gui_txt)      # COL_BAR: status bar background (never hard-code it)
WHITE = _hex("COL_TEXT", _gui_txt)   # clock digits use the primary text colour (settled: white)
FONT_SIZE = 32             # visually comparable to the status icons (ink ~20-30px)
CANVAS_H = 36
SUPERSAMPLE = 4


def parse_sizes():
    """Parse the clock cell sizes from the theme source so generator and display agree
    exactly."""
    import re
    txt = gui_source.all_text(GUI_DIR)
    cell = int(re.search(r"CLOCK_CELL_W\s*=\s*(\d+)", txt).group(1))
    colon = int(re.search(r"CLOCK_COLON_W\s*=\s*(\d+)", txt).group(1))
    high = int(re.search(r"CLOCK_H\s*=\s*(\d+)", txt).group(1))
    return cell, colon, high


def render_glyph(ch, cell_w, canvas_h, font_size=FONT_SIZE):
    w, h = cell_w * SUPERSAMPLE, canvas_h * SUPERSAMPLE
    img = Image.new("RGB", (w, h), (0, 0, 0))
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_PATH, font_size * SUPERSAMPLE)
    bb = d.textbbox((0, 0), ch, font=font)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    x = (w - tw) // 2 - bb[0]
    y = (h - th) // 2 - bb[1]
    d.text((x, y), ch, font=font, fill=WHITE)

    img = img.resize((cell_w, canvas_h), Image.LANCZOS)
    canvas = Image.new("RGB", (cell_w, canvas_h), BAR)
    px = img.load()
    cpx = canvas.load()
    for yy in range(canvas_h):
        for xx in range(cell_w):
            r, g, b = px[xx, yy]
            lum = (r * 299 + g * 587 + b * 114) // 1000
            if lum >= 128:
                cpx[xx, yy] = WHITE
            else:
                t = lum / 128.0
                cpx[xx, yy] = tuple(int(BAR[i] + (WHITE[i] - BAR[i]) * t) for i in range(3))
    return canvas


def main():
    cell, colon_w, canvas_h = parse_sizes()

    # Verify the cell is wide enough for the ink: if not, it gets clipped left and
    # right when drawn (observed: a 9px colon squeezed into 7px)
    probe = Image.new("L", (200, 200))
    d = ImageDraw.Draw(probe)
    font = ImageFont.truetype(FONT_PATH, FONT_SIZE)
    need_digit = max(d.textbbox((0, 0), c, font=font)[2] - d.textbbox((0, 0), c, font=font)[0]
                     for c in "0123456789")
    cb = d.textbbox((0, 0), ":", font=font)
    need_colon = cb[2] - cb[0]
    if cell < need_digit + 2 or colon_w < need_colon + 2:
        raise SystemExit(
            "CELL TOO NARROW: need digit>={} colon>={} but got {} / {} "
            "(update CLOCK_CELL_W / CLOCK_COLON_W in launcher.py)".format(
                need_digit + 2, need_colon + 2, cell, colon_w))

    for ch in "0123456789":
        render_glyph(ch, cell, canvas_h).save(
            os.path.join(ASSETS, "glyphs", "clock_{}.png".format(ch)))
    render_glyph(":", colon_w, canvas_h).save(os.path.join(ASSETS, "glyphs", "clock_colon.png"))
    print("cell_w={} colon_w={} h={} span={}px (ink digit={} colon={})".format(
        cell, colon_w, canvas_h, cell * 6 + colon_w * 2, need_digit, need_colon))


if __name__ == "__main__":
    main()
