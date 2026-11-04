# Measure the two UI CJK faces and render a montage of localized app screens.
# Language is forced in memory (save=False) so the board's stored setting is not
# changed. Run with --lvgl.
import sys

sys.path.insert(0, "/sdcard/factory/gui")
for _name in list(sys.modules):
    if _name.split(".")[0] in ("core", "apps", "ui", "shell", "audio", "net",
                               "launcher", "hardware"):
        del sys.modules[_name]

import gc
import time
import ubinascii
import uctypes

import lvgl as lv

from core import config
from core import i18n
from core import font_pool


def kb():
    return gc.mem_free() // 1024


print("[FONT] free before: {}KB".format(kb()))
for name in ("ui_cjk_16.bin", "ui_cjk_28.bin"):
    t0 = time.ticks_ms()
    f = font_pool.get(name)
    print("[FONT] {} {}ms ok={} free={}KB".format(
        name, time.ticks_diff(time.ticks_ms(), t0), f is not None, kb()))


def shot(tag):
    for _ in range(3):
        lv.task_handler()
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
        if self.ok_pressed:
            self.ok_pressed = False
            return True
        return False

    def report_unavailable(self, message):
        self.unavailable_msg = message


def open_app(module_name, tag):
    """Build the app onto a fresh screen and capture it."""
    config.load()
    i18n.init()
    mod = __import__(module_name, None, None, ["create_app"])
    scr = lv.obj()
    scr.set_style_bg_color(lv.color_hex(0x12141A), 0)
    lv.scr_load(scr)
    content = lv.obj(scr)
    content.set_size(800, 424)
    content.align(lv.ALIGN.TOP_MID, 0, 56)
    content.set_style_pad_all(0, 0)
    content.set_style_border_width(0, 0)
    content.set_style_bg_opa(lv.OPA.TRANSP, 0)
    app = None
    try:
        app = mod.create_app(content, Ctx())
        for _ in range(3):
            lv.task_handler()
            time.sleep_ms(30)
        shot(tag)
    except BaseException as error:
        print("[APP] {} failed: {}".format(module_name, error))
        sys.print_exception(error)
    finally:
        if app is not None:
            try:
                app.destroy()
            except BaseException as error:
                print("[APP] {} destroy: {}".format(module_name, error))
    lv.scr_load(lv.obj())
    for _ in range(2):
        lv.task_handler()


# Settings, Battery, Audio and Gallery: no camera needed, so they render safely.
config.set("ui.language", "zh", save=False)
i18n.init()
print("[I18N] lang={}".format(i18n.lang()))
for mod, tag in (("apps.app_settings", "settings_zh"),
                 ("apps.app_battery", "battery_zh"),
                 ("apps.app_audio", "audio_zh"),
                 ("apps.app_network", "network_zh"),
                 ("apps.app_gallery", "gallery_zh"),
                 ("apps.app_touch", "touch_zh")):
    open_app(mod, tag)

# The string table itself: report a few lookups so a missing key is visible
# without reading a screenshot.
for key in ("settings.language", "battery.present", "audio.sub_none",
            "ai.frozen", "rec.title_plain"):
    print("[T] {} = {}".format(key, i18n.t(key)))

print("[FONT] free at end: {}KB".format(kb()))
print("PROBE_DONE")
