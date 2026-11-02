# Generate status bar icon PNGs (Lucide, ISC): home / wifi / battery.
# Pipeline: fetch Lucide SVG -> force a white stroke -> svglib+rlPyCairo 4x render ->
#           LANCZOS downscale to the target size -> composite onto the status bar
#           colour (opaque, avoiding alpha-blending differences).
#
# Why not LVGL's built-in SYMBOL: this firmware only ships 14/16px built-in fonts, so
# the symbols render far too small next to the 56px bar. PNG icons accept any size and
# a redraw is a plain blit (measured on board: 0 ms).

import os
import re
import urllib.request

from svglib.svglib import svg2rlg
from reportlab.graphics import renderPM
from PIL import Image

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
ASSETS = os.path.join(ROOT, "factory", "gui", "assets")

_gui_txt = open(os.path.join(ROOT, "factory", "gui", "launcher.py"),
                encoding="utf-8").read()
_v = int(re.search(r"\bCOL_BAR\s*=\s*0x([0-9A-Fa-f]{6})", _gui_txt).group(1), 16)
BAR = ((_v >> 16) & 255, (_v >> 8) & 255, _v & 255)  # COL_BAR (never hard-code this)
SUPERSAMPLE = 4

# name -> (lucide file name, target width, target height)
ICONS = {
    "home": ("house", 30, 30),
    "wifi": ("wifi", 26, 26),
    "battery": ("battery-full", 32, 26),
}

BASE_URL = "https://unpkg.com/lucide-static@latest/icons/{}.svg"


def fetch_svg(name):
    path = os.path.join(ASSETS, "src_status_{}.svg".format(name))
    if not os.path.exists(path):
        url = BASE_URL.format(name)
        print("fetch", url)
        data = urllib.request.urlopen(url, timeout=20).read()
        with open(path, "wb") as f:
            f.write(data)
    return path


def ink_bbox(img, bg_is_black=True):
    """Return the bbox of non-background pixels; on a black render the brightness
    decides what counts as background."""
    w, h = img.size
    px = img.load()
    xs, ys = [], []
    for y in range(h):
        for x in range(w):
            r, g, b = px[x, y]
            if (r > 24 or g > 24 or b > 24) if bg_is_black else (r < 231):
                xs.append(x)
                ys.append(y)
    if not xs:
        return None
    return min(xs), min(ys), max(xs) + 1, max(ys) + 1


def render(svg_path, w, h, out_path, color=(255, 255, 255), shared_box=None):
    """Render an SVG onto a w x h canvas filled with the bar colour. color is the
    stroke colour; shared_box crops by another (complete) icon's ink bbox so a
    variant with fewer arcs keeps the same size and placement as the full icon."""
    src = open(svg_path, encoding="utf-8").read()
    hexc = "#{:02X}{:02X}{:02X}".format(*color)
    src = src.replace('stroke="currentColor"', 'stroke="{}"'.format(hexc))
    src = src.replace('fill="currentColor"', 'fill="{}"'.format(hexc))
    tmp = svg_path + ".tmp.svg"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(src)

    drawing = svg2rlg(tmp)
    os.remove(tmp)

    # Scale against the viewBox base rather than raising width/height: resizing the
    # canvas alone does not zoom the content, drawing.scale is required here.
    base = float(drawing.width) or 24.0
    big = max(w, h) * SUPERSAMPLE * 2
    scale = big / base
    drawing.width = base * scale
    drawing.height = base * scale
    drawing.scale(scale, scale)

    tmp_png = out_path + ".big.png"
    # Render on black: a white icon on a white background is indistinguishable, which
    # once made the whole image look like empty background.
    renderPM.drawToFile(drawing, tmp_png, fmt="PNG", bg=0x000000)
    big_img = Image.open(tmp_png).convert("RGB")
    os.remove(tmp_png)

    # Crop to the ink bbox, scale to fit the target box, then centre it on the bar
    # colour canvas. Scaling the whole canvas keeps the SVG padding and off-centres
    # the icon (observed: home and wifi shifted by 3px).
    box = shared_box if shared_box is not None else ink_bbox(big_img)
    if box is not None:
        big_img = big_img.crop(box)
    bw, bh = big_img.size
    k = min(w / float(bw), h / float(bh))
    nw, nh = max(1, int(round(bw * k))), max(1, int(round(bh * k)))
    big_img = big_img.resize((nw, nh), Image.LANCZOS)

    canvas = Image.new("RGB", (w, h), BAR)
    px = big_img.load()
    cpx = canvas.load()
    ox = (w - nw) // 2
    oy = (h - nh) // 2
    for y in range(nh):
        for x in range(nw):
            r, g, b = px[x, y]
            lum = (r * 299 + g * 587 + b * 114) // 1000
            if lum >= 128:
                cpx[ox + x, oy + y] = color
            else:
                t = lum / 128.0
                cpx[ox + x, oy + y] = tuple(
                    int(BAR[i] + (color[i] - BAR[i]) * t) for i in range(3))
    canvas.save(out_path)
    print("saved", os.path.basename(out_path), "{}x{} ink={}x{}".format(w, h, nw, nh))
    return box, canvas


