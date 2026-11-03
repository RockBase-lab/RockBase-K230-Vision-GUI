# Production-order rule 14 check: preload the 16 BOOT_MODELS exactly as boot
# does, then open Gesture through the real launcher so handkp_det becomes a 17th
# resident kpu, and run live frames through the app's own inference path. This
# is the state a real boot reaches when the user opens Gesture; a hand-rolled
# numpy_ref slice into ai2d is NOT this path and wedged the board on its own.
#
# Run with --lvgl. Point the camera at a hand for a non-zero gesture result.

import gc
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("apps.app_gesture", "apps", "apps.error", "ui.beep", "launcher",
              "camera_fov", "model_pool"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv
from model_pool import preload, loaded_count

BOOT_MODELS = [
    "face_detection_320.kmodel", "face_liveness_rgb.kmodel",
    "face_landmark.kmodel", "face_recognition_mobile.kmodel",
    "yolov8n_320.kmodel", "yolo11n-pose.kmodel",
    "yolo_license_plate_det.kmodel", "licence_reco.kmodel",
    "ocr_det_int16.kmodel", "ocr_rec_int16.kmodel",
    "person_detect_yolov5n.kmodel", "yolov5n-falldown.kmodel",
    "hand_det.kmodel", "yolo11n-obb.kmodel",
    "yolov8n_seg_320.kmodel", "face_parse.kmodel",
]
preload([("/sdcard/examples/kmodel/" + m, m) for m in BOOT_MODELS])
print("BOOT resident={} heap={}".format(loaded_count(), gc.mem_free()))

src = open("/sdcard/factory/gui/launcher.py").read()
G = {"__name__": "gui_probe"}
exec(compile(src, "launcher.py", "exec"), G)
G["_ensure_cache_dir"]()

meta = {m["id"]: m for m in G["FOLDER_MEMBERS"]["vision"]}["gesture"]

launcher = None
try:
    t0 = time.ticks_ms()
    launcher = G["Launcher"]()
    launcher.open_app(meta)
    print("O open {}ms resident={} heap={}".format(
        time.ticks_diff(time.ticks_ms(), t0), loaded_count(), gc.mem_free()))

    app = launcher.active_app
    if app is None:
        print("RESULT FAIL app is None")
        raise SystemExit(1)

    frames = 0
    err = 0
    seen = {}
    max_infer = 0.0
    t0 = time.ticks_ms()
    deadline = 8000
    while time.ticks_diff(time.ticks_ms(), t0) < deadline:
        try:
            app.step()
            lv.task_handler()
            frames += 1
            if app.last_gesture:
                seen[app.last_gesture] = seen.get(app.last_gesture, 0) + 1
            if app.infer_ms > max_infer:
                max_infer = app.infer_ms
        except BaseException as e:
            err += 1
            print("E step:", e)
            if err > 3:
                break
        time.sleep_ms(5)
    el = time.ticks_diff(time.ticks_ms(), t0)
    print("R frames={} fps={:.2f} max_infer={:.0f}ms errors={} resident={} "
          "heap={}".format(frames, frames * 1000.0 / el, max_infer, err,
                           loaded_count(), gc.mem_free()))
    print("G gestures_seen={}".format(seen))
    print("RESULT: DONE")

    launcher.close_app()
    launcher = None
finally:
    if launcher is not None:
        try:
            launcher.close_app()
        except BaseException as e:
            print("C close error:", e)
    print("FIN heap={}".format(gc.mem_free()))
