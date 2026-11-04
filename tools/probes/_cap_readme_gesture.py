# Captures the Gesture app for the README. The hand has to be in front of the lens
# before this runs; the app reports "--" until the classifier recognises a posture,
# so the probe waits for a named gesture and keeps looping until the label is stable.
#
# The expected label is read from EXPECT; set it to "five" for an open palm or
# "yeah" for a V sign. A mismatch is reported instead of silently capturing the
# wrong posture, because a picture with "--" in the bar is not usable.
#
# Pull with: python tools/_pull_file.py COM56 921600 /data/capture/gesture_<label>.jpg img/app-gesture.jpg
# Run with --lvgl. Everything runs in try/finally so a failure cannot leave the
# Sensor singleton claimed.

import os
import sys
import time
import uctypes

EXPECT = "yeah"          # "five" (open palm) or "yeah" (V sign)

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("core", "apps", "core.errors", "apps.app_gesture", "apps.anchor_base",
              "ui.beep", "launcher", "hardware.camera_fov", "core.model_pool"):
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
# The tables and classes live in shell/ after the layer split; importing them
# here also keeps sys.path bootstrapped by the exec above.
from shell.registry import FOLDER_MEMBERS
from shell.home import Launcher
from shell import tile_cache

tile_cache.ensure_cache_dir()

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


launcher = Launcher()
launcher.build_home()

meta = None
for item in FOLDER_MEMBERS["vision"]:
    if item["id"] == "gesture":
        meta = item
        break

try:
    launcher.open_app(meta)
    app = launcher.active_app
    if app is None:
        print("RESULT PANEL (app unavailable)")
    else:
        # Count consecutive frames that agree on the expected label, then capture
        # immediately: the hand drops out of frame within a second or two, and a
        # snapshot taken after the loop shows "--" in the bar even though the
        # skeleton was correct a moment earlier.
        hits = 0
        seen = {}
        locked = False
        for _ in range(170):
            app.step()
            lv.task_handler()
            time.sleep_ms(50)
            label = app.last_gesture
            if label:
                seen[label] = seen.get(label, 0) + 1
            if label == EXPECT:
                hits += 1
                if hits >= 12:
                    locked = True
                    break
            else:
                hits = 0
        print("[GESTURE] expect={} last={} hits={} seen={} fps={}".format(
            EXPECT, app.last_gesture, hits, seen, app.fps_text))
        if not locked:
            print("RESULT NO-MATCH (held {} instead)".format(app.last_gesture))
        else:
            # One more frame so the bar reflects the same frame the skeleton does.
            app.step()
            lv.task_handler()
            save_jpeg("gesture_" + EXPECT)
            print("RESULT OK")
finally:
    try:
        launcher.close_app()
    except BaseException as error:
        print("RESULT CLOSE-FAIL {}".format(error))

print("DONE")
