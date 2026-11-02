"""Letterbox a plate photo onto an 800x480 frame, the geometry the app sees.

The app's preview and AI channel are both 800x480, so a test frame must have the
same size or the coordinate mapping proves nothing. Two variants are produced:
full width (validates the pipeline) and half width (probes the detection range).

Usage: python tools/_prep_plate_frame.py tmp/plate.png
Output: tmp/plate_real.jpg, tmp/plate_small.jpg
"""

import os
import sys

from PIL import Image

W, H = 800, 480
BG = (32, 38, 48)
DEFAULT_SRC = "tmp/plate.png"


def letterbox(src_path, out_path, target_w):
    src = Image.open(src_path).convert("RGB")
    sw, sh = src.size
    scale = float(target_w) / sw
    nw, nh = int(sw * scale), int(sh * scale)
    resized = src.resize((nw, nh), Image.LANCZOS)
    canvas = Image.new("RGB", (W, H), BG)
    x = (W - nw) // 2
    y = (H - nh) // 2
    canvas.paste(resized, (x, y))
    canvas.save(out_path, quality=95)
    print("[GEN] {} plate_rect=({},{},{},{}) size={}x{} bytes={}".format(
        out_path, x, y, x + nw, y + nh, nw, nh, os.path.getsize(out_path)))


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else DEFAULT_SRC
    if not os.path.isfile(src):
        raise SystemExit("[FAIL] missing source: {}".format(src))
    letterbox(src, "tmp/plate_real.jpg", 760)
    letterbox(src, "tmp/plate_small.jpg", 380)


if __name__ == "__main__":
    main()
