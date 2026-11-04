# Generate desktop app icon PNGs from SVG sources.
#
# This is the single generator for assets/icon_touch|camera|audio|gallery.png. Those
# icons used to be rendered one-off with no script in the repository, so adding an
# icon meant hand-drawing it (unreliable). This script captures the pipeline: a new
# app icon only needs one extra row in ICONS.
#
# Sources are Lucide (ISC) by default; plate uses IconOven (MIT) because no Lucide
# icon means "licence plate". A local assets/src_<name>.svg is always preferred, so
# a non-Lucide source is committed once instead of being downloaded per build. The
# licence of every source is recorded in assets/LICENSE.txt.
#
# Pipeline (it matches the spec recorded in assets/LICENSE.txt):
#   1. take the official Lucide SVG (reuse local assets/src_<name>.svg when present,
#      otherwise download it and keep the copy as provenance)
#   2. stroke="currentColor" -> "#FFFFFF", stroke-width -> 1.6
#   3. render with svglib, scaling the viewBox up to a 4x supersample (changing only
#      width/height does not zoom the content; drawing.scale must be called)
#   4. render on black, crop to the ink bbox by brightness, then scale it into the
#      target box (pad=5) and centre it
#   5. blend the anti-aliased grey levels onto the TILE_BG(#23262A) background by
#      brightness (opaque PNG, avoiding alpha-blending differences when LVGL textures
#      it)
#
# Dependencies: pip install svglib reportlab (Pillow is already used by gen_tile_cache.py)
# Usage:
#   python tools/gen_app_icons.py            # build every entry in ICONS
#   python tools/gen_app_icons.py gallery    # build only the named ones
#
# After adding an icon, remember to re-run python tools/gen_tile_cache.py to
# re-bake the tile cache (and bump BAKE_VER in launcher.py, otherwise the board keeps
# loading the stale cache).

import os
import re
import sys
import urllib.request

from PIL import Image
from reportlab.graphics import renderPM
from svglib.svglib import svg2rlg

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
import _gui_source as gui_source
ROOT = os.path.dirname(HERE)
ASSETS = os.path.join(ROOT, "factory", "gui", "assets")
GUI_DIR = os.path.join(ROOT, "factory", "gui")


def _parse_gui():
    """Parse colour constants out of the theme source so the generator and the
    display side cannot drift apart."""
    txt = gui_source.all_text(GUI_DIR)
    bg = int(re.search(r"\bTILE_BG\s*=\s*0x([0-9A-Fa-f]{6})", txt).group(1), 16)
    accents = dict(
        (k, int(v, 16)) for k, v in re.findall(
            r'"([a-z_]+)":\s*0x([0-9A-Fa-f]{6})',
            re.search(r"APP_ACCENTS\s*=\s*\{(.*?)\}", txt, re.S).group(1)))
    return bg, accents


BG_HEX, APP_ACCENTS = _parse_gui()

SIZE = 112                    # target edge length, matches ICON_SIZE in launcher.py
PAD = 5                       # padding from ink to the border (usable area = SIZE - 2*PAD)
BG = ((BG_HEX >> 16) & 255, (BG_HEX >> 8) & 255, BG_HEX & 255)  # TILE_BG
STROKE_WIDTH = 1.6            # thinner than Lucide's default 2, crisper at 112px
SUPERSAMPLE = 4               # render multiplier (times 2 as svglib's upscale base)
INK_THRESHOLD = 24            # brightness threshold for "has ink" on black

# Output name -> Lucide icon file name (https://lucide.dev/icons)
# One row per icon the product ships: every top-level app, plus every folder
# member. An id that is not in the launcher registry yet (currently "video")
# ships its icon ahead of the app.
ICONS = {
    "touch": "hand",
    "camera": "camera",
    "audio": "mic",
    "gallery": "image",
    "vision": "brain-circuit",     # KPU / AI inference page
    "battery": "battery-medium",   # battery ADC level page
    "imu": "rotate-3d",           # on-board QMI8658A IMU (three-axis rotation)
    "network": "wifi",             # WiFi / HTTP / NTP
    "settings": "settings",        # brightness / volume / key mapping settings
    "video": "video",              # camera recording (MJPEG/MP4)
    "scan": "qr-code",             # QR code / barcode scanning
    "face": "scan-face",           # face group (detect / features / recognise)
    "gesture": "hand-metal",       # gesture recognition
    # ---- secondary popup (folder) member icons ----
    # This firmware's lv.SYMBOL exposes only 62 entries; USER/CROP/CPU/FLASK_1 and
    # friends are all missing, so placeholder members could only draw a circular badge
    # from a built-in symbol, which left the circle empty. Give every member a real
    # icon instead.
    "pose": "person-standing",
    "segment": "lasso",
    "orient": "compass",
    "person": "user-round",
    "fall": "trending-down",
    # IconOven "license-plate" (MIT): a plate body with two fixing bolts and two
    # character lines. Used instead of Lucide "car", which read as "vehicle
    # recognition rather than "licence plate".
    "plate": "license-plate",
    "ocr": "scan-text",
    "face_det": "scan-face",
    "landmark": "grid-3x3",
    "recognize": "id-card",
    "liveness": "eye",
    "mask": "shield",
}

