"""Render a synthetic Chinese licence plate onto an 800x480 test frame.

The board-side probe needs a picture whose decoded text is known, otherwise a
garbage recognition result is indistinguishable from a broken pipeline. This
writes the frame as BMP (uncompressed, which the board's image loader always
accepts) plus a one-line ground-truth file.

Usage: python tools/_gen_plate_testframe.py
Output: tmp/plate_test.bmp and tmp/plate_test.txt
"""

import os

from PIL import Image, ImageDraw, ImageFont

W, H = 800, 480
PLATE_TEXT = "粤B12345"
PLATE_W, PLATE_H = 440, 140
OUT_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "tmp")
FONT_CANDIDATES = [
    r"C:\Windows\Fonts\simhei.ttf",
    r"C:\Windows\Fonts\msyhbd.ttc",
    r"C:\Windows\Fonts\msyh.ttc",
]


def load_font(size):
    for path in FONT_CANDIDATES:
        if os.path.isfile(path):
            return ImageFont.truetype(path, size)
    raise SystemExit("[FAIL] no CJK font found; install one or edit FONT_CANDIDATES")


def main():
    img = Image.new("RGB", (W, H), (32, 38, 48))
    draw = ImageDraw.Draw(img)

    x0 = (W - PLATE_W) // 2
    y0 = (H - PLATE_H) // 2

    # Blue plate body with a white border, close to a real Chinese plate.
    draw.rectangle([x0, y0, x0 + PLATE_W, y0 + PLATE_H], fill=(20, 60, 180))
    draw.rectangle([x0 + 6, y0 + 6, x0 + PLATE_W - 6, y0 + PLATE_H - 6],
                   outline=(245, 245, 245), width=4)

    font = load_font(84)
    text = PLATE_TEXT
    bbox = draw.textbbox((0, 0), text, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    draw.text((x0 + (PLATE_W - tw) // 2 - bbox[0],
               y0 + (PLATE_H - th) // 2 - bbox[1]),
              text, font=font, fill=(245, 245, 245))

    os.makedirs(OUT_DIR, exist_ok=True)
    bmp = os.path.join(OUT_DIR, "plate_test.bmp")
    img.save(bmp)
    # JPEG keeps the push over USB serial short enough to be practical; the board
    # decodes it with image.Image the same way.
    jpg = os.path.join(OUT_DIR, "plate_test.jpg")
    img.save(jpg, quality=92)
    txt = os.path.join(OUT_DIR, "plate_test.txt")
    with open(txt, "w", encoding="utf-8") as handle:
        handle.write(PLATE_TEXT + "\n")

    print("[GEN] {} ({} bytes), plate box=({},{},{},{}) text={}".format(
        jpg, os.path.getsize(jpg), x0, y0, x0 + PLATE_W, y0 + PLATE_H, PLATE_TEXT))


if __name__ == "__main__":
    main()
