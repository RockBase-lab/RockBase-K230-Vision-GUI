# Offline-bake the timezone wheel bitmaps: on the PC, Pillow bakes the 25 timezone
# labels at three font sizes into one vertical sprite sheet each, for app_network's
# custom iOS-style picker.
#
# Why pre-bake: this firmware's LVGL runs DISP_RENDER_MODE.FULL, so tiny_ttf
# re-rasterises every frame (measured on board: 3184 ms/frame while scrolling,
# 5779 ms/frame when the font size changes), and a live scroller would lock up.
# A bitmap blit is ~0 ms.
#
# Output: factory/gui/assets/cache/tz_t{0,1,2}.bin
#   header: b"TZS1" + tier(1B) + count(1B) + w(2B LE) + cell_h(2B LE) + cf(2B LE)
#   body  : count cells, each w*cell_h*4 bytes, XRGB8888 (little-endian B,G,R,X),
#           text centred horizontally and vertically inside the cell, background =
#           card colour.
#
# After changing font sizes or colours, re-run this tool and push assets/cache/.

import os
import re
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GUI = os.path.join(ROOT, "factory", "gui")
ASSETS = os.path.join(GUI, "assets")
CACHE = os.path.join(ASSETS, "cache")
FONT_PATH = os.path.join(ASSETS, "Nunito-Bold-700.ttf")

CF_XRGB8888 = 17

# Three font sizes (selected / adjacent / far) with their matching cell heights
# (must be >= that row's ink height so nothing gets clipped).
TIERS = [(34, 44), (22, 30), (15, 22)]
ROW_W = 170
TEXT_COLOR = (255, 255, 255)      # non-selected: white; selected colour resolved by app_network


def parse_card_bg():
    """Parse COL_CARD from app_network.py so the bitmap background matches the card."""
    txt = open(os.path.join(GUI, "apps", "app_network.py"), encoding="utf-8").read()
    v = int(re.search(r"COL_CARD\s*=\s*0x([0-9A-Fa-f]{6})", txt).group(1), 16)
    return ((v >> 16) & 255, (v >> 8) & 255, v & 255)


def parse_accent():
    """The selected row uses the app accent (same colour as the desktop icons and the
    saved-network highlight, closing the visual loop on the whole card)."""
    txt = open(os.path.join(GUI, "apps", "app_network.py"), encoding="utf-8").read()
    v = int(re.search(r"COL_ACCENT\s*=\s*0x([0-9A-Fa-f]{6})", txt).group(1), 16)
    return ((v >> 16) & 255, (v >> 8) & 255, v & 255)


def labels():
    return ["UTC{:+d}".format(h) for h in range(-12, 13)]


def to_xrgb8888(img):
    w, h = img.size
    px = list(img.convert("RGBA").getdata())
    raw = bytearray(w * h * 4)
    for i, (r, g, b, _a) in enumerate(px):
        o = i * 4
        raw[o] = b
        raw[o + 1] = g
        raw[o + 2] = r
        raw[o + 3] = 255
    return bytes(raw)


def bake_sheet(size, cell_h, bg, text_color):
    font = ImageFont.truetype(FONT_PATH, size)
    d_dummy = ImageDraw.Draw(Image.new("RGBA", (1, 1)))
    sheet = Image.new("RGBA", (ROW_W, cell_h * len(labels())), bg + (255,))
    d = ImageDraw.Draw(sheet)
    for i, text in enumerate(labels()):
        bb = d_dummy.textbbox((0, 0), text, font=font)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        cx = (ROW_W - tw) // 2 - bb[0]
        cy = i * cell_h + (cell_h - th) // 2 - bb[1]
        d.text((cx, cy), text, font=font, fill=text_color + (255,))
    return sheet


def main():
    os.makedirs(CACHE, exist_ok=True)
    bg = parse_card_bg()
    accent = parse_accent()
    n = len(labels())
    for tier, (size, cell_h) in enumerate(TIERS):
        color = accent if tier == 0 else TEXT_COLOR
        raw = to_xrgb8888(bake_sheet(size, cell_h, bg, color))
        header = (b"TZS1" + bytes([tier, n]) +
                  ROW_W.to_bytes(2, "little") + cell_h.to_bytes(2, "little") +
                  CF_XRGB8888.to_bytes(2, "little"))
        path = os.path.join(CACHE, "tz_t{}.bin".format(tier))
        with open(path, "wb") as f:
            f.write(header + raw)
        print("tier{} size={} cell_h={} w={} -> tz_t{}.bin ({}B)".format(
            tier, size, cell_h, ROW_W, tier, len(header) + len(raw)))
    print("done ->", CACHE)


if __name__ == "__main__":
    main()
