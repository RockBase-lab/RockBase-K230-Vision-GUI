# Runs the Pose app through the real launcher and reports what the screen does.
#
# Checks the announced structure (18 pooled lines, 17 joint dots, one 56 px bar),
# that a person produces visible geometry, that the three views hide the right
# layers, and that the frame rate clears 20 fps. Run with --lvgl and one person
# in front of the camera, full body if possible.
#
# Prints the app's own numbers where possible, because those are what a user
# sees in the bar.

import gc
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("apps.app_pose", "apps", "apps.error", "ui.beep", "launcher",
              "camera_fov"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv

_f = open("/sdcard/factory/gui/launcher.py")
gui_src = _f.read()
_f.close()
del _f

G = {"__name__": "gui_probe"}
exec(compile(gui_src, "launcher.py", "exec"), G)
G["_ensure_cache_dir"]()

launcher = G["Launcher"]()
meta = None
for item in G["FOLDER_MEMBERS"]["vision"]:
    if item["id"] == "pose":
        meta = item
        break
print("L1 meta name={} module={} immersive={} release_home={}".format(
    meta["name"], meta["module"], meta.get("immersive"),
    meta.get("release_home")))

t0 = time.ticks_ms()
launcher.open_app(meta)
print("L2 open_app {}ms mem_free={}".format(
    time.ticks_diff(time.ticks_ms(), t0), gc.mem_free()))

app = launcher.active_app
if app is None:
    print("RESULT: FAIL - app is None")
else:
    lines = 0
    for group in app.chain_lines:
        lines += len(group)
    print("L3 class={} chains={} lines={} dots={} view={}".format(
        type(app).__name__, len(app.chain_lines), lines, len(app.dots),
        app.view))

    for _ in range(2):
        lv.task_handler()
        time.sleep_ms(60)
    print("L4 bar x={} y={} w={} h={} text='{}'".format(
        app.stat_btn.get_x(), app.stat_btn.get_y(),
        app.stat_btn.get_width(), app.stat_btn.get_height(),
        app.stat_model.get_text()))

    print("L5 hold still, one person in front of the camera, 4 s ...")
    for s in range(4, 0, -1):
        print("   ", s)
        time.sleep_ms(1000)

    N = 8
    for i in range(N):
        t0 = time.ticks_us()
        app.step()
        t_step = time.ticks_diff(time.ticks_us(), t0) / 1000.0
        t0 = time.ticks_us()
        lv.task_handler()
        t_task = time.ticks_diff(time.ticks_us(), t0) / 1000.0
        print("L6 frame {} step={:.1f} ms task={:.1f} ms persons={} infer={:.1f} ms"
              .format(i, t_step, t_task, app.last_persons, app.infer_ms))
    print("L7 fps_text='{}' view={} count='{}' hint='{}'".format(
        app.fps_text, app.view, app.stat_count.get_text(),
        app.stat_hint.get_text()))

    # ---- view switching: the pools must actually hide ----
    for view in (1, 2, 0):
        app.view = view
        app.step()
        lv.task_handler()
        lines_shown = 0
        for group in app.chain_lines:
            for ln in group:
                if not ln.has_flag(lv.obj.FLAG.HIDDEN):
                    lines_shown += 1
        dots_shown = 0
        for dot in app.dots:
            if not dot.has_flag(lv.obj.FLAG.HIDDEN):
                dots_shown += 1
        print("L8 view={} lines_shown={} dots_shown={}".format(
            view, lines_shown, dots_shown))

    # ---- sustained frame rate over 5 s ----
    app.view = 0
    t0 = time.ticks_ms()
    frames = 0
    errors = 0
    while time.ticks_diff(time.ticks_ms(), t0) < 5000:
        try:
            app.step()
            frames += 1
        except BaseException as error:
            errors += 1
            print("E step error:", error)
            if errors > 3:
                break
        lv.task_handler()
    elapsed = time.ticks_diff(time.ticks_ms(), t0)
    print("L9 frames={} elapsed={}ms loop_fps={:.1f} errors={} mem_free={}".format(
        frames, elapsed, frames * 1000.0 / elapsed, errors, gc.mem_free()))

    try:
        launcher.close_app()
        print("L10 close ok")
    except BaseException as error:
        print("L10 close error:", error)
print("RESULT: DONE")
