# Open Mask through the real launcher, run about 6 s of live preview, and report
# frame rate, parsed faces, mask pixels actually written, and heap. The pixel
# count is the part that matters: a visible widget proves nothing if the ARGB
# buffer never received data.
#
# Run with --lvgl. Point the camera at a face for a non-zero result.

import gc
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("apps.app_mask", "apps", "apps.error", "ui.beep", "launcher",
              "camera_fov", "model_pool"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv
import ulab.numpy as np

src = open("/sdcard/factory/gui/launcher.py").read()
G = {"__name__": "gui_probe"}
exec(compile(src, "launcher.py", "exec"), G)
G["_ensure_cache_dir"]()

meta = {m["id"]: m for m in G["FOLDER_MEMBERS"]["face"]}["mask"]
print("MODULE", meta["module"])

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
peak = 0
t0 = time.ticks_ms()
deadline = 6000
while time.ticks_diff(time.ticks_ms(), t0) < deadline:
    try:
        app.step()
        lv.task_handler()
        frames += 1
        if app.last_faces > peak:
            peak = app.last_faces
    except BaseException as e:
        err += 1
        print("E step:", e)
        if err > 3:
            break
    time.sleep_ms(5)
el = time.ticks_diff(time.ticks_ms(), t0)
print("R frames={} fps={:.2f} max_faces={} errors={} mem_free={}".format(
    frames, frames * 1000.0 / el, peak, err, gc.mem_free()))

# Pixel evidence: nonzero alpha inside the mask buffer means the post-process
# wrote through the shared view into the bytearray the dsc points at.
try:
    alpha = app.masks[:, :, 3]
    nz = int(np.sum(alpha > 0))
    print("P alpha_nonzero={} of {} widget_hidden={}".format(
        nz, app.masks.shape[0] * app.masks.shape[1],
        app.mask_widget.has_flag(lv.obj.FLAG.HIDDEN)))
    if nz > 0:
        ys = [r for r in range(app.masks.shape[0]) if np.max(alpha[r]) > 0]
        print("P rows {}..{}".format(min(ys), max(ys)))
except BaseException as e:
    print("P FAILED:", e)

launcher.close_app()
print("C closed mem_free={}".format(gc.mem_free()))
print("DONE mem_free={}".format(gc.mem_free()))
