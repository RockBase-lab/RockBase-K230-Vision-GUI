# Generate the eye icon PNG for the "show/hide password" control on the right side
# of the password box.
#
# 24x24, composited straight onto the input row colour (ui.kb_layer's COL_ROW 0x1E262E):
# the PNG is opaque, so it blends seamlessly with the input background (a transparent
# PNG would rely on alpha blending inside LVGL and tends to show colour banding where
# it overlaps the rounded corners and border).
#
# Reuses the render pipeline from tools/gen_status_icons.py (Lucide SVG -> supersampled
# render on black -> crop ink -> LANCZOS downscale -> composite onto the target
# colour); only the module-level BAR (background) is replaced with the input row
# colour and the stroke with the text colour. The result matches the status bar icon
# set stylistically.

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ASSETS = os.path.join(ROOT, "factory", "gui", "assets")
sys.path.insert(0, HERE)

import gen_status_icons as gsi  # noqa: E402

# ui.kb_layer.COL_ROW (input box / row background)
KB_ROW = 0x1E262E
# ui.kb_layer.COL_TEXT / COL_MUTED
KB_TEXT = 0xE6EAF0
KB_MUTED = 0x8A94A2

SIZE = 24

# (output name, lucide icon name, stroke colour)
ICONS = [
    ("eye", "eye", KB_TEXT),          # plaintext state: bright (tap to hide)
    ("eye_off", "eye-off", KB_MUTED),  # masked state: dim (tap to reveal)
]


def main():
    bar = ((KB_ROW >> 16) & 255, (KB_ROW >> 8) & 255, KB_ROW & 255)
    gsi.BAR = bar   # render() reads the module-level BAR for background and blending
    for out_name, lucide, col in ICONS:
        svg = gsi.fetch_svg(lucide)
        color = ((col >> 16) & 255, (col >> 8) & 255, col & 255)
        out = os.path.join(ASSETS, "icon_kb_{}.png".format(out_name))
        gsi.render(svg, SIZE, SIZE, out, color=color)


if __name__ == "__main__":
    main()
