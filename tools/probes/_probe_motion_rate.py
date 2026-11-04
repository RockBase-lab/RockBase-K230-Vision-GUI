# I2C read cost and step interval measurement.

import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("core", "apps.app_motion", "apps", "core.errors"):
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
lv.scr_load(scr)

times = []
samples = []
for _ in range(20):
    t0 = time.ticks_us()
    row = app.imu.read_sample()
    dt = time.ticks_diff(time.ticks_us(), t0)
    times.append(dt)
    samples.append(row)
print("I2C read_sample us: min={} max={} avg={}".format(
    min(times), max(times), sum(times) // len(times)))
print("I2C equivalent rate = {:.1f} Hz (if not throttled)".format(
    1000000.0 / (sum(times) / len(times))))

last_n = app._samples
marks = []
t_start = time.ticks_ms()
while time.ticks_diff(time.ticks_ms(), t_start) < 4000:
    app.step()
    lv.task_handler()
    if app._samples != last_n:
        last_n = app._samples
        marks.append(time.ticks_ms())

if len(marks) > 1:
    deltas = [time.ticks_diff(marks[i + 1], marks[i]) for i in range(len(marks) - 1)]
    deltas.sort()
    mid = deltas[len(deltas) // 2]
    print("STEP updates={} window={}ms".format(
        len(marks), time.ticks_diff(marks[-1], marks[0])))
    print("STEP interval ms: min={} p50={} max={} avg={:.1f}".format(
        deltas[0], mid, deltas[-1],
        float(sum(deltas)) / len(deltas)))
    print("STEP rate = {:.2f} Hz".format(
        1000.0 * len(deltas) / time.ticks_diff(marks[-1], marks[0])))
else:
    print("STEP too few updates: {}".format(len(marks)))

app.destroy()
print("PROBE DONE")
