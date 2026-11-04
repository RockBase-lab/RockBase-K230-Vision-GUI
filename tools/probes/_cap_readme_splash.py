# Captures the boot progress screen at a realistic mid-boot state, for the README
# feature section. The splash is driven directly instead of running boot_load, so
# the capture costs no model loads and cannot disturb the deployed environment.
#
# Output is one SHOT/B64 block; decode with tools/decode_lv_snapshot.py.

import sys
import time
import ubinascii
import uctypes

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("core", "ui.splash", "core.font_pool", "core.version"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv
from ui import splash as splash_ui

splash = splash_ui.Splash()
# 14 of 16 models plus the dictionary and the fonts: roughly the state a user sees
# for the longest stretch, and it shows a partially filled bar rather than 0 or 100.
splash.set(15400, 18000, "Loading models 14/16", "yolov8n_seg_320.kmodel")
splash.note("Wi-Fi: not configured")
for _ in range(4):
    lv.task_handler()
    time.sleep_ms(60)

dsc = lv.snapshot_take(lv.scr_act(), lv.COLOR_FORMAT.ARGB8888)
w, h = dsc.header.w, dsc.header.h
ptr = int.from_bytes(bytes(memoryview(dsc.data)), "little")
raw = uctypes.bytearray_at(ptr, w * h * 4)
out = bytearray()
y = 0
while y < h:
    base = y * w * 4
    run = 1
    b0 = raw[base]
    g0 = raw[base + 1]
    r0 = raw[base + 2]
    for x in range(1, w):
        o = base + x * 4
        b = raw[o]
        g = raw[o + 1]
        r = raw[o + 2]
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
    y += 1
dsc.buf_free()

print("SHOT boot_splash {} {} {} {}".format(w, h, w * h * 4, len(out)))
b64 = ubinascii.b2a_base64(bytes(out)).decode().strip()
for i in range(0, len(b64), 800):
    print("B64 " + b64[i:i + 800])
print("SHOTEND boot_splash")
print("DONE")
