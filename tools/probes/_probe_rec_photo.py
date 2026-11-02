# Recognize photo-enrollment feature end to end:
#   A1 app boots with the PHOTO switch, live frames still run
#   A2 photos_allowed persists to db.json and reloads
#   A3 photo/live tagging and match filtering
#   A4 one-shot liveness on a live face scores real
# Cleans up its test entries and restores photos_allowed afterwards.

import gc
import json
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in list(sys.modules):
    if _name.startswith("apps."):
        del sys.modules[_name]

import lvgl as lv
import ulab.numpy as np

_f = open("/sdcard/factory/gui/launcher.py")
_src = _f.read()
_f.close()
del _f

G = {"__name__": "gui_probe"}
exec(compile(_src, "launcher.py", "exec"), G)

meta = None
for item in G["FOLDER_MEMBERS"]["face"]:
    if item["id"] == "recognize":
        meta = item
        break

launcher = G["Launcher"]()
launcher.open_app(meta)
app = launcher.active_app
print("A0 switch_widget={} photos_allowed={} db={}".format(
    app._switch is not None, app.photos_allowed, len(app.db_names)))

DB_INDEX = "/data/face/recognize/db.json"
orig_allowed = app.photos_allowed

# A1: run frames with the switch present.
for _ in range(10):
    lv.task_handler()
    app.step()
    time.sleep_ms(60)
lv.task_handler()
print("A1 frames ok fps={} face={}".format(app.fps_text, app.has_face))

# A2: persistence. Set the value directly (a real tap goes through CHECKABLE,
# which a direct call does not), save, and read the file back.
app.photos_allowed = not orig_allowed
app._db_save_index()
with open(DB_INDEX, "r") as f:
    idx = json.loads(f.read())
print("A2a want={} in_file={}".format(not orig_allowed,
                                      idx.get("photos_allowed")))
# Reload path: fresh load must pick the persisted value up.
app.db_names = []
app.db_feats = []
app.db_srcs = []
app._db_load()
print("A2b reload allowed={}".format(app.photos_allowed))
app.photos_allowed = orig_allowed
app._sync_switch()
app._db_save_index()

# A3: synthetic entries, match filtering.
feat_a = np.zeros(512, dtype=np.float)
feat_a[0] = 1.0
feat_b = np.zeros(512, dtype=np.float)
feat_b[1] = 1.0
app._db_append("ZZphoto", feat_a, "photo")
app._db_append("ZZlive", feat_b, "live")
print("A3a srcs={} {}".format(app.db_srcs[-2], app.db_srcs[-1]))

app.photos_allowed = True
n1, s1 = app._db_match(feat_a)
app.photos_allowed = False
n2, s2 = app._db_match(feat_a)
n3, s3 = app._db_match(feat_b)
print("A3b photo_on={} score={:.3f} | photo_off={} | live={}".format(
    n1, s1, n2, n3))

# A3d: multi-angle samples. Two more angles of ZZlive, then the match must
# still name the identity and the sample count must reflect all three.
ang2 = np.zeros(512, dtype=np.float)
ang2[1] = 0.8
ang2[2] = 0.6
ang3 = np.zeros(512, dtype=np.float)
ang3[1] = 0.6
ang3[2] = 0.8
app._db_append("ZZlive", app._normalise(ang2), "live")
app._db_append("ZZlive", app._normalise(ang3), "live")
print("A3d count={} people={} unique={}".format(
    app._db_count("ZZlive"), len(app._db_names_unique()),
    app._db_names_unique()))
app.photos_allowed = True
n4, s4 = app._db_match(app._normalise(ang3))
print("A3e angle3_match={} score={:.3f}".format(n4, s4))

# Reload from disk to confirm src persisted.
app.db_names = []
app.db_feats = []
app.db_srcs = []
app._db_load()
i_p = app.db_names.index("ZZphoto") if "ZZphoto" in app.db_names else -1
i_l = app.db_names.index("ZZlive") if "ZZlive" in app.db_names else -1
print("A3c reload srcs={} {}".format(app.db_srcs[i_p], app.db_srcs[i_l]))

# A4: one-shot liveness on whatever the camera sees.
try:
    from media.sensor import CAM_CHN_ID_1
    ai_img = app.sensor.snapshot(chn=CAM_CHN_ID_1)
    got = app._detect(ai_img)
    if got is not None:
        box, lm = got
        sc = app._liveness(ai_img, box)
        print("A4 live_score={} box={}".format(
            "None" if sc is None else "{:.4f}".format(sc), box))
    else:
        print("A4 no face in view")
except BaseException as e:
    print("A4 liveness probe failed:", e)

# Cleanup: remove test identities, restore the switch value.
for nm in ("ZZphoto", "ZZlive"):
    if nm in app.db_names:
        app._db_delete_name(nm)
app.photos_allowed = orig_allowed
app._sync_switch()
app._db_save_index()
print("A5 cleanup db={} allowed={}".format(len(app.db_names),
                                           app.photos_allowed))

gc.collect()
print("PROBE_DONE mem_free={}".format(gc.mem_free()))
