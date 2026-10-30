# Dashboard geometry and flush cost per frame.

import gc
import sys
import time

import lvgl as lv

for _k in ("apps.app_imu", "app_imu", "apps"):
    if _k in sys.modules:
        del sys.modules[_k]
sys.path.insert(0, "/sdcard/factory/gui")

import apps.app_imu as mod

class Ctx(object):
    width = 800
    height = 424
    touch = None

    def take_ok(self):
        return False

    def report_unavailable(self, msg):
        pass

scr = lv.obj()
content = lv.obj(scr)
content.set_size(800, 424)
content.align(lv.ALIGN.TOP_MID, 0, 56)
content.set_style_bg_opa(lv.OPA.TRANSP, 0)
content.set_style_border_width(0, 0)
content.set_style_pad_all(0, 0)
content.clear_flag(lv.obj.FLAG.SCROLLABLE)
lv.scr_load(scr)

app = mod.ImuApp(content, Ctx())

def flush(tag):
    t0 = time.ticks_us()
    lv.refr_now(lv.disp_get_default())
    return time.ticks_diff(time.ticks_us(), t0) / 1000.0

t0 = time.ticks_us()
for i in range(20):
    app._update_dash(10.0 * i, 2.0, 90.0)
    app._update_dash(0.0, 0.0, 180.0)
t_geom = time.ticks_diff(time.ticks_us(), t0) / 1000.0 / 40.0
print("[M1] _update_dash geometry only: %.2f ms/frame" % t_geom)

app._update_dash(0.0, 0.0, 180.0)
flush("init")
t0 = time.ticks_us()
N = 10
for i in range(N):
    app._update_dash(3.0 * i, 1.0, 180.0)
    lv.refr_now(lv.disp_get_default())
t_all = time.ticks_diff(time.ticks_us(), t0) / 1000.0 / N
print("[M2] _update_dash + flush: %.2f ms/frame (-> %.0f Hz max)"
      % (t_all, 1000.0 / t_all if t_all > 0 else 0))

def bench_geom(fn, tag, n=10):
    fn()
    lv.refr_now(lv.disp_get_default())
    t0 = time.ticks_us()
    for i in range(n):
        fn()
        lv.refr_now(lv.disp_get_default())
    ms = time.ticks_diff(time.ticks_us(), t0) / 1000.0 / n
    print("[M3] %-22s %6.2f ms" % (tag, ms))
    return ms

import math
def ground_only():
    sr, cr = 0.0, 1.0
    half = mod.DASH_GROUND_W * 0.5
    gx, gy = mod.DASH_CX, mod.DASH_CY + half
    t = mod.DASH_LINE_T
    mod._set_pts(app._d_ground, [
        {"x": int(gx - t * cr), "y": int(gy + t * sr)},
        {"x": int(gx + t * cr), "y": int(gy - t * sr)}])

def ladder_only():
    for i, dp in enumerate((-30, -20, -10, 10, 20, 30)):
        y = mod.DASH_CY + dp * mod.DASH_PPD
        mod._set_pts(app._d_rungs[i], [
            {"x": 350, "y": int(y)}, {"x": 450, "y": int(y)}])

def ticks_only():
    for i in range(11):
        mod._set_pts(app._d_ticks[i], [
            {"x": 300 + i * 20, "y": 100}, {"x": 300 + i * 20, "y": 112}])

def tape_only():
    for i in range(21):
        x = 100 + i * 30
        mod._set_pts(app._d_tape_ticks[i], [{"x": x, "y": 368},
                                            {"x": x, "y": 390}])

bench_geom(ground_only, "ground thick line")
bench_geom(ladder_only, "6 ladder rungs")
bench_geom(ticks_only, "11 arc ticks")
bench_geom(tape_only, "21 tape ticks")

def rate_on(page, label):
    app._set_page(page)
    for _ in range(30):
        app.step()
        lv.task_handler()
        time.sleep_ms(21)
    base = app._samples
    t0 = time.ticks_ms()
    n = 0
    while time.ticks_diff(time.ticks_ms(), t0) < 2000:
        app.step()
        lv.task_handler()
        n += 1
    el = time.ticks_diff(time.ticks_ms(), t0)
    got = app._samples - base
    print("[M4] page %s: %d samples / %d ms = %.1f Hz (loop calls=%d)"
          % (label, got, el, got * 1000.0 / el, n))

rate_on(mod.PAGE_DASH, "1 dash")
rate_on(mod.PAGE_RAW, "2 raw")
rate_on(mod.PAGE_ATT, "3 att")

gc.collect()
print("[M5] mem_free: %d" % gc.mem_free())
app.destroy()
print("PROBE DONE")
