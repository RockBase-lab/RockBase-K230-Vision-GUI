# Splash screen snapshot: build the boot splash alone (no model preload) and
# dump the full screen, so the version label's position and legibility can be
# checked without racing a real boot. Decode with tools/decode_lv_snapshot.py.
# Run with --lvgl.

import sys

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("ui", "ui.splash", "version"):
    try:
        del sys.modules[_name]
    except KeyError:
        pass

import lvgl as lv
import ubinascii
import uctypes

from ui import splash as splash_ui
from version import GUI_VERSION

splash = splash_ui.Splash()
splash.set(50, 100, "Preparing desktop", "building home")
splash.note("Wi-Fi: connected 192.168.1.10")
for _ in range(4):
    lv.task_handler()

label = splash.version_label
x, y = label.get_x(), label.get_y()
w, h = label.get_width(), label.get_height()
print("VERSION text={!r} x={} y={} w={} h={} scr={}x{}".format(
    label.get_text(), x, y, w, h,
    lv.scr_act().get_width(), lv.scr_act().get_height()))
print("VERSION module={}".format(GUI_VERSION))

dsc = lv.snapshot_take(lv.scr_act(), lv.COLOR_FORMAT.ARGB8888)
sw, sh = dsc.header.w, dsc.header.h
ptr = int.from_bytes(bytes(memoryview(dsc.data)), "little")
raw = uctypes.bytearray_at(ptr, sw * sh * 4)   # B, G, R, A

out = bytearray()
for y in range(sh):
    base = y * sw * 4
    run = 0
    pb = pg = pr = -1
    for x in range(sw):
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

print("SHOT splash %d %d %d %d" % (sw, sh, len(raw), len(out)))
b64 = ubinascii.b2a_base64(bytes(out)).decode().strip()
for i in range(0, len(b64), 800):
    print("B64 " + b64[i:i + 800])
print("SHOTEND splash")
try:
    dsc.buf_free()
except Exception:
    pass
print("DONE")
