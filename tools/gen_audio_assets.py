# Generate the Audio app's large digit bitmaps and button textures.
#
# Why not use tiny_ttf for the 64px timer: this firmware's LVGL runs
# DISP_RENDER_MODE.FULL, so any change repaints the whole screen and tiny_ttf
# re-rasterises every frame (measured on board: ~155 ms per label), which locks up
# instantly. Switching a bitmap digit is a single blit with negligible cost (same
# approach as the status bar clock in tools/gen_clock_digits.py).
#
# Button labels are baked into the texture for the same reason: the built-in fonts
# are only montserrat_14/16, and 16px text looks far too sparse inside a 240x100
# button.
#
# Usage: python tools/gen_audio_assets.py
#
# The generated constants (TIMER_* / BTN_* / APP_BG) are parsed from app_audio.py so
# the generator and the display side stay strictly in sync; changing a constant in
# the app requires re-running this script.

import os
import re

from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ASSETS = os.path.join(ROOT, "factory", "gui", "assets")
# Timer digits are status-bar-style glyphs; transport buttons are control faces.
GLYPHS = os.path.join(ASSETS, "glyphs")
WIDGETS = os.path.join(ASSETS, "widgets")
FONT_PATH = os.path.join(ASSETS, "fonts", "Nunito-Bold-700.ttf")
APP_PY = os.path.join(ROOT, "factory", "gui", "apps", "app_audio.py")

SUPERSAMPLE = 4

# button state -> (background, text)
BUTTONS = {
    "btn_rec": ("0xDC2626", "REC"),
    "btn_stop_rec": ("0x991B1B", "STOP"),
    "btn_play": ("0x2563EB", "PLAY"),
    "btn_stop_play": ("0x1E40AF", "STOP"),
    # Sweep page (Audio app page 2) START/STOP, same font and size style as the main buttons
    "btn_tone_start": ("0x16A34A", "START"),
    "btn_tone_stop": ("0xDC2626", "STOP"),
}


def parse_const(name, text):
    m = re.search(r"^%s\s*=\s*(0x[0-9A-Fa-f]+|\d+)" % name, text, re.M)
    if not m:
        raise SystemExit("cannot find %s in app_audio.py" % name)
    return int(m.group(1), 0)


def hex_to_rgb(v):
    return ((v >> 16) & 0xFF, (v >> 8) & 0xFF, v & 0xFF)


def render_cell(ch, cell_w, canvas_h, font_size, bg_rgb):
    """Render one character into a fixed-size cell and centre it (so a proportional
    font does not wobble horizontally)."""
    w, h = cell_w * SUPERSAMPLE, canvas_h * SUPERSAMPLE
    img = Image.new("L", (w, h), 0)
    d = ImageDraw.Draw(img)
    font = ImageFont.truetype(FONT_PATH, font_size * SUPERSAMPLE)
    bb = d.textbbox((0, 0), ch, font=font)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    x = (w - tw) // 2 - bb[0]
    y = (h - th) // 2 - bb[1]
    d.text((x, y), ch, font=font, fill=255)

    mask = img.resize((cell_w, canvas_h), Image.LANCZOS)
    canvas = Image.new("RGB", (cell_w, canvas_h), bg_rgb)
    canvas.paste((255, 255, 255), (0, 0, cell_w, canvas_h), mask)
    return canvas


def render_button(w, h, radius, bg_rgb, text, text_size, canvas_rgb):
    """Button texture. Outside the rounded corners the **page background colour**
    must be filled in, never black.

    This bit us early on: the canvas was pure black and the PNG is stored as RGB
    (opaque), so once on screen the four corners showed as black squares, clearly
    visible against the #1A1A1A background.
    """
    img = Image.new("RGB", (w * SUPERSAMPLE, h * SUPERSAMPLE), canvas_rgb)
    d = ImageDraw.Draw(img)
    # Rounded rectangle (Pillow needs coordinates at the 4x supersample)
    d.rounded_rectangle(
        (0, 0, w * SUPERSAMPLE - 1, h * SUPERSAMPLE - 1),
        radius=radius * SUPERSAMPLE, fill=bg_rgb)
    font = ImageFont.truetype(FONT_PATH, text_size * SUPERSAMPLE)
    bb = d.textbbox((0, 0), text, font=font)
    tw, th = bb[2] - bb[0], bb[3] - bb[1]
    d.text(((w * SUPERSAMPLE - tw) // 2 - bb[0],
            (h * SUPERSAMPLE - th) // 2 - bb[1]), text, font=font, fill=(255, 255, 255))
    return img.resize((w, h), Image.LANCZOS)


def main():
    src = open(APP_PY, encoding="utf-8").read()

    font_size = parse_const("TIMER_FONT_SIZE", src)
    cell_w = parse_const("TIMER_CELL_W", src)
    colon_w = parse_const("TIMER_COLON_W", src)
    canvas_h = parse_const("TIMER_H", src)
    bg = parse_const("APP_BG", src)
    btn_w = parse_const("BTN_W", src)
    btn_h = parse_const("BTN_H", src)
    btn_radius = parse_const("BTN_RADIUS", src)
    btn_font = parse_const("BTN_FONT_SIZE", src)

    bg_rgb = hex_to_rgb(bg)
    print("timer: %dpx cell=%d colon=%d h=%d bg=#%06X"
          % (font_size, cell_w, colon_w, canvas_h, bg))
    print("button: %dx%d r=%d font=%dpx" % (btn_w, btn_h, btn_radius, btn_font))

    # Verify the cell width can hold the ink (observed: a 9px colon in a 7px cell
    # was clipped away)
    probe = Image.new("L", (400, 400))
    d = ImageDraw.Draw(probe)
    font = ImageFont.truetype(FONT_PATH, font_size)
    digit_need = max(
        d.textbbox((0, 0), c, font=font)[2] - d.textbbox((0, 0), c, font=font)[0]
        for c in "0123456789")
    cb = d.textbbox((0, 0), ":", font=font)
    colon_need = cb[2] - cb[0]
    if cell_w < digit_need + 2 or colon_w < colon_need + 2:
        raise SystemExit(
            "CELL TOO NARROW: need digit>=%d colon>=%d, got %d / %d"
            % (digit_need + 2, colon_need + 2, cell_w, colon_w))

    for ch in "0123456789":
        p = os.path.join(GLYPHS, "timer_%s.png" % ch)
        render_cell(ch, cell_w, canvas_h, font_size, bg_rgb).save(p)
        print("  wrote", os.path.basename(p), os.path.getsize(p), "B")

    p = os.path.join(GLYPHS, "timer_colon.png")
    render_cell(":", colon_w, canvas_h, font_size, bg_rgb).save(p)
    print("  wrote", os.path.basename(p), os.path.getsize(p), "B")

    for name, (color, text) in BUTTONS.items():
        p = os.path.join(WIDGETS, name + ".png")
        render_button(btn_w, btn_h, btn_radius, hex_to_rgb(int(color, 16)),
                      text, btn_font, bg_rgb).save(p)
        print("  wrote", os.path.basename(p), os.path.getsize(p), "B")

    print("done")


if __name__ == "__main__":
    main()
