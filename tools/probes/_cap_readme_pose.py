# Captures the Pose app (17-keypoint skeleton) for the README. The subject has to
# be standing in frame before this runs; the app draws nothing until the detector
# reports a person with enough valid joints (MIN_JOINTS).
#
# The probe waits for the skeleton to appear and stays in the loop long enough for
# the drawn points to stabilise, then prints the person count and FPS so the image
# can be matched against real output.
#
# Pull with: python tools/_pull_file.py COM56 921600 /data/capture/pose.jpg img/app-pose.jpg
# Run with --lvgl. Everything runs in try/finally so a failure cannot leave the
# Sensor singleton claimed.

import os
import sys
import time
import uctypes

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("apps", "apps.error", "apps.app_pose", "ui.beep", "launcher",
              "camera_fov", "model_pool"):
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
for item in G["FOLDER_MEMBERS"]["vision"]:
    if item["id"] == "pose":
        meta = item
        break

try:
    launcher.open_app(meta)
    app = launcher.active_app
    if app is None:
        print("RESULT PANEL (app unavailable)")
    else:
        # Wait for a detected person: capturing before that shows an empty preview
        # with "0 person" in the bar.
        seen = 0
        for _ in range(160):
            app.step()
            lv.task_handler()
            time.sleep_ms(50)
            if app.last_persons > 0:
                seen += 1
                if seen == 15:
                    print("[POSE] skeleton locked, {} person(s)".format(
                        app.last_persons))
        print("[POSE] persons={} count_label={!r} fps={}".format(
            app.last_persons, app.stat_count.get_text(), app.fps_text))
        save_jpeg("pose")
        print("RESULT OK")
finally:
    try:
        launcher.close_app()
    except BaseException as error:
        print("RESULT CLOSE-FAIL {}".format(error))

print("DONE")
