# Generate the 64px member icons used by the secondary popup (folder) layer.
#
# Why this exists: lv.img's set_size() in LVGL **crops** rather than scales - putting
# a 112x112 app icon into a 64x64 box only shows the top-left part, which looks cut
# off. This build exposes neither set_scale nor set_style_transform_scale, and the
# image SIZE_MODE only affects layout, not rendering (measured on board: all six
# modes 0..5 render identically). So the scaling happens at build time with PIL and
# the board blits the icon at its native 64x64 with zero scaling cost.
#
# The icons themselves are RGB with the background already filled with TILE_BG (the
# same colour as the popup card), so a scaled icon pastes seamlessly onto the card
# without any alpha handling.

import os

from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ASSETS = os.path.join(ROOT, "factory", "gui", "assets")
ICONS = os.path.join(ASSETS, "icons")
CACHE = os.path.join(ASSETS, "cache")

SIZE = 64
PREFIX = "member_"


def main():
    made = 0
    for name in sorted(os.listdir(ICONS)):
        if not name.startswith("icon_") or not name.endswith(".png"):
            continue
        # Status bar icons and keyboard icons belong to other size families; skip them
        if "status" in name or name.startswith("icon_kb_"):
            continue
        src = os.path.join(ICONS, name)
        im = Image.open(src).convert("RGB")
        if im.size == (SIZE, SIZE):
            out_img = im
        else:
            out_img = im.resize((SIZE, SIZE), Image.LANCZOS)
        dst = os.path.join(CACHE, PREFIX + name)
        out_img.save(dst)
        made += 1
        print("{} -> cache/{}{} ({} B)".format(
            name, PREFIX, name, os.path.getsize(dst)))
    print("done: {} member icons".format(made))


if __name__ == "__main__":
    main()
