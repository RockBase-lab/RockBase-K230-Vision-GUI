# Open every vision/face member that has a real module on this card, run a short
# live loop, and report fps, detections, errors and heap. Catches a launcher
# entry that points at a missing module or a constructor that cannot allocate.
#
# Run with --lvgl. Runs ~2 s per app; the camera should see something.

import gc
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("core", "apps", "core.errors", "apps.anchor_base", "apps.app_person",
              "apps.app_fall", "apps.app_gesture", "apps.app_orient",
              "apps.app_segment", "apps.app_mask", "ui.beep", "launcher",
              "hardware.camera_fov", "core.model_pool"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv

src = open("/sdcard/factory/gui/launcher.py").read()
G = {"__name__": "gui_probe"}
exec(compile(src, "launcher.py", "exec"), G)
G["_ensure_cache_dir"]()

targets = []
for group in ("vision", "face"):
    for m in G["FOLDER_MEMBERS"][group]:
        if m["module"] != "apps.app_placeholder":
            targets.append(m)

print("N targets={}".format(len(targets)))
launcher = G["Launcher"]()

for meta in targets:
    aid = meta["id"]
    t0 = time.ticks_ms()
    try:
        launcher.open_app(meta)
    except BaseException as e:
        print("X {} open failed: {}".format(aid, e))
        continue
    app = launcher.active_app
    if app is None:
        print("X {} app None".format(aid))
        continue
    open_ms = time.ticks_diff(time.ticks_ms(), t0)
    frames = 0
    err = 0
    objs = 0
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < 2000:
        try:
            app.step()
            lv.task_handler()
            frames += 1
            n = getattr(app, "last_objs", getattr(app, "last_faces", 0))
            if n > objs:
                objs = n
        except BaseException as e:
            err += 1
            print("E {} step: {}".format(aid, e))
            if err > 2:
                break
        time.sleep_ms(5)
    el = time.ticks_diff(time.ticks_ms(), t0)
    print("A {} open={}ms fps={:.1f} objs={} err={} free={}".format(
        aid, open_ms, frames * 1000.0 / max(el, 1), objs, err, gc.mem_free()))
    launcher.close_app()

print("DONE mem_free={}".format(gc.mem_free()))
