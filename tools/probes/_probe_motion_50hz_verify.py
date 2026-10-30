# Five-second frame-rate statistics through the real launcher, plus a
# snapshot for visual confirmation.

import sys
import time
import ubinascii
import uctypes

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("apps.app_motion", "apps", "apps.error", "launcher"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv

src_file = open("/sdcard/factory/gui/launcher.py")
gui_src = src_file.read()
src_file.close()
del src_file

G = {"__name__": "gui_probe"}
exec(compile(gui_src, "launcher.py", "exec"), G)

launcher = G["Launcher"]()
meta = None
for item in G["APPS"]:
    if item["id"] == "motion":
        meta = item
        break

t0 = time.ticks_ms()
launcher.open_app(meta)
print("open_app {}ms".format(time.ticks_diff(time.ticks_ms(), t0)))
app = launcher.active_app
print("ROWS hist={} live={}".format(len(app._row_labels), len(app._live_labels)))

t_start = time.ticks_ms()
n0 = app._samples
gaps = []
last_n = n0
last_t = t_start
while time.ticks_diff(time.ticks_ms(), t_start) < 5000:
    launcher.active_app.step()
    lv.task_handler()
    if launcher.active_app._samples != last_n:
        now = time.ticks_ms()
        gaps.append(time.ticks_diff(now, last_t))
        last_t = now
        last_n = launcher.active_app._samples

elapsed = time.ticks_diff(time.ticks_ms(), t_start)
total = launcher.active_app._samples - n0
gaps = gaps[1:]
gaps.sort()
print("samples={} in {}ms -> {:.2f} Hz".format(
    total, elapsed, 1000.0 * total / elapsed))
if gaps:
    print("interval ms: min={} p50={} p95={} max={}".format(
        gaps[0], gaps[len(gaps) // 2], gaps[int(len(gaps) * 0.95)], gaps[-1]))
print("app rate display = {} Hz".format(app._rate))

dsc = lv.snapshot_take(lv.scr_act(), lv.COLOR_FORMAT.ARGB8888)
w = dsc.header.w
h = dsc.header.h
ptr = int.from_bytes(bytes(memoryview(dsc.data)), "little")
raw = uctypes.bytearray_at(ptr, w * h * 4)
out = bytearray()
for y in range(h):
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
dsc.buf_free()

print("SHOT motion50 %d %d %d %d" % (w, h, w * h * 4, len(out)))
b64 = ubinascii.b2a_base64(bytes(out)).decode().strip()
for i in range(0, len(b64), 800):
    print("B64 " + b64[i:i + 800])
print("SHOTEND motion50")
print("PROBE DONE")
