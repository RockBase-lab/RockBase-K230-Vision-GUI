# README screenshots for the bilingual UI: the desktop and the Settings page in
# both languages. The language is set in memory only (save=False), so the board's
# stored setting is untouched. Run with --lvgl.
import sys

sys.path.insert(0, "/sdcard/factory/gui")
for _name in list(sys.modules):
    if _name.split(".")[0] in ("core", "apps", "ui", "shell", "launcher",
                               "hardware", "audio", "net"):
        del sys.modules[_name]

import gc
import time
import ubinascii
import uctypes

import lvgl as lv

from core import config
from core import i18n
from core import font_pool
from shell import tile_cache
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
    print("SHOT {} {} {} {} {}".format(tag, w, h, len(out), w * h * 4))
    b64 = ubinascii.b2a_base64(bytes(out)).decode().strip()
    for i in range(0, len(b64), 800):
        print("B64 " + b64[i:i + 800])
    print("SHOTEND " + tag)


class Ctx:
    width = 800
    height = 424
    screen_w = 800
    screen_h = 480
    content_x = 0
    content_y = 56
    app_name = ""
    want_exit = False
    touch = None
    ok_pressed = False
    ok_beep = True
    back_handler = None
    unavailable_msg = None

    def request_exit(self):
        self.want_exit = True

    def set_back_handler(self, handler):
        self.back_handler = handler

    def take_ok(self):
        return False

    def report_unavailable(self, message):
        self.unavailable_msg = message


def settings_shot(lang):
    """Build the Settings app in `lang` and capture it."""
    config.load()
    config.set("ui.language", lang, save=False)
    i18n.init()
    import apps.app_settings as mod
    scr = lv.obj()
    scr.set_style_bg_color(lv.color_hex(0x12141A), 0)
    lv.scr_load(scr)
    content = lv.obj(scr)
    content.set_size(800, 424)
    content.align(lv.ALIGN.TOP_MID, 0, 56)
    content.set_style_pad_all(0, 0)
    content.set_style_border_width(0, 0)
    content.set_style_bg_opa(lv.OPA.TRANSP, 0)
    app = mod.SettingsApp(content, Ctx())
    shot("settings_" + lang)
    try:
        app.destroy()
    except BaseException as error:
        print("[settings] destroy: {}".format(error))
    lv.scr_load(lv.obj())
    for _ in range(2):
        lv.task_handler()


config.load()
font_pool.get("ui_cjk_16.bin")
font_pool.get("ui_cjk_22.bin")
font_pool.get("ui_cjk_28.bin")
tile_cache.ensure_cache_dir()

# ---- desktop, both languages ----
parent = lv.obj()
for lang in ("en", "zh"):
    config.set("ui.language", lang, save=False)
    i18n.init()
    print("[CAP] desktop lang={}".format(i18n.lang()))
    tile_cache.BAKED_TILES.clear()
    tile_cache.bake_all_tiles(parent)
    L = Launcher()
    L.build_home()
    shot("desktop_" + lang)
    # Drop the home screen before the next language; the empty screen stays loaded
    # so the active screen is never deleted.
    lv.scr_load(lv.obj())
    L.home_scr.delete()
    L.home_scr = None
    gc.collect()

# ---- Settings, both languages ----
settings_shot("en")
settings_shot("zh")

print("[CAP] free {}KB".format(gc.mem_free() // 1024))
print("PROBE_DONE")
