# Resident bitmap-font pool: lv.font_load once per file per process.
#
# The GUI uses five baked .bin fonts and their combined load cost is 5.5 s, almost
# all of it the 4036-glyph OCR font (4.96 s). Loading them at boot and sharing the
# handles keeps every app's open path free of that cost.
#
# Measured on board: lv.font_load stores the font in LVGL's heap, not the Python
# heap, but the cost is visible in gc.mem_free (334 KB for all five, of which the
# OCR font is 274 KB).
#
# The path needs the LVGL drive prefix "A:"; without it font_load returns None
# silently rather than raising.

import gc
import time

import lvgl as lv

from core import paths

_pool = {}


def bin_path(name):
    return "A:" + paths.asset_path("fonts", name)


def get(name):
    """Return the loaded font for a file name under assets/fonts, or None."""
    font = _pool.get(name)
    if font is not None:
        return font
    load = getattr(lv, "font_load", None)
    if load is None:
        return None
    path = bin_path(name)
    try:
        t0 = time.ticks_ms()
        font = load(path)
    except BaseException as error:
        print("[FONT] load failed {}: {}".format(name, error))
        return None
    if font is None:
        print("[FONT] load returned None: {}".format(path))
        return None
    _pool[name] = font
    print("[FONT] {} {}ms cached={} free={}KB".format(
        name, time.ticks_diff(time.ticks_ms(), t0), len(_pool),
        gc.mem_free() // 1024))
    return font


def is_loaded(name):
    return name in _pool


def preload(names, on_progress=None):
    """Load every name once, reporting progress after each. Returns (ok, missing)."""
    total = len(names)
    ok = 0
    missing = 0
    for i in range(total):
        name = names[i]
        if get(name) is not None:
            ok += 1
        else:
            missing += 1
        if on_progress is not None:
            try:
                on_progress(i + 1, total, name)
            except BaseException as error:
                print("[FONT] progress callback failed: {}".format(error))
    return ok, missing
