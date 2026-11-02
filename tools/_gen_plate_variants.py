"""Generate degraded plate frames that reproduce an unstable first character.

The symptom: on camera the digits decode correctly but the province character
comes back as the wrong one (川 / 浙 instead of 渝). Digits are wide strokes on a
high-contrast background, so they survive a loss of resolution; the province
character is dense, sits at the left edge of the crop, and is the first thing to
fall apart when the crop is a little off or the plate is a little small.

Each variant below isolates one cause:
  base       the clean reference (should decode correctly)
  small      plate at 60% width, the usual camera distance
  tiny       plate at 40% width, beyond the sensible range
  blur       slight defocus
  cropped    left edge clipped by 4%, as a detector under-read does
  shifted    crop centre moved right by 3%, leaving less room for the character

Usage: python tools/_gen_plate_variants.py tmp/plate.png
Output: tmp/plate_var_<name>.jpg for each variant
"""

import os
import sys

from PIL import Image, ImageFilter

W, H = 800, 480
BG = (32, 38, 48)
OUT_DIR = "tmp"
PLATE_W_RATIO = 0.80        # plate width as a fraction of the frame


def plate_canvas(src, width):
    """Letterbox the source plate onto a frame at the given plate width."""
    sw, sh = src.size
    scale = float(width) / sw
    nw, nh = int(sw * scale), int(sh * scale)
    canvas = Image.new("RGB", (W, H), BG)
    x = (W - nw) // 2
    y = (H - nh) // 2
    canvas.paste(src.resize((nw, nh), Image.LANCZOS), (x, y))
    return canvas, (x, y, x + nw, y + nh)


def main():
    src_path = sys.argv[1] if len(sys.argv) > 1 else "tmp/plate.png"
    src = Image.open(src_path).convert("RGB")
    os.makedirs(OUT_DIR, exist_ok=True)

    plate_w = int(W * PLATE_W_RATIO)

    variants = []

    base, rect = plate_canvas(src, plate_w)
    variants.append(("base", base, rect))

    small, rect_s = plate_canvas(src, int(plate_w * 0.60))
    variants.append(("small", small, rect_s))

    tiny, rect_t = plate_canvas(src, int(plate_w * 0.40))
    variants.append(("tiny", tiny, rect_t))

    variants.append(("blur", base.filter(ImageFilter.GaussianBlur(1.2)), rect))

    # Left edge clipped: blank out the leftmost 4% of the frame width, which is
    # what an under-read box does to the province character.
    cropped = base.copy()
    cut = int(W * 0.04)
    cropped.paste(Image.new("RGB", (cut, H), BG), (0, 0))
    x0, y0, x1, y1 = rect
    variants.append(("cropped", cropped, (max(x0, cut), y0, x1, y1)))

    # Right shift: the plate stays put but the crop centre moves, which is what a
    # detection asymmetry looks like to the recogniser.
    shifted = base.copy()
    variants.append(("shifted", shifted, (x0 + int(W * 0.03), y0, x1, y1)))

    for name, img, r in variants:
        path = os.path.join(OUT_DIR, "plate_var_{}.jpg".format(name))
        img.save(path, quality=92)
        print("[GEN] {:8s} {} plate=({},{},{},{}) {}x{} {} B".format(
            name, path, r[0], r[1], r[2], r[3], r[2] - r[0], r[3] - r[1],
            os.path.getsize(path)))


if __name__ == "__main__":
    main()
