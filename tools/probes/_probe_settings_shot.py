# Headless snapshot of the Settings app: screen build + RLE/base64 dump.
# Run through tools/_run_remote.py with --lvgl.
import sys

sys.path.insert(0, "/sdcard/factory/gui")

# `del sys.modules` alone leaves the stale object reachable through the package
# attribute, and that object keeps its own `config` binding. Clear both.
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
from core import config
from apps import app_settings

# Assert one shared module before trusting anything the app renders.
print("SAME_MODULE", app_settings.config is config) 


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
lv.task_handler()
lv.task_handler()
lv.task_handler()

for kind, label, _key in app._live:
    print("LIVE_ROW", kind, label.get_text())

# Scroll to the SYSTEM section so the heap row is on screen.
try:
    app.scroll.scroll_to_y(470, 0)
except Exception as e:
    print("SCROLL_ERR", repr(e))
for _ in range(4):
    lv.task_handler()

import ubinascii
import uctypes

dsc = lv.snapshot_take(lv.scr_act(), lv.COLOR_FORMAT.ARGB8888)
w, h = dsc.header.w, dsc.header.h
ptr = int.from_bytes(bytes(memoryview(dsc.data)), "little")
raw = uctypes.bytearray_at(ptr, w * h * 4)   # B, G, R, A

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

print("SHOT settings %d %d %d %d" % (w, h, len(raw), len(out)))
b64 = ubinascii.b2a_base64(bytes(out)).decode().strip()
for i in range(0, len(b64), 800):
    print("B64 " + b64[i:i + 800])
print("SHOTEND settings")
try:
    dsc.buf_free()
except Exception:
    pass
