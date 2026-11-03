# Open Orient and Segment through the real launcher, run ~2 s of live preview
# each, and report frame rate, detection count and heap. Segment additionally
# prints whether the 983KB masks buffer allocated (the design hinges on it
# fitting after release_home).
#
# Run with --lvgl. Point the camera at a person for a non-zero detection count.

import gc
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("apps.app_orient", "apps.app_segment", "apps.anchor_det",
              "apps", "apps.error", "ui.beep", "launcher", "camera_fov",
              "model_pool"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv

src = open("/sdcard/factory/gui/launcher.py").read()
G = {"__name__": "gui_probe"}
exec(compile(src, "launcher.py", "exec"), G)
G["_ensure_cache_dir"]()

members = {m["id"]: m for m in G["FOLDER_MEMBERS"]["vision"]}

for app_id in ("orient", "segment"):
    meta = members[app_id]
    print("=== {} module={} ===".format(app_id, meta["module"]))
    t0 = time.ticks_ms()
    launcher = G["Launcher"]()
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
    while time.ticks_diff(time.ticks_ms(), t0) < 2500:
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
    if app_id == "segment":
        print("S masks={} mask_widget_hidden={}".format(
            None if app.masks is None else app.masks.shape,
            app.mask_widget.has_flag(lv.obj.FLAG.HIDDEN)))
    launcher.close_app()
    print("C closed mem_free={}".format(gc.mem_free()))

print("DONE mem_free={}".format(gc.mem_free()))
