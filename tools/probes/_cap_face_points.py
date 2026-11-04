# Captures the Points app screen to a raw file for host-side viewing.
#
# The view is chosen by VIEW_INDEX (0 = Mesh, 1 = Contour, 2 = Off), so a colour
# or layout change can be checked per view without touching the probe body.
# The snapshot needs about 1.5 MB of contiguous heap, so it is taken right after
# the app is up and a few frames have run, then downsampled to 400x240 before
# being written. Run with --lvgl and a face in front of the camera.

import gc
import struct
import sys
import time

VIEW_INDEX = 1

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("core", "apps.app_face_points", "apps.app_face_det", "apps",
              "core.errors", "ui.beep", "launcher"):
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
    if item["id"] == "landmark":
        meta = item
        break
launcher.open_app(meta)
app = launcher.active_app
app.view_idx = VIEW_INDEX
print("C1 app up face={} view={}".format(app.has_face, VIEW_INDEX))

for i in range(12):
    app.step()
    lv.task_handler()
print("C2 after warmup face={} pts={} mem_free={}".format(
    app.has_face, app._have_pts, gc.mem_free()))

lv.refr_now(lv.disp_get_default())

STEP = 2
W = 800 // STEP
H = 480 // STEP
dsc = lv.snapshot_take(lv.scr_act(), lv.COLOR_FORMAT.ARGB8888)
print("C3 snapshot {}x{}".format(dsc.header.w, dsc.header.h))

ptr = int.from_bytes(bytes(memoryview(dsc.data)), "little")
raw = uctypes.bytearray_at(ptr, 800 * 480 * 4)
print("C4 ptr ok bytes={}".format(len(raw)))

out = bytearray(W * H * 3)
o = 0
row_bytes = 800 * 4
for y in range(0, 480, STEP):
    base = y * row_bytes
    for x in range(0, 800, STEP):
        p = base + x * 4
        # memory order is B, G, R, A
        out[o] = raw[p + 2]
        out[o + 1] = raw[p + 1]
        out[o + 2] = raw[p]
        o += 3

f = open("/sdcard/_shot.bin", "wb")
f.write(struct.pack("<HH", W, H))
f.write(out)
f.close()
del f, out, raw, dsc
gc.collect()
print("C5 wrote /sdcard/_shot.bin {}x{} mem_free={}".format(W, H, gc.mem_free()))

launcher.close_app()
print("C6 closed")
print("RESULT: DONE")
