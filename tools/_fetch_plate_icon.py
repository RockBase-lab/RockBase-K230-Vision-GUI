"""Re-fetch the IconOven licence-plate source, for provenance.

The result is committed as assets/src/src_license-plate.svg, so this only needs to run
if that file is lost or the upstream icon changes. gen_app_icons.py prefers the
local file and never downloads it itself.

IconOven's shape is a plate: a rounded rectangle with two fixing bolts and two
character lines. It is MIT licensed, so it can be redistributed, which is why it is
preferred over drawing the shape by hand.

The stroke width is unified to 1.6 to match the other sources in this directory
(Lucide is rewritten to the same value during rendering).

Usage: python tools/_fetch_plate_icon.py
Output: factory/gui/assets/src/src_license-plate.svg
"""

import os
import urllib.request

UA = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"}
URL = "https://api.iconify.design/iconoven/license-plate.svg?height=24"
OUT = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                   "factory", "gui", "assets", "src", "src_license-plate.svg")

HEADER = (
    "<!-- IconOven \"license-plate\" (MIT, Appic Marketing, "
    "https://iconoven.com).\n"
    "     Fetched from the Iconify API; stroke width unified to 1.6 to match\n"
    "     the other icon sources in this directory. -->\n"
)


def main():
    req = urllib.request.Request(URL, headers=UA)
    svg = urllib.request.urlopen(req, timeout=25).read().decode("utf-8")
    svg = svg.replace('stroke-width="1.5"', 'stroke-width="1.6"')
    svg = svg.replace('stroke-width="2"', 'stroke-width="1.6"')
    with open(OUT, "w", encoding="utf-8") as handle:
        handle.write(HEADER + svg + "\n")
    print("[OK]", OUT)
    print(svg)


if __name__ == "__main__":
    main()
