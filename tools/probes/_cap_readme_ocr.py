# Captures the OCR app mid-result: live preview frames, then one scan pass, then a
# JPEG snapshot of the frozen frame with the boxes and the recognised text panel.
#
# The screen has to be pointed at readable text before this runs. The preview is
# live, so the captured picture is whatever the camera sees at that moment.
#
# OCR freezes on scan, which is the state worth showing: a LIVE screenshot shows no
# boxes and no text. The scan is triggered through _toggle_scan() (the same path the
# OK key takes via ctx.take_ok()) so the production code runs unchanged.
#
# The JPEG goes to /data/capture/ocr.jpg; pull it with tools/_pull_file.py.
# Run with --lvgl. The whole body runs in try/finally: a probe that dies before
# close_app leaves the Sensor singleton claimed, and only machine.reset() clears it.

import os
import sys
import time
import uctypes

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("apps", "apps.error", "apps.app_ocr", "ui.beep", "launcher",
              "camera_fov", "model_pool", "font_pool", "ocr_dict"):
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
    if item["id"] == "ocr":
        meta = item
        break

try:
    launcher.open_app(meta)
    app = launcher.active_app
    if app is None:
        print("RESULT PANEL (app unavailable)")
    else:
        # Let the live preview settle so the frozen frame is sharp: focus and
        # exposure need a few frames after the sensor starts.
        for _ in range(20):
            app.step()
            lv.task_handler()
            time.sleep_ms(60)

        print("[OCR] state before scan: {}".format(app.state))
        app._toggle_scan()
        # step() runs the blocking pass and leaves the app in RESULT.
        for _ in range(6):
            app.step()
            lv.task_handler()
            time.sleep_ms(80)
        print("[OCR] state after scan: {} lines={}".format(
            app.state, len(getattr(app, "texts", []) or [])))
        for item in (getattr(app, "texts", []) or [])[:6]:
            print("[OCR] text: {!r}".format(item))

        save_jpeg("ocr")
        print("RESULT OK")
finally:
    try:
        launcher.close_app()
    except BaseException as error:
        print("RESULT CLOSE-FAIL {}".format(error))

print("DONE")
