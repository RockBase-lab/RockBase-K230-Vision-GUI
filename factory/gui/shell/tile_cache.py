# Baked home tiles: the cache format, the offline-compatible reader, and the
# one-off on-board bake.
#
# Why tiles are bitmaps at all: this firmware renders LVGL in DISP_RENDER_MODE.FULL,
# so every refresh repaints the whole screen, and tiny_ttf re-rasterises on each
# repaint (measured ~1.4s for 9 labels), which freezes a swipe. Each tile is
# therefore pre-baked into one XRGB8888 bitmap, and a full-screen repaint during a
# swipe becomes a handful of blits (measured ~0ms).
#
# Cache flow:
#   python tools/gen_tile_cache.py     offline bake on the PC (the normal path)
#   bake_all_tiles()                   on boot, read each entry; on a miss, bake once
#                                      and write it back
#
# Nothing here takes a directory from the caller: paths go through core.paths, so a
# module that moves cannot silently resolve the wrong one.

import os
import sys
import time
import gc
import uctypes

import lvgl as lv

from core import font_pool
from core import i18n
from core import paths
from ui.image import load as load_png
from shell import widgets
from shell.registry import APPS, ACCENTS, APP_ACCENTS
from shell.theme import (COL_BG, COL_TEXT, TILE_SIZE, TILE_BG, TILE_RADIUS,
                         TILE_BORDER, TILE_BORDER_W, NAME_COLOR, TILE_FONT_SIZE,
                         BAKE_VER)

# app id -> (img_dsc_t, raw_bytes). The raw bytes stay referenced next to the
# descriptor so the bitmap cannot be collected while a widget draws it.
BAKED_TILES = {}

# Tile label face: assets/fonts/Nunito-Bold-700.ttf through the firmware's own
# tiny_ttf. Loaded lazily, because a cache hit never needs it.
TILE_FONT = None
_tile_font_tried = False


def _cache_dir():
    return paths.asset_path("cache")


def ensure_cache_dir():
    try:
        os.mkdir(_cache_dir())
    except OSError:
        pass


def cache_path(meta):
    """One cache file per language: English keeps the historical name so an
    existing card never re-bakes; other languages get a suffix."""
    lang = i18n.lang()
    if lang == "en":
        return _cache_dir() + "/tile_{}.bin".format(meta["id"])
    return _cache_dir() + "/tile_{}_{}.bin".format(meta["id"], lang)


def load_tile_font():
    """Render Nunito Bold through tiny_ttf.

    The built-in fonts have no bold face, and a bin produced by lv_font_conv does
    not match this firmware's LVGL version (metrics read back fine but the bitmap
    alpha is distorted, making the text nearly invisible on a dark background --
    measured on board), which is why this goes through tiny_ttf instead.
    The path must carry the LVGL FS drive prefix A:; without it the call fails
    silently (measured on board).
    """
    global TILE_FONT, _tile_font_tried
    _tile_font_tried = True
    path = "A:" + paths.asset_path("fonts", "Nunito-Bold-700.ttf")
    try:
        f = lv.tiny_ttf_create_file(path, TILE_FONT_SIZE)
        if f is None:
            raise RuntimeError("tiny_ttf returned None")
        TILE_FONT = f
        print("[GUI] tile font loaded: Nunito Bold {}px (line_height={})".format(
            TILE_FONT_SIZE, getattr(f, "line_height", "?")))
    except BaseException as error:
        print("[GUI] tile font load failed ({}), fallback built-in".format(error))
        TILE_FONT = None


def tile_name(meta):
    """Display name for the current language; falls back to the APPS entry name."""
    return i18n.name(meta["id"], meta["name"])


def cjk_font(name=i18n.CJK_FONT):
    """Baked bitmap font covering the desktop CJK strings, or None."""
    try:
        return font_pool.get(name)
    except BaseException:
        return None


def tile_name_font(label):
    """Font for a tile name: the Latin tiny_ttf face has no CJK glyphs, so a
    localized label takes the baked ui_cjk_28 face instead (same 28px size)."""
    if i18n.needs_cjk(label):
        f = cjk_font()
        if f is not None:
            return f
    return TILE_FONT or widgets.font("font_dejavu_16_persian_hebrew",
                                     "font_montserrat_16", "font_montserrat_14")


