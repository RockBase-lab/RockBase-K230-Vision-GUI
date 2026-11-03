# Load all 16 GUI kmodels into one process and report whether the board survives.
# AGENTS.md rule 14 claims ten is the ceiling (wedge while loading the ninth of
# the Detect alternates). This probe tests 10 boot models + 6 new-app models.
# Headless: nn.kpu needs no display, so no --lvgl flag required.

import gc
import sys
import time

import nncase_runtime as nn

KMODEL_DIR = "/sdcard/examples/kmodel"

MODELS = [
    # the ten currently boot-preloaded
    "face_detection_320.kmodel",
    "face_liveness_rgb.kmodel",
    "face_landmark.kmodel",
    "face_recognition_mobile.kmodel",
    "yolov8n_320.kmodel",
    "yolo11n-pose.kmodel",
    "yolo_license_plate_det.kmodel",
    "licence_reco.kmodel",
    "ocr_det_int16.kmodel",
    "ocr_rec_int16.kmodel",
    # the six new-app models
    "person_detect_yolov5n.kmodel",
    "yolov5n-falldown.kmodel",
    "hand_det.kmodel",
    "yolo11n-obb.kmodel",
    "yolov8n_seg_320.kmodel",
    "face_parse.kmodel",
]

kpus = []
for i, name in enumerate(MODELS):
    path = KMODEL_DIR + "/" + name
    t0 = time.ticks_ms()
    try:
        k = nn.kpu()
        k.load_kmodel(path)
    except BaseException as e:
        print("LOAD-FAIL {} #{}: {}".format(name, i + 1, e))
        sys.print_exception(e)
        break
    kpus.append(k)
    print("OK {} #{} {}ms heap_free={}KB".format(
        name, len(kpus), time.ticks_diff(time.ticks_ms(), t0), gc.mem_free() // 1024))

print("RESIDENT={}/{}".format(len(kpus), len(MODELS)))
print("DONE heap_free={}KB".format(gc.mem_free() // 1024))
