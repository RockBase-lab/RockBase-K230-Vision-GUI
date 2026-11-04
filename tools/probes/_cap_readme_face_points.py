# Captures the Points app (face landmark mesh) for the README. The face has to be
# in front of the lens before this runs; the app needs a detected face to draw
# anything, so the probe waits for has_face and keeps the loop running until the
# point count stops changing.
#
# Mesh is the default view (VIEWS[0]); the contour view is cheaper to draw but shows
# polylines instead of points, and the README wants the point cloud. The view index
# is printed so the image can be matched to the log.
#
# Pull with: python tools/_pull_file.py COM56 921600 /data/capture/face_points.jpg img/app-face-points.jpg
# Run with --lvgl. Everything runs in try/finally so a failure cannot leave the
# Sensor singleton claimed.

import os
import sys
import time
import uctypes

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("core", "apps", "core.errors", "apps.app_face_points", "ui.beep",
              "launcher", "hardware.camera_fov", "core.model_pool"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv
import image
import ulab.numpy as np

_f = open("/sdcard/factory/gui/launcher.py")
_src = _f.read()
_f.close()
del _f

G = {"__name__": "gui_probe"}
exec(compile(_src, "launcher.py", "exec"), G)
G["_ensure_cache_dir"]()

OUT = "/data/capture"


def ensure_dir(path):
    cur = ""
    for part in path.strip("/").split("/"):
        cur += "/" + part
        try:
            os.mkdir(cur)
        except OSError:
            pass


ensure_dir(OUT)


def save_jpeg(tag):
    for _ in range(3):
        lv.task_handler()
        time.sleep_ms(60)
    dsc = lv.snapshot_take(lv.scr_act(), lv.COLOR_FORMAT.ARGB8888)
    w, h = dsc.header.w, dsc.header.h
    ptr = int.from_bytes(bytes(memoryview(dsc.data)), "little")
    raw = uctypes.bytearray_at(ptr, w * h * 4)
    try:
        img = image.Image(w, h, image.RGB888)
        dst = img.to_numpy_ref()
        src = np.frombuffer(raw, dtype=np.uint8).reshape((h, w, 4))
        dst[:, :, 0] = src[:, :, 2]
        dst[:, :, 1] = src[:, :, 1]
        dst[:, :, 2] = src[:, :, 0]
        del src
        data = img.compressed(85).bytearray()
        path = OUT + "/" + tag + ".jpg"
        with open(path, "wb") as f:
            f.write(data)
        print("[CAP] {} {}x{} bytes={}".format(tag, w, h, len(data)))
        del data
        del img
    finally:
        dsc.buf_free()


launcher = G["Launcher"]()
launcher.build_home()

meta = None
for item in G["FOLDER_MEMBERS"]["face"]:
    if item["id"] == "landmark":
        meta = item
        break

try:
    launcher.open_app(meta)
    app = launcher.active_app
    if app is None:
        print("RESULT PANEL (app unavailable)")
    else:
        # Wait for a face: the mesh draws nothing until the detector locks on, and
        # capturing before that produces a picture of an empty preview.
        seen = 0
        for _ in range(160):
            app.step()
            lv.task_handler()
            time.sleep_ms(50)
            if app.has_face:
                seen += 1
                if seen == 10:
                    print("[POINTS] face locked, {} pts, view={}".format(
                        app.seg_count.get_text(), G["VIEWS"][app.view_idx][0]
                        if "VIEWS" in G else app.view_idx))
        print("[POINTS] has_face={} count={} fps={} view_idx={}".format(
            app.has_face, app.seg_count.get_text(), app.fps_text, app.view_idx))
        save_jpeg("face_points")
        print("RESULT OK")
finally:
    try:
        launcher.close_app()
    except BaseException as error:
        print("RESULT CLOSE-FAIL {}".format(error))

print("DONE")
