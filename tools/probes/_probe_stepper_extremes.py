# Stepper at 100% and 20%: no overlap between value and the minus button.
#
# Run through tools/_run_remote.py with --lvgl.
import sys

sys.path.insert(0, "/sdcard/factory/gui")

import apps
for _m in ("apps.app_settings", "config"):
    try:
        del sys.modules[_m]
    except KeyError:
        pass
try:
    delattr(apps, "app_settings")
except Exception:
    pass

import lvgl as lv
import config

config.PATH = "/data/settings/_probe_stepper.json"

from apps import app_settings


class Ctx:
    width = 800
    height = 424
    content_x = 0
    content_y = 56
    app_name = "Settings"
    touch = None

    def request_exit(self):
        pass


def build():
    scr = lv.obj()
    scr.set_style_bg_color(lv.color_hex(0x12141A), 0)
    scr.set_style_bg_opa(lv.OPA.COVER, 0)
    content = lv.obj(scr)
    content.set_size(800, 424)
    content.align(lv.ALIGN.TOP_MID, 0, 56)
    content.set_style_bg_opa(lv.OPA.TRANSP, 0)
    content.set_style_border_width(0, 0)
    content.set_style_pad_all(0, 0)
    content.clear_flag(lv.obj.FLAG.SCROLLABLE)
    lv.scr_load(scr)
    app = app_settings.create_app(content, Ctx())
    for _ in range(4):
        lv.task_handler()
    try:
        app.scroll.scroll_to_y(150, 0)
    except Exception:
        pass
    for _ in range(4):
        lv.task_handler()
    return scr, app


def shot(tag):
    import ubinascii
    import uctypes

    dsc = lv.snapshot_take(lv.scr_act(), lv.COLOR_FORMAT.ARGB8888)
    w, h = dsc.header.w, dsc.header.h
    ptr = int.from_bytes(bytes(memoryview(dsc.data)), "little")
    raw = uctypes.bytearray_at(ptr, w * h * 4)
    out = bytearray()
    for y in range(h):
        base = y * w * 4
        run = 0
        pb = pg = pr = -1
        for x in range(w):
            o = base + x * 4
            b = raw[o]
            g = raw[o + 1]
            r = raw[o + 2]
            if b == pb and g == pg and r == pr:
                run += 1
                if run == 255:
                    out.append(254)
                    out.append(pb)
                    out.append(pg)
                    out.append(pr)
                    run = 0
            else:
                if run:
                    out.append(run - 1)
                    out.append(pb)
                    out.append(pg)
                    out.append(pr)
                pb, pg, pr = b, g, r
                run = 1
        if run:
            out.append(run - 1)
            out.append(pb)
            out.append(pg)
            out.append(pr)
    print("SHOT %s %d %d %d %d" % (tag, w, h, len(raw), len(out)))
    b64 = ubinascii.b2a_base64(bytes(out)).decode().strip()
    for i in range(0, len(b64), 800):
        print("B64 " + b64[i:i + 800])
    print("SHOTEND " + tag)
    try:
        dsc.buf_free()
    except Exception:
        pass


for value, tag in ((100, "full"), (20, "low")):
    config._values = None
    config.load()
    config.set("display.brightness", value, save=False)
    scr, app = build()
    item, vlabel = app._steppers[0]
    card = vlabel.get_parent()
    minus_x = None
    for i in range(card.get_child_cnt()):
        ch = card.get_child(i)
        if type(ch).__name__ == "btn":
            minus_x = ch.get_x()
            break
    print("CASE", tag, "value", vlabel.get_text(),
          "value_x", vlabel.get_x(), "w", vlabel.get_width(),
          "value_right", vlabel.get_x() + vlabel.get_width(),
          "minus_x", minus_x)
    shot("step_" + tag)
    # Never delete the active screen: switch to a scratch screen first, then drop
    # the one the app was built on.
    scratch = lv.obj()
    lv.scr_load(scratch)
    for _ in range(2):
        lv.task_handler()
    scr.delete()

try:
    import os
    os.remove(config.PATH)
except Exception:
    pass
print("PROBE_DONE")