BASE_URL = "https://unpkg.com/lucide-static@latest/icons/{}.svg"

def fetch_svg(icon_name):
    """Prefer the local src_<icon>.svg; fetch it from Lucide when missing.

    Source files are named after the icon, not the app: several apps can reuse the
    same source, and there is never a src_touch.svg duplicating the content of
    src_hand.svg.

    Lucide is the default upstream because most icons come from there. A source
    with a different upstream (plate -> IconOven) is committed under assets/ and
    therefore resolves locally; only a missing Lucide name triggers a download.
    """
    path = os.path.join(ASSETS, "src_{}.svg".format(icon_name))
    if not os.path.exists(path):
        url = BASE_URL.format(icon_name)
        print("  fetch", url)
        data = urllib.request.urlopen(url, timeout=20).read()
        with open(path, "wb") as f:
            f.write(data)
    return path


def ink_bbox(img):
    """Return the bbox of non-black pixels (rendered on black, brightness is ink)."""
    mono = img.convert("L")
    return mono.point(lambda v: 255 if v > INK_THRESHOLD else 0).getbbox()


def render(svg_path, out_path, ink=(255, 255, 255)):
    """ink = target ink colour (the app's accent); brightness from the black render
    acts as the blend weight."""
    # ---- 1. rewrite the stroke colour and width ----
    src = open(svg_path, encoding="utf-8").read()
    src = src.replace('stroke="currentColor"', 'stroke="#FFFFFF"')
    src = src.replace('fill="currentColor"', 'fill="#FFFFFF"')
    src = src.replace('stroke-width="2"',
                      'stroke-width="{}"'.format(STROKE_WIDTH))
    tmp = svg_path + ".tmp.svg"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(src)
    try:
        drawing = svg2rlg(tmp)
    finally:
        os.remove(tmp)
    if drawing is None:
        raise RuntimeError("svg2rlg returned None for " + svg_path)

    # ---- 2. upscale to the supersample size (scale is required; changing
    # width/height alone does not zoom the content) ----
    base = float(drawing.width) or 24.0
    big = SIZE * SUPERSAMPLE * 2
    scale = big / base
    drawing.width = base * scale
    drawing.height = base * scale
    drawing.scale(scale, scale)

    # ---- 3. render on black (a white icon on white cannot be told apart from the
    # background, so the black canvas is mandatory) ----
    tmp_png = out_path + ".big.png"
    renderPM.drawToFile(drawing, tmp_png, fmt="PNG", bg=0x000000)
    big_img = Image.open(tmp_png).convert("RGB")
    os.remove(tmp_png)

    # ---- 4. crop to the ink, then scale into the target box ----
    box = ink_bbox(big_img)
    if box is None:
        raise RuntimeError("no ink rendered for " + svg_path)
    big_img = big_img.crop(box)
    bw, bh = big_img.size
    avail = SIZE - 2 * PAD
    k = min(avail / float(bw), avail / float(bh))
    nw = max(1, int(round(bw * k)))
    nh = max(1, int(round(bh * k)))
    big_img = big_img.resize((nw, nh), Image.LANCZOS)

    # ---- 5. blend the grey levels onto the background by brightness (anti-aliased
    # edges land smoothly on the tile colour) ----
    canvas = Image.new("RGB", (SIZE, SIZE), BG)
    ox = (SIZE - nw) // 2
    oy = (SIZE - nh) // 2
    src_px = big_img.load()
    dst_px = canvas.load()
    for y in range(nh):
        for x in range(nw):
            r, g, b = src_px[x, y]
            lum = (r * 299 + g * 587 + b * 114) // 1000
            if lum >= 255:
                dst_px[ox + x, oy + y] = ink
            else:
                t = lum / 255.0
                dst_px[ox + x, oy + y] = tuple(
                    int(BG[i] + (ink[i] - BG[i]) * t) for i in range(3))

    canvas.save(out_path, optimize=True)
    print("  saved {} {}x{} ink={}x{}".format(
        os.path.basename(out_path), SIZE, SIZE, nw, nh))


def main():
    only = sys.argv[1:] if len(sys.argv) > 1 else None
    names = [n for n in ICONS if only is None or n in only]
    if not names:
        print("nothing to do; known icons:", ", ".join(sorted(ICONS)))
        return
    for app_name in sorted(names):
        icon_name = ICONS[app_name]
        acc = APP_ACCENTS.get(app_name)
        ink = ((acc >> 16) & 255, (acc >> 8) & 255, acc & 255) if acc else (255, 255, 255)
        print("[{}] source/{} ink=#{:06X}".format(app_name, icon_name,
                                                  ink[0] << 16 | ink[1] << 8 | ink[2]))
        svg = fetch_svg(icon_name)
        out = os.path.join(ASSETS, "icon_{}.png".format(app_name))
        render(svg, out, ink)


if __name__ == "__main__":
    main()
