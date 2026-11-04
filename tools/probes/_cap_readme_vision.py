# Captures the camera-backed apps used by the README as JPEG files under
# /data/capture/. A live preview does not compress with RLE (a full frame expands
# past 1 MB and raises MemoryError), so the snapshot is converted to RGB888 and
# handed to the firmware's JPEG encoder.
#
# Pull the files with tools/_pull_file.py; the probe prints one RESULT line per app.
# Apps run in ascending risk order: the two-model chains (OCR, Plate) go last, and
# every close path runs in finally so a failure cannot leave the sensor claimed.

import os
import sys
import time
import uctypes

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("apps", "apps.error", "apps.anchor_det", "apps.app_vision",
              "apps.app_pose", "apps.app_ocr", "apps.app_plate",
              "apps.app_face_points", "apps.app_gesture", "apps.app_segment",
              "apps.app_orient", "apps.app_person", "apps.app_fall",
              "apps.app_mask", "ui.beep", "launcher", "camera_fov",
              "model_pool", "font_pool", "ocr_dict"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv
import image
import ulab.numpy as np

_f = open("/sdcard/factory/gui/launcher.py")
_gui_src = _f.read()
_f.close()
del _f

G = {"__name__": "gui_probe"}
exec(compile(_gui_src, "launcher.py", "exec"), G)
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
    """Snapshot the active screen and write it as JPEG to /data/capture/<tag>.jpg."""
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


def meta_of(group, app_id):
    for item in G["FOLDER_MEMBERS"][group]:
        if item["id"] == app_id:
            return item
    return None


launcher = G["Launcher"]()
launcher.build_home()

TARGETS = [
    ("face", "face_det"),
    ("vision", "detect"),
    ("vision", "pose"),
    ("vision", "ocr"),
    ("vision", "plate"),
]

for group, app_id in TARGETS:
    meta = meta_of(group, app_id)
    if meta is None:
        print("RESULT {} SKIP no meta".format(app_id))
        continue
    try:
        launcher.open_app(meta)
        app = launcher.active_app
        if app is None:
            print("RESULT {} PANEL (app unavailable)".format(app_id))
            continue
        for _ in range(14):
            app.step()
            lv.task_handler()
            time.sleep_ms(60)
        save_jpeg(app_id)
        print("RESULT {} OK".format(app_id))
    except BaseException as error:
        print("RESULT {} FAIL {}".format(app_id, error))
    finally:
        try:
            launcher.close_app()
        except BaseException as close_error:
            print("RESULT {} CLOSE-FAIL {}".format(app_id, close_error))

print("DONE")