def _snapshot_pixels(dsc):
    """Read the pixel bytes out of the img_dsc returned by snapshot.

    dsc.data is a C_Array whose buffer protocol exposes 8 bytes: the pixel buffer
    pointer (little endian). Take the pointer and batch-read with bytes_at
    (measured on board: 0ms). Calling addressof(dsc.data) instead yields the
    wrapper struct, not the pixels, so the pointer indirection is mandatory.
    """
    w, h = dsc.header.w, dsc.header.h
    size = w * h * 4
    ptr = int.from_bytes(bytes(memoryview(dsc.data)), "little")
    return w, h, dsc.header.cf, bytes(uctypes.bytes_at(ptr, size))


def _make_dsc(w, h, cf, raw):
    return lv.img_dsc_t({"header": {"cf": cf, "w": w, "h": h},
                         "data_size": len(raw), "data": raw})


def cache_load(meta):
    """Return (dsc, raw) on a cache hit, else None. Validates magic / BAKE_VER /
    size / name / folder flag; any mismatch invalidates the entry."""
    try:
        with open(cache_path(meta), "rb") as f:
            blob = f.read()
    except OSError:
        return None
    if len(blob) < 15 or blob[:5] != b"TILE2" or blob[5] != BAKE_VER:
        return None
    w = blob[6] | (blob[7] << 8)
    h = blob[8] | (blob[9] << 8)
    cf = blob[10] | (blob[11] << 8)
    nlen = blob[12] | (blob[13] << 8)
    name = blob[14:14 + nlen]
    folder_flag = blob[14 + nlen]
    raw = blob[15 + nlen:]
    # The folder flag takes part in validation: an entry switching between "folder"
    # and "plain app" invalidates its old tile automatically.
    if (name != tile_name(meta).encode() or
            folder_flag != (1 if meta.get("folder") else 0) or
            len(raw) != w * h * 4):
        return None
    return _make_dsc(w, h, cf, raw), raw


def cache_save(meta, w, h, cf, raw):
    name = tile_name(meta).encode()
    try:
        with open(cache_path(meta), "wb") as f:
            f.write(b"TILE2")
            f.write(bytes([BAKE_VER]))
            f.write(w.to_bytes(2, "little"))
            f.write(h.to_bytes(2, "little"))
            f.write(cf.to_bytes(2, "little"))
            f.write(len(name).to_bytes(2, "little"))
            f.write(name)
            f.write(bytes([1 if meta.get("folder") else 0]))
            f.write(raw)
    except OSError as error:
        print("[GUI] cache save '{}' failed: {}".format(meta["id"], error))


