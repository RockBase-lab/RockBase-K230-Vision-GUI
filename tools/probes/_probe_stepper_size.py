# Measure and capture the Settings stepper: button sizes, offsets, value font.
#
# Run through tools/_run_remote.py with --lvgl.
import sys

sys.path.insert(0, "/sdcard/factory/gui")

import apps
for _m in ("core", "apps.app_settings", "core.config"):
    try:
        del sys.modules[_m]
    except KeyError:
        pass
try:
    delattr(apps, "app_settings")
except Exception:
    pass

import lvgl as lv
from apps import app_settings

print("STEP_ROW_H", app_settings.STEP_ROW_H)
print("STEP_BTN_W", app_settings.STEP_BTN_W)
print("STEP_BTN_H", app_settings.STEP_BTN_H)
print("STEP_SLOT", app_settings.STEP_SLOT)
print("VALUE_OFFSET", app_settings.SettingsApp._value_offset(None))
print("BTN0_OFFSET", app_settings.SettingsApp._btn_offset(None, 0))
print("BTN1_OFFSET", app_settings.SettingsApp._btn_offset(None, 1))


class Ctx:
    width = 800
    height = 424
    content_x = 0
    content_y = 56
    app_name = "Settings"
    touch = None

    def request_exit(self):
        pass


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

print("STEP_FONT", app.step_font)
print("VALUE_FONT", app.value_font)

item, vlabel = app._steppers[0]
print("VALUE_TEXT", vlabel.get_text())
print("VALUE_W_H", vlabel.get_width(), vlabel.get_height())
print("VALUE_Y", vlabel.get_y())

# Find the two buttons inside the stepper card to report real geometry.
card = vlabel.get_parent()
kids = card.get_child_cnt()
print("CARD_CHILDREN", kids, "H", card.get_height())
for i in range(kids):
    ch = card.get_child(i)
    tag = type(ch).__name__
    try:
        print("  CHILD", i, tag, "x", ch.get_x(), "y", ch.get_y(),
              "w", ch.get_width(), "h", ch.get_height())
    except BaseException as e:
        print("  CHILD", i, tag, "err", repr(e))

# Scroll so the DISPLAY section sits mid-screen and capture.
try:
    app.scroll.scroll_to_y(150, 0)
except Exception as e:
    print("SCROLL_ERR", repr(e))
for _ in range(4):
    lv.task_handler()

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

print("SHOT stepper %d %d %d %d" % (w, h, len(raw), len(out)))
b64 = ubinascii.b2a_base64(bytes(out)).decode().strip()
for i in range(0, len(b64), 800):
    print("B64 " + b64[i:i + 800])
print("SHOTEND stepper")
try:
    dsc.buf_free()
except Exception:
    pass
print("PROBE_DONE")
