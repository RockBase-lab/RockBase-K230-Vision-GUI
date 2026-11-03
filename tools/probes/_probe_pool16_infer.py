# With all 16 kmodels resident, run one real inference on each new-app model to
# prove the NPU still works at 16. Loading alone does not prove this: the wedge
# in the old note appeared at load time, but arenas are also allocated per run.
# Headless (no lv), synthetic frames only.

import gc
import sys
import time

import nncase_runtime as nn
import ulab.numpy as np

KMODEL_DIR = "/sdcard/examples/kmodel"

NEW_MODELS = [
    ("person_detect_yolov5n.kmodel", [1, 3, 640, 640]),
    ("yolov5n-falldown.kmodel", [1, 3, 640, 640]),
    ("hand_det.kmodel", [1, 3, 512, 512]),
    ("yolo11n-obb.kmodel", [1, 3, 320, 320]),
    ("yolov8n_seg_320.kmodel", [1, 3, 320, 320]),
    ("face_parse.kmodel", [1, 3, 320, 320]),
]
BOOT_MODELS = [
    "face_detection_320.kmodel", "face_liveness_rgb.kmodel",
    "face_landmark.kmodel", "face_recognition_mobile.kmodel",
    "yolov8n_320.kmodel", "yolo11n-pose.kmodel",
    "yolo_license_plate_det.kmodel", "licence_reco.kmodel",
    "ocr_det_int16.kmodel", "ocr_rec_int16.kmodel",
]

pool = {}
for name in BOOT_MODELS + [m[0] for m in NEW_MODELS]:
    k = nn.kpu()
    k.load_kmodel(KMODEL_DIR + "/" + name)
    pool[name] = k
print("LOADED={} heap_free={}KB".format(len(pool), gc.mem_free() // 1024))

for name, shape in NEW_MODELS:
    t0 = time.ticks_ms()
    try:
        inp = nn.from_numpy(np.zeros(shape, dtype=np.uint8))
        k = pool[name]
        k.set_input_tensor(0, inp)
        k.run()
        ot = k.get_output_tensor(0)
        try:
            arr = ot.to_numpy()
            out_shape = arr.shape
        finally:
            ot.release()
        print("INFER-OK {} out={} {}ms heap_free={}KB".format(
            name, out_shape, time.ticks_diff(time.ticks_ms(), t0),
            gc.mem_free() // 1024))
    except BaseException as e:
        print("INFER-FAIL {}: {}".format(name, e))
        sys.print_exception(e)

print("ALIVE heap_free={}KB".format(gc.mem_free() // 1024))
