# Real sensor plus a stubbed sensor that always fails, then the real
# launcher panel, with a snapshot of the unavailable panel.

import sys
import time

import image
import lvgl as lv

W, H = 800, 480
lv.init()
disp = lv.disp_create(W, H)
disp.set_color_format(lv.COLOR_FORMAT.ARGB8888)
b1 = image.Image(W, H, image.BGRA8888)
b2 = image.Image(W, H, image.BGRA8888)
disp.set_draw_buffers(b1.bytearray(), b2.bytearray(), b1.size(),
                      lv.DISP_RENDER_MODE.FULL)
disp.set_flush_cb(lambda drv, area, color: drv.flush_ready())

for m in ("core", "core.errors", "apps.app_camera", "launcher"):
    sys.modules.pop(m, None)

GUI = "/sdcard/factory/gui"
if GUI not in sys.path:
    sys.path.insert(0, GUI)

from core.errors import AppUnavailable
from apps.app_camera import create_app

class Ctx:
    pass

scr = lv.obj()
content = lv.obj(scr)
content.set_size(W, H)
content.align(lv.ALIGN.TOP_MID, 0, 0)

ctx = Ctx()
ctx.width = W
ctx.height = H
ctx.ok_beep = True
ctx.want_exit = False

t0 = time.ticks_ms()
app = None
err = None
try:
    app = create_app(content, ctx)
except AppUnavailable as e:
    err = e
except BaseException as e:
    err = e
print("CREATE_MS", time.ticks_diff(time.ticks_ms(), t0))
print("IS_UNAVAILABLE", isinstance(err, AppUnavailable))
if err is not None:
    print("MSG", getattr(err, "message", str(err)))

if app is not None:
    print("CAMERA_PRESENT=1 (cleanup)")
    try:
        app.destroy()
    except BaseException as e:
        print("destroy err", e)
else:
    print("CAMERA_PRESENT=0")

import apps.app_camera as cam

class _AbsentSensor:
    def __init__(self, **kwargs):
        raise RuntimeError("Can not found sensor on 0 (probe 1920x1080@30)")

cam.Sensor = _AbsentSensor

class _FakeSys:
    @staticmethod
    def print_exception(err):
        print("PRINT_EXC_SUPPRESSED", type(err).__name__)

cam.sys = _FakeSys
scr2 = lv.obj()
content2 = lv.obj(scr2)
content2.set_size(W, H)
content2.align(lv.ALIGN.TOP_MID, 0, 0)
ctx2 = Ctx()
ctx2.width = W
ctx2.height = H
ctx2.ok_beep = True
ctx2.want_exit = False
t0 = time.ticks_ms()
err2 = None
try:
    cam.create_app(content2, ctx2)
except AppUnavailable as e:
    err2 = e
    print("SIM_CAUGHT_APPUNAVAILABLE")
except BaseException as e:
    print("SIM_OTHER_EXC", type(e).__name__, str(e))
print("SIM_CREATE_MS", time.ticks_diff(time.ticks_ms(), t0))
print("SIM_IS_UNAVAILABLE", isinstance(err2, AppUnavailable))
if err2 is not None:
    print("SIM_MSG", getattr(err2, "message", str(err2)))

g = {"__name__": "guimod", "__file__": "/sdcard/factory/gui/launcher.py"}
src = open("/sdcard/factory/gui/launcher.py").read()
exec(src, g)
launcher = g["Launcher"]()
if err2 is None:
    err2 = AppUnavailable("Camera module not detected.\n"
                          "Check the camera cable and try again.")
launcher._show_app_unavailable(scr, ctx, {"name": "Camera", "id": "camera"},
                               err2, content)
lv.scr_load(scr)
lv.task_handler()
lv.task_handler()

import ubinascii
import uctypes

dsc = lv.snapshot_take(lv.scr_act(), lv.COLOR_FORMAT.ARGB8888)
w, h = dsc.header.w, dsc.header.h
ptr = int.from_bytes(bytes(memoryview(dsc.data)), "little")
raw = uctypes.bytearray_at(ptr, w * h * 4)
dsc.buf_free()
out = bytearray()
for y in range(h):
    x = 0
    while x < w:
        i = (y * w + x) * 4
        bb = raw[i]
        gg = raw[i + 1]
        rr = raw[i + 2]
        n = 1
        while x + n < w and n < 255:
            j = (y * w + x + n) * 4
            if raw[j] != bb or raw[j + 1] != gg or raw[j + 2] != rr:
                break
            n += 1
        out.append(n - 1)
        out.append(bb)
        out.append(gg)
        out.append(rr)
        x += n
print("SHOT unavail %d %d %d %d" % (w, h, len(raw), len(out)))
b64 = ubinascii.b2a_base64(bytes(out)).decode().strip()
for i in range(0, len(b64), 800):
    print("B64 " + b64[i:i + 800])
print("SHOTEND unavail")
print("PROBE_DONE")
