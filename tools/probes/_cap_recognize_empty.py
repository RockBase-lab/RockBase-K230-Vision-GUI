# Captures the Recognize app with the empty-database panel on screen.
#
# The panel is the entry experience for a board that has never enrolled a face,
# so it is worth one screenshot. Run with --lvgl.

import struct
import sys

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("apps.app_face_recognize", "apps", "apps.error", "ui.beep",
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
G["_ensure_cache_dir"]()

launcher = G["Launcher"]()
meta = None
for item in G["FOLDER_MEMBERS"]["face"]:
    if item["id"] == "recognize":
        meta = item
        break
launcher.open_app(meta)
app = launcher.active_app
print("C1 faces={} empty_panel={}".format(
    len(app.db_names), app._empty_panel is not None))

for i in range(6):
    app.step()
    lv.task_handler()

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
print("C2 wrote /sdcard/_shot.bin")
launcher.close_app()
print("C3 closed")
print("RESULT: DONE")
