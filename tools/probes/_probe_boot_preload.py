# Boot preload end to end, without entering the main loop: build the splash, run
# boot_load (tiles, fonts, dictionary, ten models, Wi-Fi decision), then measure
# what an app's open costs once everything is resident.
#
# Run with --lvgl. Every line is also written to /sdcard/_boot_result.txt, because
# the host tool truncates the serial stream once the board goes quiet.

import gc
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
# Purging a SUBmodule is not enough: the parent package keeps the old module object
# in its namespace, so "from net import service" hands back the stale one even after
# "del sys.modules['net.service']". Purge the parents too.
for _name in ("core", "apps.app_ocr", "apps.app_vision", "apps", "core.errors",
              "ui", "ui.beep", "shell.splash", "launcher", "hardware.camera_fov",
              "core.model_pool", "core.font_pool", "core.ocr_dict",
              "net", "net.service"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv

_src = open("/sdcard/factory/gui/launcher.py").read()
G = {"__name__": "gui_probe"}
exec(compile(_src, "launcher.py", "exec"), G)
# The tables and classes live in shell/ after the layer split; importing them
# here also keeps sys.path bootstrapped by the exec above.
from shell.boot import BOOT_FONTS
from shell.registry import FOLDER_MEMBERS
from shell.home import Launcher
from shell import tile_cache
from shell.boot import boot_load
from core import font_pool
from core import model_pool
from core import ocr_dict
from shell import splash as splash_ui

tile_cache.ensure_cache_dir()

REPORT = []


def say(text):
    print(text)
    REPORT.append(text)
    REPORT.append(text)


splash = splash_ui.Splash()
say("P0 splash up mem_free={} KB".format(gc.mem_free() // 1024))

# open_app swallows the constructor exception into the notice panel. Hook the panel
# builder to record the reason, otherwise a failed open looks like "app=None" with no
# explanation once the serial stream is truncated.
_Launcher = Launcher
_orig_unavailable = _Launcher._show_app_unavailable


def _hooked_unavailable(self, scr, ctx, meta, error, content=None):
    say("P3 {} unavailable: {}".format(
        meta.get("id"), getattr(error, "message", error)))
    return _orig_unavailable(self, scr, ctx, meta, error, content)


_Launcher._show_app_unavailable = _hooked_unavailable

t0 = time.ticks_ms()
try:
    boot_load(splash)
    boot_ms = time.ticks_diff(time.ticks_ms(), t0)
    say("P1 boot_load {}ms ({} s) mem_free={} KB".format(
        boot_ms, boot_ms // 1000, gc.mem_free() // 1024))
except BaseException as error:
    say("P1 boot_load FAILED {} mem_free={}".format(error, gc.mem_free()))
    try:
        sys.print_exception(error)
    except BaseException:
        pass
    boot_ms = -1

mp = model_pool
fp = font_pool
say("P2 resident models={} fonts={} dict={}".format(
    mp.loaded_count(),
    len([n for n in BOOT_FONTS if fp.is_loaded(n[0])]),
    ocr_dict.is_loaded()))

launcher = Launcher()
t0 = time.ticks_ms()
launcher.build_home()
say("P4 build_home {}ms mem_free={} KB".format(
    time.ticks_diff(time.ticks_ms(), t0), gc.mem_free() // 1024))

if launcher.splash is None:
    splash.delete()

# ---- the number the design is about: open cost with everything resident ----
CASES = ["ocr", "plate", "face_det", "landmark"]
for app_id in CASES:
    meta = None
    for group in FOLDER_MEMBERS.values():
        for item in group:
            if item["id"] == app_id:
                meta = item
                break
        if meta is not None:
            break
    if meta is None:
        say("P5 {} SKIP not found".format(app_id))
        continue
    t0 = time.ticks_ms()
    try:
        launcher.open_app(meta)
    except BaseException as error:
        say("P5 {} open FAILED {}".format(app_id, error))
        launcher.close_app()
        continue
    open_ms = time.ticks_diff(time.ticks_ms(), t0)
    app = launcher.active_app
    say("P5 {:10s} open_app={:>5d} ms app={} sensor={} mem_free={} KB".format(
        app_id, open_ms, type(app).__name__ if app else None,
        app.sensor is not None if app else None, gc.mem_free() // 1024))

    # Ten frames: proves the pooled kpu still infers after the app that shared it
    # was closed, which is the failure mode a shared registry introduces.
    if app is not None:
        steps = 0
        t0 = time.ticks_ms()
        while steps < 10 and time.ticks_diff(time.ticks_ms(), t0) < 4000:
            app.step()
            lv.task_handler()
            time.sleep_ms(20)
            steps += 1
        say("P6 {:10s} frames={}".format(app_id, steps))
    launcher.close_app()

say("P7 after all apps resident={} mem_free={} KB".format(
    mp.loaded_count(), gc.mem_free() // 1024))

try:
    _f = open("/sdcard/_boot_result.txt", "w")
    _f.write("\n".join(REPORT))
    _f.close()
    print("WROTE /sdcard/_boot_result.txt")
except BaseException as error:
    print("write result failed: {}".format(error))

print("RESULT: DONE")
