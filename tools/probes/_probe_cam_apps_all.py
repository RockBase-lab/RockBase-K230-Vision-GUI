# Camera apps under two failure modes: constructor failure for all three
# apps, then runtime disconnect for all three.
# Stubbed sensor stop/deinit proxy to the real sensor so the capture
# device is released.

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

GUI = "/sdcard/factory/gui"
if GUI not in sys.path:
    sys.path.insert(0, GUI)
for m in ("core", "core.errors", "apps.app_camera", "apps.app_scan", "apps.app_vision"):
    sys.modules.pop(m, None)

g = {"__name__": "guimod", "__file__": GUI + "/launcher.py"}
exec(open(GUI + "/launcher.py").read(), g)
# The tables and classes live in shell/ after the layer split; importing them
# here also keeps sys.path bootstrapped by the exec above.
from core.errors import AppUnavailable
from shell.home import Launcher
from shell import tile_cache


from core.errors import NoSignalCounter

tile_cache.ensure_cache_dir()
APPS = {
    "camera": {"id": "camera", "name": "Camera",
               "module": "apps.app_camera", "immersive": True},
    "scan": {"id": "scan", "name": "Scan",
             "module": "apps.app_scan", "immersive": True},
    "detect": {"id": "detect", "name": "Detect",
               "module": "apps.app_vision", "immersive": True,
               "release_home": True},
}

class FailSensor:
    """Fails in the constructor, reproducing a missing camera."""

    def __init__(self, **kwargs):
        raise RuntimeError("Can not found sensor on 0 (probe 1920x1080@30)")

class DeadSensor:
    """Constructs fine but never yields a frame, reproducing a runtime drop.

    stop/deinit must proxy to the real sensor: the app destroy() path relies
    on them to release the hardware. If the stub hides the real handle the
    capture device stays claimed and later apps report a sensor already
    inited error.
    """

    def __init__(self, real):
        self.real = real
        self.stopped = False
        self.deinited = False

    def snapshot(self, **kwargs):
        raise RuntimeError("no frame")

    def stop(self):
        self.stopped = True
        self.real.stop()

    def deinit(self):
        self.deinited = True
        self.real.deinit()

try:
    from media.sensor import Sensor as _RealSensor
    for _s in getattr(_RealSensor, "_devs", None) or []:
        if _s is not None:
            for _m in ("stop", "deinit"):
                try:
                    getattr(_s, _m)()
                except BaseException as _e:
                    print("cleanup", _m, _e)
except BaseException as _e:
    print("cleanup import err", _e)

class Ctx2:
    @staticmethod
    def print_exception(err):
        pass

launcher = Launcher()
todo = [("A", k) for k in ("camera", "scan", "detect")] + \
       [("B", k) for k in ("camera", "scan", "detect")]
fails = 0

for phase, key in todo:
    meta = APPS[key]
    mod = __import__(meta["module"], None, None, ["create_app"])
    real_sensor = mod.Sensor
    if phase == "A":
        mod.Sensor = FailSensor

        mod.sys = Ctx2

    t0 = time.ticks_ms()
    launcher.open_app(meta)
    ms = time.ticks_diff(time.ticks_ms(), t0)

    if phase == "A":
        ok = (launcher.active_app is None and
              launcher.ctx is not None and
              launcher.ctx.back_handler is None and
              launcher.ctx.unavailable_msg is None and
              launcher.app_scr is not None)

        launcher.ctx.request_exit()
        launcher.close_app()
        ok = ok and launcher.ctx is None and launcher.app_scr is None
        print("A {} construct={}ms {}".format(key, ms, "PASS" if ok else "FAIL"))
        if not ok:
            fails += 1
        mod.Sensor = real_sensor
        continue

    app = launcher.active_app
    if app is None:
        print("B {} SKIP (app not opened)".format(key))
        fails += 1
        continue
    dead = DeadSensor(app.sensor)
    app.sensor = dead
    for _ in range(NoSignalCounter().limit + 2):
        app.step()
    reported = launcher.ctx is not None and launcher.ctx.unavailable_msg is not None
    panel_ms = time.ticks_ms()
    launcher._fail_running_app()
    released = dead.stopped and dead.deinited
    hidden = "N/A"
    try:
        hidden = launcher.app_content.has_flag(lv.obj.FLAG.HIDDEN)
    except BaseException as e:
        hidden = "N/A({})".format(e)
    ok2 = (reported and released and launcher.active_app is None and
           launcher.ctx is not None and launcher.ctx.unavailable_msg is None)
    launcher.ctx.request_exit()
    launcher.close_app()
    ok2 = ok2 and launcher.ctx is None and launcher.app_scr is None
    print("B {} reported={} released={} hidden={} {}ms {}".format(
        key, reported, released, hidden,
        time.ticks_diff(time.ticks_ms(), panel_ms),
        "PASS" if ok2 else "FAIL"))
    if not ok2:
        fails += 1

print("RESULT fails={} {}".format(fails, "ALL_PASS" if fails == 0 else "HAS_FAIL"))
print("PROBE_DONE")
