# Render the home screen and the Vision folder flyout in both languages, capture
# each, so the desktop i18n can be verified without touching the running GUI.
# Run with --lvgl. Language is set in memory only (save=False): the on-disk
# setting is never changed by this probe.
import sys

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("core", "launcher", "core.i18n", "core.config", "apps", "core.errors", "ui.beep",
              "ui.paging", "hardware.backlight", "core.font_pool", "core.model_pool", "core.ocr_dict",
              "hardware.camera_fov", "shell", "shell.tile_cache", "shell.registry",
              "shell.home", "shell.statusbar", "shell.boot", "shell.touchscreen"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv
import time
import ubinascii
import uctypes

from core import config
from core import i18n
from core import font_pool
from shell import tile_cache
from shell.registry import APPS
from shell.home import Launcher


def shot(tag):
    for _ in range(3):
        lv.task_handler()
        time.sleep_ms(40)
    dsc = lv.snapshot_take(lv.scr_act(), lv.COLOR_FORMAT.ARGB8888)
    w, h = dsc.header.w, dsc.header.h
    ptr = int.from_bytes(bytes(memoryview(dsc.data)), "little")
    raw = uctypes.bytearray_at(ptr, w * h * 4)
    out = bytearray()
    for y in range(h):
        base = y * w * 4
        run = 1
        b0, g0, r0 = raw[base], raw[base + 1], raw[base + 2]
        for x in range(1, w):
            o = base + x * 4
            b, g, r = raw[o], raw[o + 1], raw[o + 2]
            if b == b0 and g == g0 and r == r0 and run < 256:
                run += 1
            else:
                out.append(run - 1)
                out.append(b0)
                out.append(g0)
                out.append(r0)
                b0, g0, r0 = b, g, r
                run = 1
        out.append(run - 1)
        out.append(b0)
        out.append(g0)
        out.append(r0)
    dsc.buf_free()
    print("SHOT {} {} {} {} {}".format(tag, w, h, w * h * 4, len(out)))
    b64 = ubinascii.b2a_base64(bytes(out)).decode().strip()
    for i in range(0, len(b64), 800):
        print("B64 " + b64[i:i + 800])
    print("SHOTEND " + tag)


config.load()
tile_cache.ensure_cache_dir()

# Preload both UI faces: the 28px one for tile names and flyout titles, the 16px
# one for the member labels.
f = font_pool.get("ui_cjk_28.bin")
print("[I18N] ui_cjk_28 loaded:", f is not None)
f16 = font_pool.get("ui_cjk_16.bin")
print("[I18N] ui_cjk_16 loaded:", f16 is not None)

parent = lv.obj()


def render(lang):
    config.set("ui.language", lang, save=False)
    i18n.init()
    print("[I18N] rendering lang={} -> {}".format(lang, i18n.lang()))
    tile_cache.BAKED_TILES.clear()
    tile_cache.bake_all_tiles(parent)
    L = Launcher()
    L.build_home()
    shot("desk_" + lang)
    vision = [m for m in APPS if m["id"] == "vision"][0]
    L._open_folder(vision)
    shot("folder_" + lang)
    L._close_folder()
    # Tear the home down so the next language rebuilds cleanly. The empty screen
    # stays loaded: deleting the active screen leaves a dangling pointer and the
    # next build stalls with no output.
    if L.home_scr is not None:
        lv.scr_load(lv.obj())
        L.home_scr.delete()
        L.home_scr = None


render("en")
render("zh")
print("PROBE_DONE")
