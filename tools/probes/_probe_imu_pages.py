# Three-page swipe behaviour for the IMU app: page switching, wrap-around
# and the small-displacement no-trigger case.

import gc
import sys
import time
import ubinascii

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

    def report_unavailable(self, reason):
        print("[PROBE] report_unavailable:", reason)

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

def snap(tag):
    dsc = lv.snapshot_take(lv.scr_act(), lv.COLOR_FORMAT.ARGB8888)
    w, h = dsc.header.w, dsc.header.h
    ptr = int.from_bytes(bytes(memoryview(dsc.data)), "little")
    raw = uctypes.bytearray_at(ptr, w * h * 4)
    dsc.buf_free()
    out = bytearray()
    for y in range(h):
        x = 0
        while x < w:
            off = (y * w + x) * 4
            b, g, r = raw[off], raw[off + 1], raw[off + 2]
            n = 1
            while x + n < w and n < 255:
                o = off + n * 4
                if raw[o] != b or raw[o + 1] != g or raw[o + 2] != r:
                    break
                n += 1
            out.append(n - 1)
            out.append(b)
            out.append(g)
            out.append(r)
            x += n
    print("SHOT %s %d %d %d %d" % (tag, w, h, len(raw), len(out)))
    b64 = ubinascii.b2a_base64(bytes(out)).decode().strip()
    for i in range(0, len(b64), 800):
        print("B64 " + b64[i:i + 800])
    print("SHOTEND %s" % tag)

print("[CHK1] pages built: %s  default page=%d (expect 0)"
      % ([p is not None for p in app._pg], app._page))

t0 = time.ticks_ms()
n = 0
while time.ticks_diff(time.ticks_ms(), t0) < 2500:
    app.step()
    n += 1
    lv.task_handler()
print("[CHK2] steps=%d roll=%+.2f pitch=%+.2f yaw=%+.2f (static should be near 0)"
      % (n, app._roll, app._pitch, app._yaw))
print("[CHK2] ahrs scale: acc=%.2f gyr=%.0f att=%.0f"
      % (app._raw_view.full[0], app._raw_view.full[1], app._att_view.full[0]))
snap("p1_dash")

class FakeTouch(object):
    def __init__(self, seq):
        self.seq = seq
        self.i = 0

    def read_raw(self):
        if self.i >= len(self.seq):
            return []
        v = self.seq[self.i]
        self.i += 1
        return v

def swipe(dx):
    """Simulate one horizontal swipe: Down, several Moves, then Up."""
    x0, y = 500, 200
    seq = [[(x0, y, 2, 1)]]
    steps = 4
    for k in range(1, steps + 1):
        seq.append([(x0 + dx * k // steps, y, 3, 1)])
    seq.append([(x0 + dx, y, 1, 1)])
    return seq

app.ctx.touch = FakeTouch(swipe(-300))
for _ in range(10):
    app.step()
    lv.task_handler()
    time.sleep_ms(21)
print("[CHK3] after left swipe: page=%d (expect 1)" % app._page)
snap("p2_raw")

app.ctx.touch = FakeTouch(swipe(-300))
for _ in range(10):
    app.step()
    lv.task_handler()
    time.sleep_ms(21)
print("[CHK3] after 2nd left swipe: page=%d (expect 2)" % app._page)
snap("p3_att")

app.ctx.touch = FakeTouch(swipe(-300))
for _ in range(10):
    app.step()
    lv.task_handler()
    time.sleep_ms(21)
print("[CHK3] after 3rd left swipe: page=%d (expect 0, wrap)" % app._page)

app.ctx.touch = FakeTouch(swipe(300))
for _ in range(10):
    app.step()
    lv.task_handler()
    time.sleep_ms(21)
print("[CHK3] after right swipe: page=%d (expect 2)" % app._page)

app.ctx.touch = FakeTouch(swipe(-40))
for _ in range(10):
    app.step()
    lv.task_handler()
    time.sleep_ms(21)
print("[CHK3] small drag: page=%d (expect unchanged 2)" % app._page)

print("[CHK4] att hist=%d raw hist=%d  chart pts=%d"
      % (len(app._att_view.hist), len(app._raw_view.hist), mod.CHART_POINTS))
app.destroy()
gc.collect()
print("PROBE DONE")
