# Text width measurement and repaint cost for the instrument layout.

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

print("--- header text width (montserrat_14) ---")
probe = lv.label(content)
probe.set_pos(0, 440)
small = lv.font_montserrat_14
probe.set_style_text_font(small, 0)
for t in ("AccX(g)", "AccY(g)", "AccZ(g)", "GyrX(dps)", "GyrY(dps)",
          "GyrZ(dps)", "Temp(C)", "|", "-999.9999", "-999.99", "-9999.99"):
    probe.set_text(t)
    lv.task_handler()
    print("  {!r:14s} w={} h={}".format(t, probe.get_self_width(),
                                       probe.get_self_height()))
probe.delete()
lv.task_handler()

disp = lv.disp_get_default()

def refr():
    lv.refr_now(disp)

def time_refr(tag, n=5):
    """Force n consecutive repaints and return the timings in ms."""
    refr()
    out = []
    for _ in range(n):
        t0 = time.ticks_us()
        refr()
        out.append(time.ticks_diff(time.ticks_us(), t0) / 1000.0)
    print("{}: {}".format(tag, " ".join("{:.1f}".format(v) for v in out)))
    return out

time_refr("clean frame ms", 5)

labels = app._row_labels[-1]
def touch7():
    for lb in labels:
        lb.invalidate()
touch7()
time_refr("invalidate 7 labels", 5)

def touch70():
    for row in app._row_labels:
        for lb in row:
            lb.invalidate()
touch70()
time_refr("invalidate 70 labels", 5)

def set7(i):
    for k, lb in enumerate(labels):
        lb.set_text("{}.{}".format(i, k))
    refr()
t = []
for i in range(5):
    t0 = time.ticks_us()
    set7(i)
    t.append(time.ticks_diff(time.ticks_us(), t0) / 1000.0)
print("set_text 7 + refr_now ms:", " ".join("{:.1f}".format(v) for v in t))

print("PROBE DONE")
