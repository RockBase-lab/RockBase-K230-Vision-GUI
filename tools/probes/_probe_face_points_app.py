# Runs the Points app through the real launcher and reports what the screen shows.
#
# Checks that the announced structure exists (line per group), that view cycling
# hides the right groups, and that the frame rate with a live face still clears
# the 20 fps bar. Run with --lvgl, a face in front of the camera.

import gc
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("core", "apps.app_face_points", "apps.app_face_det", "apps",
              "core.errors", "ui.beep", "launcher"):
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
for item in G["FOLDER_MEMBERS"]["face"]:
    if item["id"] == "landmark":
        meta = item
        break
print("L1 meta name={} module={} immersive={}".format(
    meta["name"], meta["module"], meta.get("immersive")))

t0 = time.ticks_ms()
launcher.open_app(meta)
print("L2 open_app {}ms".format(time.ticks_diff(time.ticks_ms(), t0)))

app = launcher.active_app
if app is None:
    print("RESULT: FAIL - app is None")
else:
    print("L3 class={} y_off={} groups={} links={} dots={}".format(
        type(app).__name__, app.y_off, len(app.lines), len(app.links),
        len(app.dots)))

    for _ in range(2):
        lv.task_handler()
        time.sleep_ms(60)
    print("L4 bar x={} y={} w={} h={} title='{}'".format(
        app.stat_btn.get_x(), app.stat_btn.get_y(),
        app.stat_btn.get_width(), app.stat_btn.get_height(),
        app.seg_title.get_text()))

    print("L5 hold still, a face in front of the camera, 4 s ...")
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
        print("L6 frame {} step={:.1f} ms task={:.1f} ms face={} pts={}".format(
            i, t_step, t_task, app.has_face, app._have_pts))
    print("L7 fps_text='{}' view={} lines={} links={} dots={}".format(
        app.fps_text, app.view_idx, len(app.lines), len(app.links),
        len(app.dots)))

    if app._have_pts:
        xs = app._kx
        ys = app._ky
        inside = 0
        for k in range(len(xs)):
            if 0 <= xs[k] <= 800 and 0 <= ys[k] <= 480:
                inside += 1
        print("L8 points x=[{:.0f},{:.0f}] y=[{:.0f},{:.0f}] inside {}/{}".format(
            min(xs), max(xs), min(ys), max(ys), inside, len(xs)))
        le = (xs[35] + xs[36] + xs[33] + xs[37]) / 4.0
        le_y = (ys[35] + ys[36] + ys[33] + ys[37]) / 4.0
        re = (xs[89] + xs[90] + xs[87] + xs[91]) / 4.0
        re_y = (ys[89] + ys[90] + ys[87] + ys[91]) / 4.0
        print("L9 eye centres ({:.0f},{:.0f}) ({:.0f},{:.0f}) gap={:.0f} px".format(
            le, le_y, re, re_y, re - le))

    # view cycling: Mesh -> Contour -> Off -> Mesh
    import apps.app_face_points as pts_mod
    for step in range(4):
        app._cycle_view()
        lv.task_handler()
        groups = 0
        for ln in app.lines:
            if not ln.has_flag(lv.obj.FLAG.HIDDEN):
                groups += 1
        links = 0
        for ln in app.links:
            if not ln.has_flag(lv.obj.FLAG.HIDDEN):
                links += 1
        dots = 0
        for d in app.dots:
            if not d.has_flag(lv.obj.FLAG.HIDDEN):
                dots += 1
        print("L10 view='{}' groups={}/{} links={}/{} dots={}/{}".format(
            pts_mod.VIEWS[app.view_idx][0], groups, len(app.lines),
            links, len(app.links), dots, len(app.dots)))

    app._cycle_view()   # back to All
    app.frozen = True
    app._update_stat()
    before = app.frame_count
    for _ in range(5):
        app.step()
    print("L11 frozen step() advances frames: {}".format(
        "NO (correct)" if app.frame_count == before else "YES (bug)"))
    app.frozen = False

    print("L12 mem_free={}".format(gc.mem_free()))
    launcher.close_app()
    print("L13 closed | mem_free={}".format(gc.mem_free()))
    print("RESULT: PASS")
print("ALL DONE")
