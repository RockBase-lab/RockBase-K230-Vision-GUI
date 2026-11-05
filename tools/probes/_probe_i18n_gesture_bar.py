# Exercise GestureApp._update_stat with stub labels, so the fix for the second
# status-bar field (a label built from "--" that later gets a Chinese gesture
# name) is verified on the real code path. Run with --lvgl.
import sys

sys.path.insert(0, "/sdcard/factory/gui")
for _name in list(sys.modules):
    if _name.split(".")[0] in ("core", "apps", "ui", "shell", "launcher",
                               "hardware", "audio", "net"):
        del sys.modules[_name]

import lvgl as lv
import time
import ubinascii
import uctypes

from core import config
from core import i18n


def shot(tag):
    for _ in range(3):
        lv.task_handler()
        time.sleep_ms(30)
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


config.load()
config.set("ui.language", "zh", save=False)
i18n.init()
print("[I18N] lang={} fist={}".format(i18n.lang(), i18n.t("gesture.fist")))

import apps.app_gesture as g

scr = lv.obj()
scr.set_style_bg_color(lv.color_hex(0x12141A), 0)
lv.scr_load(scr)

bar_font = getattr(lv, "font_montserrat_16", None)


def make_label(text, x):
    lab = lv.label(scr)
    lab.set_text(text)
    lab.set_style_text_color(lv.color_hex(0xFFFFFF), 0)
    if bar_font is not None:
        lab.set_style_text_font(bar_font, 0)
    lab.set_pos(x, 20)
    return lab


class Shim:
    pass


app = Shim()
# These three mirror __init__: the fps and gesture fields start as ASCII, the hint
# already goes through font_for.
app.stat_fps = make_label("FPS -", 40)
app.stat_gesture = make_label("--", 200)
hint_text = i18n.t("ai.show_hand")
app.stat_hint = make_label(hint_text, 420)
f = i18n.font_for(hint_text, bar_font)
if f is not None:
    app.stat_hint.set_style_text_font(f, 0)
app.fps_text = "24"
app.frozen = False
app.last_gesture = "fist"

g.GestureApp._update_stat(app)
print("[G] gesture field -> {}".format(app.stat_gesture.get_text()))

# A second pass with a camelCase token, which the table stores lower case.
app.last_gesture = "thumbUp"
g.GestureApp._update_stat(app)
print("[G] gesture field -> {}".format(app.stat_gesture.get_text()))

# End on a Chinese token so the capture shows the rendered glyphs.
app.last_gesture = "fist"
g.GestureApp._update_stat(app)
print("[G] gesture field -> {}".format(app.stat_gesture.get_text()))

shot("gesture_bar_zh")
print("PROBE_DONE")