def main():
    for name, (lucide, w, h) in ICONS.items():
        svg = fetch_svg(lucide)
        out = os.path.join(ASSETS, "icon_status_{}.png".format(name))
        render(svg, w, h, out)
    gen_wifi_status()
    gen_battery_status()


# ---- battery charge variants ----
# The Lucide battery-full icon carries three inner bars, which can only express four
# states if one of them doubles as "full" and leaves a coarse top step. The status bar
# needs one cell per 25% (measured charge buckets), so the three bars are replaced by
# four evenly spaced ones and the unlit cells keep the same grey track the wifi
# variants use. Geometry comes from the icon itself: body x=2..18 with a 2px stroke
# leaves 3..17, so four 2px bars need 5 gaps of 1.2 units.
BATTERY_BARS = (5.2, 8.4, 11.6, 14.8)
BATTERY_COL = (
    (0xFF, 0x6B, 0x6B),   # 0: <= 25%
    (0xFF, 0xC8, 0x57),   # 1: 25-50%
    (0x4C, 0xD9, 0x7B),   # 2: 50-75%
    (0x4C, 0xD9, 0x7B),   # 3: 75-100%
)
BATTERY_TRACK = (0x4A, 0x53, 0x5E)


def _bars_svg(src, lit):
    """Battery SVG whose first `lit` inner cells are drawn and the rest omitted."""
    body = "\n  ".join('<path d="M{} 10v4" />'.format(x) for x in BATTERY_BARS[:lit])
    head = re.sub(r'\s*<path d="M\d+(?:\.\d+)? 10v4" />', "", src)
    return re.sub(r"<rect ", body + "\n  <rect ", head, count=1)


def gen_battery_status():
    """Bake icon_status_battery_0..3.png, one per 25% bucket."""
    w, h = ICONS["battery"][1], ICONS["battery"][2]
    svg_path = fetch_svg(ICONS["battery"][0])
    src = open(svg_path, encoding="utf-8").read()
    # Crop box from the full four-cell icon so every level keeps the same size and
    # placement; cropping an empty cell count's own ink would rescale it.
    box, _full = _render_svg(_bars_svg(src, 4), w, h, (255, 255, 255),
                             shared_box=None)
    for lit in range(4):
        _, lit_img = _render_svg(_bars_svg(src, lit), w, h, BATTERY_COL[lit], box)
        _, track = _render_svg(_bars_svg(src, 4), w, h, BATTERY_TRACK, box)
        out = os.path.join(ASSETS, "icon_status_battery_{}.png".format(lit))
        _composite(track, lit_img).save(out)
        print("saved icon_status_battery_{}.png".format(lit))
# Variants derive directly from the original Lucide wifi SVG: dropping the outer
# arc paths lights fewer arcs, changing the stroke colour encodes signal strength,
# and the disconnected state lights all three arcs red plus an overlay slash path.
# They run through exactly the same pipeline as the main icon (same viewBox, same
# stroke width, same supersampling and cropping), so geometry, line width, and
# anti-aliasing match icon_status_wifi.png pixel for pixel.
# Fewer-arc variants are cropped by the full three-arc icon's ink bbox (shared_box);
# cropping their own small bbox would blow them up to fill the frame and make them
# look alternately large and small (observed).

# Signal tier colours (same palette as app_network's RSSI meter)
WIFI_COL = {
    "off": (0xFF, 0x6B, 0x6B),   # disconnected: red
    "1":   (0xFF, 0x6B, 0x6B),   # weak: red
    "2":   (0xFF, 0xC8, 0x57),   # fair: yellow
    "3":   (0x4C, 0xD9, 0x7B),   # strong: green
}


# "Track" grey for unlit arcs: faint enough that the missing arc is still visible,
# yet it does not compete with the lit colour.
WIFI_TRACK = (0x4A, 0x53, 0x5E)


