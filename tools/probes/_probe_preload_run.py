# Can every GUI model stay resident AND still run?
#
# Loads all ten models the GUI uses, keeps them resident, then runs three of them
# (one per input size: 320, 640) through ai2d on a synthetic frame. Loading proves
# nothing on its own: the KPU output arena is allocated at run time, so a preload
# design only works if inference still succeeds with every model held.
#
# Run without --lvgl. Every kpu is released before exit.

import gc
import time

import nncase_runtime as nn
import ulab.numpy as np
from libs.AI2D import Ai2d

KMODEL_DIR = "/sdcard/examples/kmodel"

RESIDENT = [
    "yolov8n_320.kmodel",
    "yolo11n-pose.kmodel",
    "yolo_license_plate_det.kmodel",
    "licence_reco.kmodel",
    "ocr_det_int16.kmodel",
    "ocr_rec_int16.kmodel",
    "face_detection_320.kmodel",
    "face_landmark.kmodel",
    "face_recognition_mobile.kmodel",
    "face_liveness_rgb.kmodel",
]


def load_all():
    held = {}
    for name in RESIDENT:
        path = KMODEL_DIR + "/" + name
        t0 = time.ticks_ms()
        try:
            kpu = nn.kpu()
            kpu.load_kmodel(path)
        except BaseException as error:
            print("L FAIL {} {} free={}".format(name, error, gc.mem_free()))
            continue
        held[name] = kpu
        print("L ok {:44s} {:>4d} ms resident={} free={}".format(
            name, time.ticks_diff(time.ticks_ms(), t0), len(held),
            gc.mem_free()))
    gc.collect()
    return held


def run_one(label, kpu, in_w, in_h, out_w, out_h, pad_top):
    """Run one inference with a flat frame; returns (ai2d_ms, kpu_ms, dims)."""
    a2d = Ai2d()
    a2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                       np.uint8, np.uint8)
    if pad_top:
        a2d.pad([0, 0, 0, 0, pad_top, pad_top, 0, 0], 0, [128, 128, 128])
    a2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
    a2d.build([1, 3, in_h, in_w], [1, 3, out_h, out_w])

    frame = np.zeros((3, in_h, in_w), dtype=np.uint8)
    frame[:] = 128
    t0 = time.ticks_ms()
    tin = a2d.run(frame)
    a2d_ms = time.ticks_diff(time.ticks_ms(), t0)
    del frame

    kpu.set_input_tensor(0, tin)
    t0 = time.ticks_ms()
    kpu.run()
    kpu_ms = time.ticks_diff(time.ticks_ms(), t0)

    ot = kpu.get_output_tensor(0)
    try:
        out = ot.to_numpy()
        shape = tuple(out.shape)
        del out
    finally:
        ot.release()
    del tin
    a2d.deinit()
    return a2d_ms, kpu_ms, shape


print("R0 free at start={} ({} KB)".format(gc.mem_free(), gc.mem_free() // 1024))
held = load_all()
print("R1 resident={} free={} ({} KB)".format(
    len(held), gc.mem_free(), gc.mem_free() // 1024))

# One run per distinct input size, in the order an app would use them.
TESTS = [
    ("yolov8n_320.kmodel", 800, 480, 320, 320, 80),
    ("face_detection_320.kmodel", 800, 480, 320, 320, 80),
    ("ocr_det_int16.kmodel", 800, 480, 640, 640, 160),
]

ok = 0
for name, iw, ih, ow, oh, pad in TESTS:
    kpu = held.get(name)
    if kpu is None:
        print("R2 {} SKIP not resident".format(name))
        continue
    try:
        a2d_ms, kpu_ms, shape = run_one(name, kpu, iw, ih, ow, oh, pad)
        ok += 1
        print("R2 {:32s} ai2d={:>3d} ms kpu={:>3d} ms out={} free={}".format(
            name, a2d_ms, kpu_ms, shape, gc.mem_free()))
    except BaseException as error:
        print("R3 {:32s} RUN FAILED {} free={}".format(
            name, error, gc.mem_free()))

print("R4 runs_ok={}/{} resident={}".format(ok, len(TESTS), len(held)))

for name, kpu in held.items():
    try:
        kpu.release()
    except BaseException as error:
        print("R5 release {}: {}".format(name, error))
del held
gc.collect()
print("R6 after release free={} ({} KB)".format(
    gc.mem_free(), gc.mem_free() // 1024))
print("RESULT: DONE")
