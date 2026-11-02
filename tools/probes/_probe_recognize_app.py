# End-to-end check of the Recognize app through the real launcher.
#
# Enrolls one face by calling the app's own save path (skipping the keyboard),
# reloads the database from disk, confirms the match, deletes it again, and
# measures the frame rate. Run with --lvgl and a face in front of the camera.

import gc
import json
import os
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("apps.app_face_recognize", "apps.app_face_points",
              "apps.app_face_det", "apps", "apps.error", "ui.beep",
              "ui.kb_layer", "launcher"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv
import ulab.numpy as np

_f = open("/sdcard/factory/gui/launcher.py")
gui_src = _f.read()
_f.close()
del _f

G = {"__name__": "gui_probe"}
exec(compile(gui_src, "launcher.py", "exec"), G)
G["_ensure_cache_dir"]()

DIR = "/data/face/recognize"
print("E1 dir exists: {}".format(os.stat(DIR) is not None))
try:
    print("E2 entries before: {}".format(os.listdir(DIR)))
except BaseException as e:
    print("E2 entries before: error {}".format(e))

launcher = G["Launcher"]()
meta = None
for item in G["FOLDER_MEMBERS"]["face"]:
    if item["id"] == "recognize":
        meta = item
        break
print("E3 meta name={} module={} immersive={}".format(
    meta["name"], meta["module"], meta.get("immersive")))

t0 = time.ticks_ms()
launcher.open_app(meta)
print("E4 open_app {}ms".format(time.ticks_diff(time.ticks_ms(), t0)))

app = launcher.active_app
if app is None:
    print("RESULT: FAIL - app is None")
else:
    print("E5 class={} dim={} faces={} empty_panel={}".format(
        type(app).__name__, app.__class__ and len(app._last_feat or []) or 0,
        len(app.db_names), app._empty_panel is not None))

    for _ in range(2):
        lv.task_handler()
        time.sleep_ms(80)
    print("E6 bar title='{}' name='{}' hint='{}'".format(
        app.seg_title.get_text(), app.seg_name.get_text(),
        app.seg_hint.get_text()))

    print("E7 hold still, 3 s ...")
    for s in range(3, 0, -1):
        print("   ", s)
        time.sleep_ms(1000)

    # Frames: count how often a feature was produced.
    N = 15
    feats = 0
    t0 = time.ticks_ms()
    for i in range(N):
        app.step()
        lv.task_handler()
        if app._pending_feat is not None:
            feats += 1
    ms = time.ticks_diff(time.ticks_ms(), t0) / float(N)
    print("E8 {} frames | features {} | {:.1f} ms/frame | {:.1f} fps".format(
        N, feats, ms, 1000.0 / ms if ms else 0))
    print("E9 has_face={} feat_len={}".format(
        app.has_face, len(app._pending_feat) if app._pending_feat else 0))

    if app._pending_feat is None:
        print("RESULT: NO_FEATURE - no face detected, cannot test enrollment")
    else:
        feat = app._pending_feat
        print("E10 enrolling as 'probe'")
        n = app._db_append("probe", feat, "live")
        print("E11 sample={} faces={} next={}".format(
            n, len(app.db_names), app._db_next))
        print("E12 entries: {}".format(sorted(os.listdir(DIR))))
        try:
            f = open(DIR + "/db.json", "r")
            raw = f.read()
            f.close()
            del f
            print("E13 index: {}".format(raw))
        except BaseException as e:
            print("E13 index read error {}".format(e))

        size = 0
        try:
            size = os.stat(DIR + "/f001.bin")[6]
        except BaseException as e:
            print("E14 bin stat error", e)
        print("E14 bin size={} (expect {})".format(size, 512 * 4))

        # Reload through a fresh app instance's loader.
        app.db_names = []
        app.db_feats = []
        app.db_srcs = []
        app._db_load()
        print("E15 reloaded faces={} names={}".format(
            len(app.db_names), app.db_names))

        name, score = app._db_match(feat)
        print("E16 match self: name={} score={:.4f} (expect >= {})".format(
            name, score, 0.85))

        # An unrelated vector must land below the threshold. _db_match takes
        # ulab arrays, so build one rather than a Python list.
        rnd = [(i * 37 % 101) - 50.0 for i in range(512)]
        rnd_arr = np.array(rnd, dtype=np.float)
        norm = float(np.dot(rnd_arr, rnd_arr)) ** 0.5
        rnd_arr = rnd_arr / norm
        name2, score2 = app._db_match(rnd_arr)
        print("E17 match synthetic: name={} score={:.4f} (expect < 0.85)".format(
            name2, score2))

        app._db_delete_name("probe")
        print("E18 after delete: faces={} entries={}".format(
            len(app.db_names), sorted(os.listdir(DIR))))

    print("E19 mem_free={}".format(gc.mem_free()))
    launcher.close_app()
    print("E20 closed | mem_free={}".format(gc.mem_free()))
    print("RESULT: PASS")
print("ALL DONE")
