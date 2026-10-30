#!/usr/bin/env python3
"""Build runtime .bin bitmap fonts (lv_font_conv master) and load them on board
with lv.font_load after pushing.

Why this route: this firmware only embeds montserrat_14/16 at compile time, and for
larger sizes or custom symbols the runtime TTF path (tiny_ttf/freetype) has no glyph
cache and re-rasterises every frame under FULL render (measured on board: the 36px
keyboard costs 485~1298 ms/frame), so it is unusable. A .bin bitmap font is loaded
into memory once and a redraw is just a blit (measured on board: a 28px full-screen
redraw is ~3.6 ms/frame, on par with the built-in montserrat_16).

Critical: lv_font_conv must be the GitHub master build. The 1.5.3 release on npm
emits LVGL v8-era output; the v9 loader on board reports correct metrics but the
bitmap alpha is corrupted (glyphs are almost invisible). master ships --stride (a
v9.3+ flag). Install steps:
    git clone --depth 1 https://github.com/lvgl/lv_font_conv.git tools/_lvfc_master
    cd tools/_lvfc_master && npm install --omit=dev --ignore-scripts

Usage:
    python tools/gen_font.py            # build every font in SPECS
    python tools/gen_font.py kb28       # build only the spec named kb28
Output goes to factory/gui/assets/fonts/ and is pushed automatically by
deploy_board.py (it walks all of factory/gui).
"""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LVFC = os.path.join(ROOT, "tools", "_lvfc_master", "lv_font_conv.js")
FONTSRC = os.path.join(ROOT, "tools", "fontsrc")
OUT_DIR = os.path.join(ROOT, "factory", "gui", "assets", "fonts")

# Private-use codepoints used by the LVGL keyboard's default map (including the
# symbol and emoji pages). These codepoints are Font Awesome's own, so taking the
# glyphs from an FA font is an identity mapping and needs no => remap.
#   OK=0xF00C CLOSE=0xF00D LEFT=0xF053 RIGHT=0xF054
#   KEYBOARD=0xF11C BACKSPACE=0xF55A NEW_LINE=0xF8A2
# FA solid 900 (v5) has the first six; NEW_LINE(0xF8A2) is an FA4-era codepoint that
# v5 dropped, so v5's corner-down-left(0xF3BE, the return arrow) is remapped to
# 0xF8A2, which is exactly the right meaning.
LV_KB_SYMBOLS = [0xF00C, 0xF00D, 0xF053, 0xF054, 0xF11C, 0xF55A]
LV_KB_REMAPS = [(0xF3BE, 0xF8A2)]   # (source codepoint, target LVGL codepoint)


def _ranges(items):
    """Assemble range descriptions into lv_font_conv's -r argument list (one -r
    per element).

    items entries: (lo, hi) a normal range (lo==hi means a single point);
    ("=>", src, dst) a remap. Normal ranges merge into one comma string; each remap
    gets its own -r (mixing remaps into a comma list is not supported, measured on
    board).
    """
    plain = []
    remaps = []
    for item in items:
        if isinstance(item, tuple) and len(item) == 3 and item[0] == "=>":
            _, src, dst = item
            remaps.append("0x%X=>0x%X" % (src, dst))
        else:
            lo, hi = item
            if lo == hi:
                plain.append("0x%X" % lo)
            else:
                plain.append("0x%X-0x%X" % (lo, hi))
    out = []
    if plain:
        out.append(",".join(plain))
    out += remaps
    return out


# Per spec: out = output file name; size/bpp; parts = [(source font file, [(lo,hi),...]), ...]
# Several --font sections merge into one font (shared baseline, metrics come from the
# first font).
SPECS = {
    # Large keyboard size: printable ASCII comes from Nunito-Bold, LVGL symbols from
    # FA solid (identity codepoints). Measured at 28px: line_height=30 and a
    # full-screen redraw is ~3.6 ms.
    "kb28": {
        "out": "kb_nunito_bold_28.bin",
        "size": 28,
        "bpp": 4,
        "parts": [
            ("Nunito-Bold.ttf", [(0x20, 0x7E)]),
            ("fa-solid-900.woff",
             [(c, c) for c in LV_KB_SYMBOLS] + [("=>", s, d) for s, d in LV_KB_REMAPS]),
        ],
    },
    # Monospaced numeric font (IMU app readouts): every digit has exactly the same
    # width, so with a fixed sign field ({:+.4f}) the glyph start and end positions
    # never move and the readout does not jitter horizontally.
    # Take the whole ASCII block: the readout prefixes A(g)/G(dps)/T(C) and the group
    # separator "|" must share this font, otherwise the two baselines differ and the
    # prefix sits a few pixels above/below the numbers (measured from an on-board
    # screenshot: montserrat sits 4px lower than mono). The whole ASCII block is only
    # ~4KB, which is acceptable.
    "mono16": {
        "out": "mono_dejavu_16.bin",
        "size": 16,
        "bpp": 4,
        "parts": [
            ("DejaVuSansMono.ttf", [(0x20, 0x7E)]),
        ],
    },
    # Popup body text: only montserrat_14/16 are built in, and 14px looks too small
    # on an 800x480 panel (users said it was hard to read). 22px is the compromise
    # between "readable" and "fits several lines": 22px Nunito Bold has a line height
    # of ~26, so 4 lines of body text take 104px and still fit a 600x340 popup;
    # anything larger pushes the buttons off the bottom.
    "ui22": {
        "out": "ui_nunito_bold_22.bin",
        "size": 22,
        "bpp": 4,
        "parts": [
            ("Nunito-Bold.ttf", [(0x20, 0x7E)]),
        ],
    },
}


def gen(name, spec):
    out = os.path.join(OUT_DIR, spec["out"])
    cmd = ["node", LVFC]
    for font_file, ranges in spec["parts"]:
        src = os.path.join(FONTSRC, font_file)
        if not os.path.isfile(src):
            print("[FAIL] missing source font: %s" % src)
            return False
        cmd += ["--font", src]
        for r in _ranges(ranges):
            cmd += ["-r", r]
    cmd += ["--size", str(spec["size"]), "--format", "bin",
            "--bpp", str(spec["bpp"]), "--no-compress", "-o", out]
    print("[GEN] %s -> %s (%dpx bpp%d)" % (name, spec["out"], spec["size"], spec["bpp"]))
    r = subprocess.run(cmd, capture_output=True, text=True)
    if r.stdout.strip():
        print(r.stdout.strip())
    if r.stderr.strip():
        print(r.stderr.strip())
    if r.returncode != 0 or not os.path.isfile(out):
        print("[FAIL] generating %s failed (exit %d)" % (name, r.returncode))
        return False
    print("[OK] %s  %d bytes" % (out, os.path.getsize(out)))
    return True


def main():
    if not os.path.isfile(LVFC):
        print("[FAIL] cannot find lv_font_conv master: %s\n"
              "     run first: git clone --depth 1 https://github.com/lvgl/lv_font_conv.git tools/_lvfc_master\n"
              "                cd tools/_lvfc_master && npm install --omit=dev --ignore-scripts" % LVFC)
        return 1
    os.makedirs(OUT_DIR, exist_ok=True)
    names = sys.argv[1:] or list(SPECS)
    ok = True
    for n in names:
        if n not in SPECS:
            print("[FAIL] unknown spec: %s (available: %s)" % (n, ", ".join(SPECS)))
            ok = False
            continue
        ok = gen(n, SPECS[n]) and ok
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
