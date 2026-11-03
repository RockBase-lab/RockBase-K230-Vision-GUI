# Open Gesture through the real launcher, run ~8 s of live preview, and report
# fps, recognised gestures, skeleton overlay visibility, and heap. Point the
# camera at a hand for a non-zero result. Run with --lvgl.

import gc
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("apps.app_gesture", "apps", "apps.error", "ui.beep", "launcher",
              "camera_fov", "model_pool"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv

src = open("/sdcard/factory/gui/launcher.py").read()
G = {"__name__": "gui_probe"}
exec(compile(src, "launcher.py", "exec"), G)
G["_ensure_cache_dir"]()

meta = {m["id"]: m for m in G["FOLDER_MEMBERS"]["vision"]}["gesture"]
print("MODULE", meta["module"])

launcher = None
try:
    t0 = time.ticks_ms()
    launcher = G["Launcher"]()
    launcher.open_app(meta)
    print("O open {}ms mem_free={}".format(
        time.ticks_diff(time.ticks_ms(), t0), gc.mem_free()))

    app = launcher.active_app
    if app is None:
        print("RESULT FAIL app is None")
        raise SystemExit(1)

    frames = 0
    err = 0
    seen = {}
    max_infer = 0.0
    t0 = time.ticks_ms()
    deadline = 8000
    while time.ticks_diff(time.ticks_ms(), t0) < deadline:
        try:
            app.step()
            lv.task_handler()
            frames += 1
            if app.last_gesture:
                seen[app.last_gesture] = seen.get(app.last_gesture, 0) + 1
            if app.infer_ms > max_infer:
                max_infer = app.infer_ms
        except BaseException as e:
            err += 1
            print("E step:", e)
            if err > 3:
                break
        time.sleep_ms(5)
    el = time.ticks_diff(time.ticks_ms(), t0)
    print("R frames={} fps={:.2f} max_infer={:.0f}ms errors={} mem_free={}"
          .format(frames, frames * 1000.0 / el, max_infer, err, gc.mem_free()))
    print("G gestures_seen={}".format(seen))

    skel_ok = (not app.palm_dot.has_flag(lv.obj.FLAG.HIDDEN)
               and not app.finger_lines[0].has_flag(lv.obj.FLAG.HIDDEN)
               and not app.dots[0].has_flag(lv.obj.FLAG.HIDDEN))
    print("V skeleton_visible={}".format(skel_ok))

    launcher.close_app()
    launcher = None
    print("C closed mem_free={}".format(gc.mem_free()))
finally:
    if launcher is not None:
        try:
            launcher.close_app()
        except BaseException as e:
            print("C close error:", e)
    print("DONE mem_free={}".format(gc.mem_free()))