def bake_tile(parent, meta):
    """Render one tile and snapshot it into a bitmap; returns (w, h, cf, raw).

    The structure is background wrapper > rounded button > icon + label. The outer
    wrapper makes the rounded corners fill with the desktop colour under opaque
    XRGB8888, so the tile sits seamlessly on the wallpaper.
    Only called on a cache miss, which is why the TTF is loaded lazily.
    """
    if TILE_FONT is None and not _tile_font_tried:
        load_tile_font()
    wrap = lv.obj(parent)
    wrap.set_size(TILE_SIZE, TILE_SIZE)
    wrap.set_pos(0, 0)
    wrap.set_style_bg_color(lv.color_hex(COL_BG), 0)
    wrap.set_style_bg_opa(lv.OPA.COVER, 0)
    wrap.set_style_border_width(0, 0)
    wrap.set_style_radius(0, 0)
    wrap.set_style_pad_all(0, 0)
    wrap.clear_flag(lv.obj.FLAG.SCROLLABLE)

    btn = lv.obj(wrap)
    btn.set_size(TILE_SIZE, TILE_SIZE)
    btn.align(lv.ALIGN.CENTER, 0, 0)
    btn.set_style_bg_color(lv.color_hex(TILE_BG), 0)
    btn.set_style_bg_opa(lv.OPA.COVER, 0)
    btn.set_style_radius(TILE_RADIUS, 0)
    btn.set_style_border_width(TILE_BORDER_W, 0)
    btn.set_style_border_color(lv.color_hex(TILE_BORDER), 0)
    btn.set_style_pad_all(0, 0)
    widgets.clean(btn)

    accent = ACCENTS[APPS.index(meta) % len(ACCENTS)]
    icon_src = load_png(paths.gui_path(meta["icon"])) if meta.get("icon") else None
    if icon_src is not None:
        icon = lv.img(btn)
        icon.set_src(icon_src)
        icon.align(lv.ALIGN.TOP_MID, 0, 2)
    else:
        badge_size = 96
        badge = lv.obj(btn)
        badge.set_size(badge_size, badge_size)
        badge.align(lv.ALIGN.TOP_MID, 0, (TILE_SIZE - 30 - badge_size) // 2)
        badge.set_style_bg_color(lv.color_hex(accent), 0)
        badge.set_style_bg_opa(lv.OPA.COVER, 0)
        badge.set_style_radius(badge_size // 2, 0)
        badge.set_style_border_width(0, 0)
        sym = lv.label(badge)
        sym.set_text(meta["symbol"])
        sym.set_style_text_color(lv.color_hex(COL_TEXT), 0)
        f = widgets.font("font_montserrat_48", "font_montserrat_32",
                         "font_montserrat_24")
        if f:
            sym.set_style_text_font(f, 0)
        sym.center()

    label = tile_name(meta)
    name = lv.label(btn)
    name.set_text(label)
    name.set_style_text_color(lv.color_hex(NAME_COLOR), 0)
    f = tile_name_font(label)
    if f:
        name.set_style_text_font(f, 0)
    name.align(lv.ALIGN.BOTTOM_MID, 0, -2)

    # Folder tile: three-dot badge in the bottom-right corner. The accent comes from
    # that app's tint so it matches the icon colour; the name is centred at the
    # bottom and the badge sits in the empty bottom-right area, so the two do not
    # collide.
    if meta.get("folder"):
        dot_c = lv.color_hex(APP_ACCENTS.get(meta["id"], 0x9CA3AF))
        for di in range(3):
            dot = lv.obj(btn)
            dot.set_size(10, 10)
            dot.set_pos(TILE_SIZE - 46 + di * 14, TILE_SIZE - 40)
            dot.set_style_bg_color(dot_c, 0)
            dot.set_style_bg_opa(lv.OPA.COVER, 0)
            dot.set_style_radius(5, 0)
            dot.set_style_border_width(0, 0)
            widgets.no_hit(dot)

    try:
        wrap.update_layout()
    except BaseException:
        pass
    lv.task_handler()
    dsc = lv.snapshot_take(wrap, lv.COLOR_FORMAT.ARGB8888)
    w, h, cf, raw = _snapshot_pixels(dsc)
    try:
        dsc.buf_free()
    except BaseException:
        pass
    wrap.delete()
    return w, h, cf, raw


def bake_all_tiles(splash_parent):
    """Prepare every tile bitmap at boot: read the cache when it hits, otherwise
    bake and store it.

    Adding an app needs only one APPS entry: its cache is missing, so it is baked
    once automatically.
    """
    ensure_cache_dir()
    t0 = time.ticks_ms()
    hits = baked = 0
    for meta in APPS:
        cached = cache_load(meta)
        if cached is not None:
            BAKED_TILES[meta["id"]] = cached
            hits += 1
            continue
        try:
            w, h, cf, raw = bake_tile(splash_parent, meta)
            cache_save(meta, w, h, cf, raw)
            BAKED_TILES[meta["id"]] = (_make_dsc(w, h, cf, raw), raw)
            baked += 1
        except BaseException as error:
            print("[GUI] bake tile '{}' failed: {}".format(meta["id"], error))
            sys.print_exception(error)
    gc.collect()
    print("[GUI] tiles ready: {} cached + {} baked in {}ms | {}".format(
        hits, baked, time.ticks_diff(time.ticks_ms(), t0), widgets.mem_str()))


def reload_all():
    """Read every tile back in after release_home dropped the cache (no re-bake)."""
    t0 = time.ticks_ms()
    hits = 0
    for meta in APPS:
        cached = cache_load(meta)
        if cached is not None:
            BAKED_TILES[meta["id"]] = cached
            hits += 1
    gc.collect()
    print("[GUI] tiles reloaded: {}/{} in {}ms | {}".format(
        hits, len(APPS), time.ticks_diff(time.ticks_ms(), t0), widgets.mem_str()))
