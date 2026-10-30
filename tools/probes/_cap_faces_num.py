# Headless capture of the face app, counting drawn boxes.
# Calls the module-level cache-dir initialiser before exercising the
# launcher, which is only built inside main().

import gc
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("apps.app_face_det", "apps", "app_error", "beep", "gui"):
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
print("L1 gui loaded")

launcher = G["Launcher"]()
meta = None
for item in G["FOLDER_MEMBERS"]["face"]:
    if item["id"] == "face_det":
        meta = item
        break
print("L2 meta:", meta["name"], meta["module"], "immersive=", meta.get("immersive"))

t0 = time.ticks_ms()
launcher.open_app(meta)
print("L3 open_app {}ms".format(time.ticks_diff(time.ticks_ms(), t0)))

app = launcher.active_app
if app is None:
    print("RESULT: FAIL - app is None")
else:
    print("L4 ctx={}x{} y_off={} anchors={} th={}".format(
        launcher.ctx.width, launcher.ctx.height, app.y_off,
        app.anchors.shape if app.anchors is not None else None, app.th_idx))

    for _ in range(2):
        lv.task_handler()
        time.sleep_ms(60)

    bar = app.stat_btn
    print("L5 topbar x={} y={} w={} h={} (expect 72,10,720,56)".format(
        bar.get_x(), bar.get_y(), bar.get_width(), bar.get_height()))
    print("L6 segs '{}' | '{}' | '{}' | '{}'".format(
        app.seg_title.get_text(), app.seg_count.get_text(),
        app.seg_fps.get_text(), app.seg_hint.get_text()))

    N = 90
    hits = 0
    dot_hits = 0
    min_free = 1 << 30
    best = None
    t0 = time.ticks_ms()
    for i in range(N):
        app.step()
        lv.task_handler()
        if app.last_faces > 0:
            hits += 1
            b = app.box_objs[0]
            if (b.get_width() > 0 and
                    (best is None or b.get_width() * b.get_height() >
                     best[0] * best[1])):
                best = (b.get_width(), b.get_height(), b.get_x(), b.get_y())
            if not (app.dot_objs[0].has_flag(lv.obj.FLAG.HIDDEN)):
                dot_hits += 1
        mf = gc.mem_free()
        if mf < min_free:
            min_free = mf
        if i % 10 == 0:
            time.sleep_ms(20)
    dt = time.ticks_diff(time.ticks_ms(), t0)
    print("L7 {}/{} frames with faces | 5-pt visible {} frames | {:.1f} ms/frame | min_free={} now={}"
          .format(hits, N, dot_hits, dt / N, min_free, gc.mem_free()))
    if best is not None:
        cx = best[2] + best[0] // 2
        cy = best[3] + best[1] // 2
        print("L8 largest face box w={} h={} at ({},{}) centre=({},{})".format(
            best[0], best[1], best[2], best[3], cx, cy))
        print("L9 centre offset from screen centre dx={} dy={} (|dy|<=120 means y_off is right)"
              .format(cx - 400, cy - 240))
    print("L10 fps='{}' objs={} count_seg='{}'".format(
        app.fps_text, app.last_faces, app.seg_count.get_text()))

    app._open_picker()
    print("L11 picker open frozen={} obj={}".format(
        app.frozen, app.picker is not None))
    app._pick(0)
    print("L12 pick(0) th={} frozen={} title='{}'".format(
        app.th_idx, app.frozen, app.seg_title.get_text()))
    app._pick(1)
    print("L13 pick(1) th={} title='{}'".format(
        app.th_idx, app.seg_title.get_text()))

    launcher.close_app()
    print("L14 closed | mem_free={}".format(gc.mem_free()))
    print("RESULT:", "PASS" if hits > 0 else "NO_FACE")
print("ALL DONE")
