# Single-frame cost and 20 ms loop achievement for the live row.
# Compares refreshing 7 labels against refreshing all 70.

import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("apps.app_motion", "apps", "app_error"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv
from apps.app_motion import MotionApp

scr = lv.obj()
scr.set_size(800, 480)
scr.set_style_bg_color(lv.color_hex(0x12141A), 0)
scr.set_style_bg_opa(lv.OPA.COVER, 0)
scr.set_style_pad_all(0, 0)
scr.clear_flag(lv.obj.FLAG.SCROLLABLE)
content = lv.obj(scr)
content.set_size(800, 424)
content.set_pos(0, 56)
content.set_style_bg_opa(lv.OPA.TRANSP, 0)
content.set_style_border_width(0, 0)
content.set_style_pad_all(0, 0)
content.clear_flag(lv.obj.FLAG.SCROLLABLE)

class Ctx(object):
    width = 800
    height = 424
    touch = None
    app_name = "Motion"
    unavailable_msg = None

    def report_unavailable(self, msg):
        self.unavailable_msg = msg

ctx = Ctx()
app = MotionApp(content, ctx)
for _ in range(3):
    app.step()
    time.sleep_ms(210)
lv.scr_load(scr)
for _ in range(3):
    lv.task_handler()

disp = lv.disp_get_default()
rowN = app._row_labels[-1]
allrows = app._row_labels

def refr():
    lv.refr_now(disp)

def bench(tag, fn, n=12):
    fn(0)
    refr()
    out = []
    for i in range(n):
        t0 = time.ticks_us()
        fn(i + 1)
        refr()
        out.append(time.ticks_diff(time.ticks_us(), t0) / 1000.0)
    lo = min(out)
    hi = max(out)
    avg = sum(out) / len(out)
    print("{}: min={:.1f} avg={:.1f} max={:.1f} ms  -> {:.0f} Hz max".format(
        tag, lo, avg, hi, 1000.0 / avg))
    return avg

def only_last(i):
    for k, lb in enumerate(rowN):
        lb.set_text("{}.{:04d}".format(i, k))

def all_rows(i):
    for row in allrows:
        for k, lb in enumerate(row):
            lb.set_text("{}.{:04d}".format(i, k))

bench("7 labels (last row)", only_last)
bench("70 labels (all rows)", all_rows)
bench("7 labels again", only_last)

print("--- 20ms loop (last row only) ---")
t_start = time.ticks_ms()
n = 0
worst = 0
prev = t_start
while time.ticks_diff(time.ticks_ms(), t_start) < 3000:
    now = time.ticks_ms()
    if time.ticks_diff(now, prev) >= 20:
        gap = time.ticks_diff(now, prev)
        if gap > worst:
            worst = gap
        prev = now
        for k, lb in enumerate(rowN):
            lb.set_text("v{}".format(n % 10000))
        refr()
        n += 1
    lv.task_handler()
elapsed = time.ticks_diff(time.ticks_ms(), t_start)
print("loop frames={} elapsed={}ms -> {:.1f} Hz | worst gap={}ms".format(
    n, elapsed, 1000.0 * n / elapsed, worst))

print("--- 20ms loop (all rows) ---")
t_start = time.ticks_ms()
n = 0
worst = 0
prev = t_start
while time.ticks_diff(time.ticks_ms(), t_start) < 3000:
    now = time.ticks_ms()
    if time.ticks_diff(now, prev) >= 20:
        gap = time.ticks_diff(now, prev)
        if gap > worst:
            worst = gap
        prev = now
        for row in allrows:
            for lb in row:
                lb.set_text("v{}".format(n % 10000))
        refr()
        n += 1
    lv.task_handler()
elapsed = time.ticks_diff(time.ticks_ms(), t_start)
print("loop frames={} elapsed={}ms -> {:.1f} Hz | worst gap={}ms".format(
    n, elapsed, 1000.0 * n / elapsed, worst))

print("PROBE DONE")
