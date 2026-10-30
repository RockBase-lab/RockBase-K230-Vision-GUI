# Offline-bake the tile cache: on the PC, Pillow composites each app's tile bitmap
# and emits tile_<id>.bin (XRGB8888, cf=17) that matches launcher.py's on-board
# _cache_load exactly.
#
# Purpose: skip the first-boot on-device snapshot bake (~1.3 s per tile) so the UI is
# instant at power-on.
# New app flow: edit the APPS table in launcher.py -> run this tool -> push
# factory/gui/assets/cache/.
#
# It parses BAKE_VER / TILE_SIZE / ACCENTS / APPS out of launcher.py so the baked
# parameters always match the board; apps without an icon (placeholders) get an
# accent circular badge plus the first letter (the LVGL symbol font cannot be
# reproduced on the PC).

import os
import re
from PIL import Image, ImageDraw, ImageFont

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GUI = os.path.join(ROOT, "factory", "gui")
ASSETS = os.path.join(GUI, "assets")
CACHE = os.path.join(ASSETS, "cache")
FONT_PATH = os.path.join(ASSETS, "Nunito-Bold-700.ttf")

CF_XRGB8888 = 17   # same as the on-board snapshot

BADGE_SIZE = 96   # badge size when there is no icon (matches _bake_tile in launcher.py)

# Colours, radius, and margins all come from launcher.py so neither side drifts by
# hard-coding them.
CFG = {}


def _hex(txt, name):
    return int(re.search(r"\b{}\s*=\s*0x([0-9A-Fa-f]{{6}})".format(name), txt).group(1), 16)


def parse_gui():
    txt = open(os.path.join(GUI, "launcher.py"), encoding="utf-8").read()
    ver = int(re.search(r"BAKE_VER\s*=\s*(\d+)", txt).group(1))
    CFG["tile"] = int(re.search(r"\bTILE_SIZE\s*=\s*(\d+)", txt).group(1))
    CFG["icon"] = int(re.search(r"\bICON_SIZE\s*=\s*(\d+)", txt).group(1))
    CFG["font"] = int(re.search(r"TILE_FONT_SIZE\s*=\s*(\d+)", txt).group(1))
    CFG["radius"] = int(re.search(r"\bTILE_RADIUS\s*=\s*(\d+)", txt).group(1))
    CFG["name_margin"] = int(re.search(r"\bNAME_BOTTOM_MARGIN\s*=\s*(\d+)", txt).group(1))
    CFG["border_w"] = int(re.search(r"\bTILE_BORDER_W\s*=\s*(\d+)", txt).group(1))
    for key, const in (("bg", "COL_BG"), ("btn", "TILE_BG"), ("txt", "NAME_COLOR"),
                       ("border", "TILE_BORDER")):
        v = _hex(txt, const)
        CFG[key] = ((v >> 16) & 255, (v >> 8) & 255, v & 255)
    accents = [int(x, 16) for x in re.findall(
        r"0x([0-9A-Fa-f]{6})",
        re.search(r"ACCENTS\s*=\s*\[(.*?)\]", txt, re.S).group(1))]
    # Folder badge colour: APP_ACCENTS table id -> RGB (same lookup as _bake_tile on
    # board).
    CFG["app_accents"] = dict(
        (k, int(v, 16)) for k, v in re.findall(
            r'"([a-z_]+)":\s*0x([0-9A-Fa-f]{6})',
            re.search(r"APP_ACCENTS\s*=\s*\{(.*?)\}", txt, re.S).group(1)))
    block = re.search(r"APPS\s*=\s*\[(.*?)\n]", txt, re.S).group(1)
    apps = []
    for e in re.findall(r"\{.*?\}", block, re.S):
        i = re.search(r'"id"\s*:\s*"([^"]+)"', e)
        n = re.search(r'"name"\s*:\s*"([^"]+)"', e)
        c = re.search(r'"icon"\s*:\s*"([^"]+)"', e)
        if i and n:
            apps.append({"id": i.group(1), "name": n.group(1),
                         "icon": c.group(1) if c else None,
                         "folder": '"folder": True' in e})
    return ver, accents, apps


