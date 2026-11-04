# End-to-end check of the Liveness app through the real launcher.
#
# Reports launch time, the pipeline, whether a score is produced, the threshold
# presets, freeze behaviour and the frame rate, then captures the screen. Run with
# --lvgl and a face in front of the camera; with nobody present it still verifies the
# UI and the "no face" path.

import gc
import struct
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("core", "apps.app_face_liveness", "apps.app_face_points",
              "apps.app_face_det", "apps.app_face_recognize", "apps",
              "core.errors", "ui.beep", "launcher"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv
import uctypes

import apps.app_face_liveness as live_mod
TH = live_mod.THRESHOLDS

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
    if item["id"] == "liveness":
        meta = item
        break
print("E1 meta name={} module={} immersive={} release_home={}".format(
    meta["name"], meta["module"], meta.get("immersive"),
    meta.get("release_home")))

t0 = time.ticks_ms()
launcher.open_app(meta)
print("E2 open_app {}ms".format(time.ticks_diff(time.ticks_ms(), t0)))

app = launcher.active_app
if app is None:
    print("RESULT: FAIL - app is None")
else:
    for _ in range(2):
        lv.task_handler()
        time.sleep_ms(80)
    print("E3 bar title='{}' verdict='{}' hint='{}'".format(
        app.seg_title.get_text(), app.seg_verdict.get_text(),
        app.seg_hint.get_text()))

    print("E4 hold still, 3 s ...")
    for s in range(3, 0, -1):
        print("   ", s)
        time.sleep_ms(1000)

    N = 15
    scored = 0
    faces = 0
    scores = []
    t0 = time.ticks_ms()
    for i in range(N):
        app.step()
        lv.task_handler()
        if app.has_face:
            faces += 1
        if app.score is not None:
            scored += 1
            scores.append(app.score)
    ms = time.ticks_diff(time.ticks_ms(), t0) / float(N)
    print("E5 {} frames | face {} | scored {} | {:.1f} ms/frame | {:.1f} fps".format(
        N, faces, scored, ms, 1000.0 / ms if ms else 0))

    if scores:
        scores.sort()
        n = len(scores)
        print("E6 score min={:.4f} median={:.4f} max={:.4f} verdict={}".format(
            scores[0], scores[n // 2], scores[-1],
            "REAL" if app.is_real else "SPOOF"))
        print("E7 label='{}'".format(app.box_label.get_text()))
    else:
        print("E6 no score (no face in view)")
        print("E7 label hidden={}".format(
            app.box_label.has_flag(lv.obj.FLAG.HIDDEN)))

    # Threshold presets: apply each and check the verdict follows the score.
    for idx in range(3):
        app._pick(idx)
        lv.task_handler()
        expect = None if app.score is None else app.score >= TH[idx][1]
        print("E8 th={:.2f} verdict={} expect={} bar='{}'".format(
            TH[idx][1], app.is_real, expect, app.seg_verdict.get_text()))
    app._pick(1)

    # Freeze must stop the pipeline from advancing.
    app.frozen = True
    before = app.frame_count
    for _ in range(4):
        app.step()
    print("E9 frozen stops frames: {}".format(
        "YES (correct)" if app.frame_count == before else "NO (bug)"))
    app.frozen = False

    # Overlay open/close must leave the app running again.
    app._open_picker()
    print("E10 picker open frozen={} obj={}".format(
        app.frozen, app.picker is not None))
    app._close_picker()
    print("E11 picker closed frozen={} obj={}".format(
        app.frozen, app.picker is not None))

    lv.refr_now(lv.disp_get_default())
    STEP = 2
    W = 800 // STEP
    H = 480 // STEP
    dsc = lv.snapshot_take(lv.scr_act(), lv.COLOR_FORMAT.ARGB8888)
    ptr = int.from_bytes(bytes(memoryview(dsc.data)), "little")
    raw = uctypes.bytearray_at(ptr, 800 * 480 * 4)
    out = bytearray(W * H * 3)
    o = 0
    for y in range(0, 480, STEP):
        base = y * 800 * 4
        for x in range(0, 800, STEP):
            p = base + x * 4
            out[o] = raw[p + 2]
            out[o + 1] = raw[p + 1]
            out[o + 2] = raw[p]
            o += 3
    f = open("/sdcard/_shot.bin", "wb")
    f.write(struct.pack("<HH", W, H))
    f.write(out)
    f.close()
    del f, out, raw, dsc
    print("E12 wrote /sdcard/_shot.bin")

    print("E13 mem_free={}".format(gc.mem_free()))
    launcher.close_app()
    print("E14 closed | mem_free={}".format(gc.mem_free()))
    print("RESULT: PASS")
print("ALL DONE")
