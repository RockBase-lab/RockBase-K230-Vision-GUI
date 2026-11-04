# Captures the Recognize app with a match on screen.
#
# Enrolls the face currently in view as "probe", runs a few frames so the match
# is drawn, snapshots the screen, then removes the entry so the probe leaves the
# database as it found it. Run with --lvgl and a face in front of the camera.

import os
import struct
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("core", "apps.app_face_recognize", "apps.app_face_points",
              "apps.app_face_det", "apps", "core.errors", "ui.beep",
              "ui.kb_layer", "launcher"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv
import uctypes

_f = open("/sdcard/factory/gui/launcher.py")
gui_src = _f.read()
_f.close()
del _f

G = {"__name__": "gui_probe"}
exec(compile(gui_src, "launcher.py", "exec"), G)
# The tables and classes live in shell/ after the layer split; importing them
# here also keeps sys.path bootstrapped by the exec above.
from shell.registry import FOLDER_MEMBERS
from shell.home import Launcher
from shell import tile_cache

tile_cache.ensure_cache_dir()

launcher = Launcher()
meta = None
for item in FOLDER_MEMBERS["face"]:
    if item["id"] == "recognize":
        meta = item
        break
launcher.open_app(meta)
app = launcher.active_app
print("C1 app up faces={} empty_panel={}".format(
    len(app.db_names), app._empty_panel is not None))

if app._empty_panel is not None:
    app._close_empty_panel()

print("C2 hold still, 3 s ...")
for s in range(3, 0, -1):
    print("   ", s)
    time.sleep_ms(1000)

# Get a feature, enroll it, then keep stepping so the label reflects a match.
for i in range(8):
    app.step()
    lv.task_handler()
if app._pending_feat is None:
    print("C3 no feature, nothing to show")
else:
    app._db_append("probe", app._pending_feat, "live")
    print("C3 enrolled probe, faces={}".format(len(app.db_names)))
    for i in range(4):
        app.step()
        lv.task_handler()
    print("C4 name={} score={:.3f} has_face={}".format(
        app._name, app._score, app.has_face))
    print("C5 label='{}'".format(app.box_label.get_text()))

lv.refr_now(lv.disp_get_default())

STEP = 2
W = 800 // STEP
H = 480 // STEP
dsc = lv.snapshot_take(lv.scr_act(), lv.COLOR_FORMAT.ARGB8888)
print("C6 snapshot {}x{}".format(dsc.header.w, dsc.header.h))
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
print("C7 wrote /sdcard/_shot.bin")

# Leave the database as it was found.
while app.db_names:
    app._db_delete(0)
print("C8 db cleaned: {}".format(os.listdir("/data/face/recognize")))
launcher.close_app()
print("C9 closed")
print("RESULT: DONE")
