# Dashboard direction check: the sign of roll and pitch against the
# expected horizon movement.

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

def probe_geom(roll, pitch):
    """Call _update_dash and read the geometry back to judge direction."""
    app._update_dash(roll, pitch, 0.0)
    lv.task_handler()

    pts = app._d_horizon

    import math
    sr = math.sin(math.radians(roll))
    cr = math.cos(math.radians(roll))
    pp = pitch * mod.DASH_PPD
    lim = mod.DASH_CY - 6
    pp = max(-lim, min(lim, pp))
    hcy = mod.DASH_CY + pp
    t = mod.DASH_LINE_T
    left_y = hcy + t * sr
    right_y = hcy - t * sr
    print("[G] roll=%+.0f pitch=%+.0f -> hcy=%.1f  left_y=%.0f right_y=%.0f %s"
          % (roll, pitch, hcy, left_y, right_y,
             "(right higher)" if right_y < left_y else
             ("(level)" if abs(right_y - left_y) < 1 else "(right lower)")))
    return hcy, left_y, right_y

print("=== direction criteria ===")

hcy0, l0, r0 = probe_geom(0, 0)
hcyR, lR, rR = probe_geom(30, 0)
print("[CHK] roll +30: right higher? %s" % (rR < lR))
hcyL, lL, rL = probe_geom(-30, 0)
print("[CHK] roll -30: left higher? %s" % (lL < rL))

hcyU, _, _ = probe_geom(0, 20)
hcyD, _, _ = probe_geom(0, -20)
print("[CHK] pitch +20 moves horizon DOWN? %s (%.0f -> %.0f)"
      % (hcyU > hcy0, hcy0, hcyU))
print("[CHK] pitch -20 moves horizon UP?   %s (%.0f -> %.0f)"
      % (hcyD < hcy0, hcy0, hcyD))

for tag, (ro, pi) in (("roll_p30", (30, 0)), ("roll_m30", (-30, 0)),
                      ("pitch_p15", (0, 15)), ("pitch_m15", (0, -15)),
                      ("level", (0, 0))):
    app._update_dash(ro, pi, 0.0)
    lv.task_handler()
    snap(tag)

print("[CHK] readout: %r / %r / %r"
      % (app._d_text[0].get_text(), app._d_text[1].get_text(),
         app._d_text[2].get_text()))
print("[CHK] heading: %r" % app._d_hdg.get_text())
app.destroy()
gc.collect()
print("PROBE DONE")
