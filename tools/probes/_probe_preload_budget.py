# Preload budget: can every GUI model stay resident at once?
#
# Lists the kmodels under /sdcard/examples/kmodel with their sizes, then loads the
# models the GUI apps actually use one at a time, keeping each resident, and prints
# the free heap after every load. The point is to answer "can boot preload them
# all" with a measurement instead of an estimate.
#
# Run without --lvgl: no widget is created. The probe releases every kpu before it
# exits, so the board is left clean.

import gc
import os
import time

import nncase_runtime as nn

KMODEL_DIR = "/sdcard/examples/kmodel"

# Every model the GUI loads, in the order the app families use them. Duplicates
# are listed once: a preload cache is keyed by path.
CANDIDATES = [
    "yolov8n_320.kmodel",             # Vision / Detect
    "yolo11n-pose.kmodel",            # Vision / Pose
    "yolo_license_plate_det.kmodel",  # Vision / Plate
    "licence_reco.kmodel",            # Vision / Plate
    "ocr_det_int16.kmodel",           # Vision / OCR
    "ocr_rec_int16.kmodel",           # Vision / OCR
    "face_detection_320.kmodel",      # Face family detector, shared by 4 apps
    "face_landmark.kmodel",           # Face / Points
    "face_recognition_mobile.kmodel", # Face / Recognize
    "face_liveness_rgb.kmodel",       # Face / Liveness
]

print("P0 files under {}".format(KMODEL_DIR))
try:
    names = os.listdir(KMODEL_DIR)
except BaseException as error:
    names = []
    print("P0 listdir failed: {}".format(error))
total = 0
for name in sorted(names):
    if not name.endswith(".kmodel"):
        continue
    try:
        size = os.stat(KMODEL_DIR + "/" + name)[6]
    except BaseException:
        size = -1
    total += size if size > 0 else 0
    print("P1 {:44s} {:>9d} B".format(name, size))
print("P1 total kmodel bytes={} ({:.1f} MB)".format(total, total / 1048576.0))

gc.collect()
free0 = gc.mem_free()
print("P2 mem_free at start={} ({:.2f} MB)".format(free0, free0 / 1048576.0))

loaded = []
for name in CANDIDATES:
    path = KMODEL_DIR + "/" + name
    if name not in names:
        print("P3 {:44s} SKIP missing".format(name))
        continue
    before = gc.mem_free()
    t0 = time.ticks_ms()
    kpu = None
    try:
        kpu = nn.kpu()
        kpu.load_kmodel(path)
        dt = time.ticks_diff(time.ticks_ms(), t0)
        gc.collect()
        after = gc.mem_free()
        loaded.append((name, kpu))
        print("P3 {:44s} {:>5d} ms  heap {} -> {} ({:+.0f} KB) resident={}".format(
            name, dt, before, after, (after - before) / 1024.0, len(loaded)))
    except BaseException as error:
        dt = time.ticks_diff(time.ticks_ms(), t0)
        gc.collect()
        print("P4 {:44s} {:>5d} ms  FAILED {} free={}".format(
            name, dt, error, gc.mem_free()))
        if kpu is not None:
            try:
                kpu.release()
            except BaseException:
                pass

gc.collect()
free_all = gc.mem_free()
print("P5 resident={} free={} ({:.2f} MB) consumed={} KB".format(
    len(loaded), free_all, free_all / 1048576.0,
    (free0 - free_all) / 1024.0))

# Load time is what an app pays today at open; the sum is what a boot preload
# would have to spend instead.
for name, kpu in loaded:
    try:
        kpu.release()
    except BaseException as error:
        print("P6 release {} failed: {}".format(name, error))
del loaded
gc.collect()
print("P7 after release free={} ({:.2f} MB)".format(
    gc.mem_free(), gc.mem_free() / 1048576.0))
print("RESULT: DONE")