def _wifi_tweak(svg):
    """Enlarge the bottom dot of Lucide's wifi icon so its visual weight matches the
    solid battery block to its right.

    Measured on board: the battery's bottom edge is a solid 24~26px wide bar while
    wifi's bottom is only a 2~4px dot, which makes wifi look like it floats with a
    misaligned baseline. Lucide draws that dot as a zero-length line with a round cap
    (diameter = stroke width, ~2px). Replacing it with a filled circle of r=1.5 whose
    centre sits slightly lower drops the bottom edge by ~1 unit (lower centre of
    gravity) while **leaving stroke width and arc radii untouched** - an earlier
    version thickened the stroke to 2.4 and grew the dot to r=2.1, which pushed the
    dot almost onto the inner arc and unbalanced the arc-to-arc and dot-to-arc gaps
    (observed on board as "every gap looks different"). The dot's top edge still
    touches the inner arc's outer edge (the original dot-to-arc gap is only ~0.1
    unit), preserving the original rhythm.
    Applied uniformly to the white base image and to every variant so the geometry
    stays pixel-identical."""
    return svg.replace('<path d="M12 20h.01" />',
                       '<circle cx="12" cy="20.4" r="1.5" fill="currentColor" stroke="none" />')


def _render_svg(svg_text, w, h, color, shared_box=None, save_path=None):
    """Render SVG text onto a bar-colour canvas and return (box, canvas). With
    shared_box=None the crop box comes from this image's own ink (used for the first
    base image); a given save_path additionally writes the result to disk."""
    import tempfile
    fd, tmp = tempfile.mkstemp(suffix=".svg", dir=ASSETS)
    os.close(fd)
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(svg_text)
    out = save_path if save_path is not None else tmp + ".png"
    box, canvas = render(tmp, w, h, out, color=color, shared_box=shared_box)
    os.remove(tmp)
    if save_path is None:
        os.remove(out)
    return box, canvas


def _composite(track, lit):
    """Overlay the lit pixels (whatever on `lit` is not the bar colour) onto `track`,
    keeping track's grey arcs everywhere else. Both canvases share the same shared_box
    and geometry, so they line up pixel for pixel."""
    w, h = track.size
    tp = track.load()
    lp = lit.load()
    for y in range(h):
        for x in range(w):
            lr, lg, lb = lp[x, y]
            # Bar colour on `lit` means no ink there, so keep track; else use lit.
            if (lr, lg, lb) != BAR:
                tp[x, y] = (lr, lg, lb)
    return track


def gen_wifi_status():
    w, h = ICONS["wifi"][1], ICONS["wifi"][2]
    svg_path = fetch_svg(ICONS["wifi"][0])
    src = open(svg_path, encoding="utf-8").read()
    # The white base image also goes through _wifi_tweak (thicker stroke, larger
    # bottom dot) before rendering, so it yields the shared crop box.
    out_full = os.path.join(ASSETS, "icon_status_wifi.png")
    box, _full = _render_svg(_wifi_tweak(src), w, h, (255, 255, 255),
                             shared_box=None, save_path=out_full)

    paths = re.findall(r'<path[^>]*/>', src)
    # Lucide wifi's four paths, in order: bottom dot, outer arc, middle arc, inner arc
    dot, arc_out, arc_mid, arc_in = paths[0], paths[1], paths[2], paths[3]
    all_arcs = [dot, arc_out, arc_mid, arc_in]
    # Disconnected slash: first stroke a wide bar-colour outline (negative space),
    # then draw the coloured line, so the slash stays readable where it crosses an arc.
    bar_hex = "#{:02X}{:02X}{:02X}".format(*BAR)
    slash_gap = ('<path d="M4 5 L20 19" stroke="{}" stroke-width="5" />'.format(bar_hex))
    slash = ('<path d="M4 5 L20 19" />')
    variants = {
        "off": [dot, arc_out, arc_mid, arc_in, slash_gap, slash],
        "1":   [dot, arc_in],
        "2":   [dot, arc_mid, arc_in],
        "3":   [dot, arc_out, arc_mid, arc_in],
    }

    def build_svg(keep):
        body = "\n  ".join(keep)
        # Replace the whole span from the first path to the last path (greedy +
        # DOTALL). Matching only the leading run of paths leaves the later original
        # paths in place (observed: every variant rendered as full arcs).
        # _wifi_tweak is applied last: it swaps the dot path for a filled circle and
        # thickens the stroke (the dot path string is still in body, so the
        # replacement hits).
        return _wifi_tweak(re.sub(r"<path.*</svg>", body + "\n</svg>", src,
                                  count=1, flags=re.S))

    # Shared "track": all three arcs plus the dot, in pale grey. Fewer-arc variants
    # paint this first, then overlay the lit arcs.
    track_svg = build_svg(all_arcs)
    for key, keep in variants.items():
        _, lit = _render_svg(build_svg(keep), w, h, WIFI_COL[key], box)
        out = os.path.join(ASSETS, "icon_status_wifi_{}.png".format(key))
        if key == "off":
            # Disconnected state lights all three arcs plus the slash: no grey track
            # is needed, use lit as is (keeps the slash's bar-colour negative space).
            lit.save(out)
        else:
            _, track = _render_svg(track_svg, w, h, WIFI_TRACK, box)
            _composite(track, lit).save(out)
        print("saved icon_status_wifi_{}.png".format(key))


if __name__ == "__main__":
    main()