def to_xrgb8888(img):
    """RGBA -> XRGB8888 byte stream (little-endian memory order B,G,R,X, X = 255)."""
    w, h = img.size
    px = list(img.convert("RGBA").getdata())
    raw = bytearray(w * h * 4)
    for i, (r, g, b, _a) in enumerate(px):
        o = i * 4
        raw[o] = b
        raw[o + 1] = g
        raw[o + 2] = r
        raw[o + 3] = 255
    return bytes(raw)


def bake(app, idx, accents):
    tile = CFG["tile"]
    icon_size = CFG["icon"]
    accent = accents[idx % len(accents)]
    img = Image.new("RGBA", (tile, tile), CFG["bg"] + (255,))
    d = ImageDraw.Draw(img)
    d.rounded_rectangle([0, 0, tile - 1, tile - 1], radius=CFG["radius"],
                        fill=CFG["btn"] + (255,),
                        outline=CFG["border"] + (255,),
                        width=max(1, CFG["border_w"]))
    font = ImageFont.truetype(FONT_PATH, CFG["font"])

    if app["icon"]:
        p = os.path.join(GUI, app["icon"])
        if os.path.exists(p):
            ic = Image.open(p).convert("RGBA")
            ic = ic.resize((icon_size, icon_size), Image.LANCZOS)
            img.paste(ic, ((tile - icon_size) // 2, 2), ic)
    else:
        # Accent circular badge plus the first letter (placeholder apps; the symbol
        # font cannot be reproduced on the PC)
        bx = (tile - BADGE_SIZE) // 2
        by = (tile - 30 - BADGE_SIZE) // 2
        d.ellipse([bx, by, bx + BADGE_SIZE - 1, by + BADGE_SIZE - 1],
                  fill=(accent >> 16 & 255, accent >> 8 & 255, accent & 255, 255))
        letter = app["name"][0].upper()
        lfont = ImageFont.truetype(FONT_PATH, 44)
        bb = d.textbbox((0, 0), letter, font=lfont)
        tw, th = bb[2] - bb[0], bb[3] - bb[1]
        cx = bx + BADGE_SIZE // 2
        cy = by + BADGE_SIZE // 2
        d.text((cx - tw // 2 - bb[0], cy - th // 2 - bb[1]), letter,
               font=lfont, fill=(255, 255, 255, 255))

    # Name text: the ink bottom aligns to tile-NAME_BOTTOM_MARGIN.
    # Note that textbbox's origin is the ascender line, so ink bottom = draw y + bb[3];
    # using y = tile-2-(bb[3]-bb[1]) shifts everything down by bb[1] pixels and clips
    # the bottom (observed).
    bb = d.textbbox((0, 0), app["name"], font=font)
    tw = bb[2] - bb[0]
    d.text(((tile - tw) // 2 - bb[0], tile - CFG["name_margin"] - bb[3]),
           app["name"], font=font, fill=CFG["txt"] + (255,))

    # Folder badge: three dots in the bottom-right corner (same position, size, and
    # colour lookup as _bake_tile on board).
    if app.get("folder"):
        c = CFG["app_accents"].get(app["id"], 0x9CA3AF)
        rgb = (c >> 16 & 255, c >> 8 & 255, c & 255, 255)
        for di in range(3):
            x = tile - 46 + di * 14
            y = tile - 40
            d.ellipse([x, y, x + 9, y + 9], fill=rgb)
    return img


def main():
    ver, accents, apps = parse_gui()
    os.makedirs(CACHE, exist_ok=True)
    print("BAKE_VER={} TILE={} ICON={} FONT={} radius={} apps={}".format(
        ver, CFG["tile"], CFG["icon"], CFG["font"], CFG["radius"], len(apps)))
    for idx, app in enumerate(apps):
        img = bake(app, idx, accents)
        raw = to_xrgb8888(img)
        name = app["name"].encode()
        tile = CFG["tile"]
        header = (b"TILE2" + bytes([ver]) +
                  tile.to_bytes(2, "little") + tile.to_bytes(2, "little") +
                  CF_XRGB8888.to_bytes(2, "little") +
                  len(name).to_bytes(2, "little") + name +
                  bytes([1 if app.get("folder") else 0]))
        path = os.path.join(CACHE, "tile_{}.bin".format(app["id"]))
        with open(path, "wb") as f:
            f.write(header + raw)
        print("{:14s} {:10s} -> tile_{}.bin ({}B)".format(
            app["id"], app["name"], app["id"], len(header) + len(raw)))
    print("done ->", CACHE)


if __name__ == "__main__":
    main()
