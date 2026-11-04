# Open Person, Fall and Gesture through the real launcher, run each for ~2 s of
# live preview, and report detections, frame rate and heap. Confirms the shared
# anchor_det shell decodes correctly on the board (a wrong anchor/coordinate
# order shows up as zero boxes or boxes pinned to one edge).
#
# Run with --lvgl. Point the camera at a person for a non-zero detection count.

import gc
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("core", "apps.anchor_base", "apps.app_person", "apps.app_fall",
              "apps.app_gesture", "apps", "core.errors", "ui.beep", "launcher",
              "hardware.camera_fov", "core.model_pool"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv

src = open("/sdcard/factory/gui/launcher.py").read()
G = {"__name__": "gui_probe"}
exec(compile(src, "launcher.py", "exec"), G)
# The tables and classes live in shell/ after the layer split; importing them
# here also keeps sys.path bootstrapped by the exec above.
from shell.registry import FOLDER_MEMBERS
from shell.home import Launcher
from shell import tile_cache

tile_cache.ensure_cache_dir()

members = {m["id"]: m for m in FOLDER_MEMBERS["vision"]}

for app_id in ("person", "fall", "gesture"):
    meta = members[app_id]
    print("=== {} module={} ===".format(app_id, meta["module"]))
    t0 = time.ticks_ms()
    launcher = Launcher()
    launcher.open_app(meta)
    print("O open {}ms mem_free={}".format(
        time.ticks_diff(time.ticks_ms(), t0), gc.mem_free()))
    app = launcher.active_app
    if app is None:
        print("RESULT {} FAIL - app None".format(app_id))
        continue
    frames = 0
    objs_seen = 0
    err = 0
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < 2000:
        try:
            app.step()
            lv.task_handler()
            frames += 1
            if app.last_objs > objs_seen:
                objs_seen = app.last_objs
        except BaseException as e:
            err += 1
            print("E step:", e)
            if err > 3:
                break
        time.sleep_ms(10)
    el = time.ticks_diff(time.ticks_ms(), t0)
    print("R frames={} fps={:.1f} max_objs={} errors={} mem_free={}".format(
        frames, frames * 1000.0 / el, objs_seen, err, gc.mem_free()))
    launcher.close_app()
    print("C closed mem_free={}".format(gc.mem_free()))

print("DONE mem_free={}".format(gc.mem_free()))
