# Measure the hand-keypoint stage before building the Gesture app: can a 17th
# resident kpu (handkp_det, 25.6 MB) load alongside the boot set, what is its
# output shape and warm cost, and does the crop + 21-point decode produce points
# that classify into a gesture.
#
# Run with --lvgl. Put a hand in front of the camera for a non-zero result.

import gc
import math
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")

import ulab.numpy as np
import nncase_runtime as nn
import aicube
from libs.AI2D import Ai2d
from media.sensor import Sensor, CAM_CHN_ID_1
from hardware.camera_fov import set_preview
from core.model_pool import get as model_get, loaded_count

PREVIEW_W = 800
PREVIEW_H = 480
DET_IN = [512, 512]
KP_IN = [256, 256]
ANCHORS = [26, 27, 53, 52, 75, 71, 80, 99, 106, 82, 99, 134,
           140, 113, 161, 172, 245, 276]

sensor = None
try:
    print("HEAP start={}".format(gc.mem_free()))
    kpu_det = model_get("/sdcard/examples/kmodel/hand_det.kmodel")
    print("POOL after hand_det resident={}".format(loaded_count()))
    t0 = time.ticks_ms()
    kpu_kp = model_get("/sdcard/examples/kmodel/handkp_det.kmodel")
    print("LOAD handkp_det {}ms resident={} heap={}".format(
        time.ticks_diff(time.ticks_ms(), t0), loaded_count(), gc.mem_free()))

    sensor = Sensor(fps=30)
    sensor.reset()
    set_preview(sensor, CAM_CHN_ID_1, PREVIEW_W, PREVIEW_H)
    sensor.set_pixformat(Sensor.RGBP888, chn=CAM_CHN_ID_1)
    sensor.run()

    a2d_det = Ai2d()
    a2d_det.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                          np.uint8, np.uint8)
    a2d_det.pad([0, 0, 0, 0, 160, 160, 0, 0], 0, [0, 0, 0])
    a2d_det.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
    a2d_det.build([1, 3, PREVIEW_H, PREVIEW_W], [1, 3, 512, 512])

    a2d_kp = Ai2d()
    a2d_kp.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                         np.uint8, np.uint8)

    def crop_param(box):
        x1, y1, x2, y2 = box[2], box[3], box[4], box[5]
        length = max(x2 - x1, y2 - y1) / 2
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2
        r = 1.26 * length
        ax = int(max(0, cx - r))
        ay = int(max(0, cy - r))
        bx = int(min(PREVIEW_W - 1, cx + r))
        by = int(min(PREVIEW_H - 1, cy + r))
        return [ax, ay, bx - ax + 1, by - ay + 1]

    def angle(v1, v2):
        n1 = math.sqrt(v1[0] * v1[0] + v1[1] * v1[1])
        n2 = math.sqrt(v2[0] * v2[0] + v2[1] * v2[1])
        if n1 * n2 <= 1e-6:
            return 65535.0
        c = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)
        c = max(-1.0, min(1.0, c))
        return math.acos(c) * 180 / math.pi

    def classify(r):
        a = []
        for i in range(5):
            a.append(angle([r[0] - r[i * 8 + 4], r[1] - r[i * 8 + 5]],
                           [r[i * 8 + 6] - r[i * 8 + 8],
                            r[i * 8 + 7] - r[i * 8 + 9]]))
        ta, tt, ts = 65.0, 53.0, 49.0
        if 65535.0 in a:
            return None, a
        if a[0] > tt and a[1] > ta and a[2] > ta and a[3] > ta and a[4] > ta:
            g = "fist"
        elif a[0] < ts and a[1] < ts and a[2] < ts and a[3] < ts and a[4] < ts:
            g = "five"
        elif a[0] < ts and a[1] < ts and a[2] > ta and a[3] > ta and a[4] > ta:
            g = "gun"
        elif a[0] < ts and a[1] < ts and a[2] > ta and a[3] > ta and a[4] < ts:
            g = "love"
        elif a[0] > 5 and a[1] < ts and a[2] > ta and a[3] > ta and a[4] > ta:
            g = "one"
        elif a[0] < ts and a[1] > ta and a[2] > ta and a[3] > ta and a[4] < ts:
            g = "six"
        elif a[0] > tt and a[1] < ts and a[2] < ts and a[3] < ts and a[4] > ta:
            g = "three"
        elif a[0] < ts and a[1] > ta and a[2] > ta and a[3] > ta and a[4] > ta:
            g = "thumbUp"
        elif a[0] > tt and a[1] < ts and a[2] < ts and a[3] > ta and a[4] > ta:
            g = "yeah"
        else:
            g = None
        return g, a

    hand = None
    for attempt in range(80):
        img = sensor.snapshot(chn=CAM_CHN_ID_1)
        tin = a2d_det.run(img.to_numpy_ref())
        kpu_det.set_input_tensor(0, tin)
        kpu_det.run()
        res = []
        for i in range(kpu_det.outputs_size()):
            ot = kpu_det.get_output_tensor(i)
            try:
                res.append(ot.to_numpy())
            finally:
                ot.release()
        dets = aicube.anchorbasedet_post_process(
            res[0], res[1], res[2], DET_IN, [PREVIEW_W, PREVIEW_H],
            [8, 16, 32], 1, 0.2, 0.5, ANCHORS, False)
        del res, tin
        if dets:
            hand = max(dets, key=lambda d: (d[4] - d[2]) * (d[5] - d[3]))
            print("D attempt={} box={} score={:.2f}".format(
                attempt, [int(v) for v in hand[2:6]], float(hand[1])))
            break
        time.sleep_ms(80)

    if hand is None:
        print("RESULT no hand detected")
        raise SystemExit(0)

    cp = crop_param(hand)
    print("C crop={}".format(cp))
    a2d_kp.crop(cp[0], cp[1], cp[2], cp[3])
    a2d_kp.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
    a2d_kp.build([1, 3, PREVIEW_H, PREVIEW_W], [1, 3, 256, 256])

    img = sensor.snapshot(chn=CAM_CHN_ID_1)
    t0 = time.ticks_ms()
    tin = a2d_kp.run(img.to_numpy_ref())
    kpu_kp.set_input_tensor(0, tin)
    kpu_kp.run()
    outs = []
    for i in range(kpu_kp.outputs_size()):
        ot = kpu_kp.get_output_tensor(i)
        try:
            outs.append(ot.to_numpy())
        finally:
            ot.release()
    kp_ms = time.ticks_diff(time.ticks_ms(), t0)
    print("I kp={}ms outs={}".format(kp_ms, [o.shape for o in outs]))

    r0 = outs[0]
    flat = r0.reshape(r0.shape[0] * r0.shape[1])
    print("I flat_len={} (expect 42 = 21 points)".format(len(flat)))
    pts = np.zeros(flat.shape, dtype=np.int16)
    pts[0::2] = flat[0::2] * cp[2] + cp[0]
    pts[1::2] = flat[1::2] * cp[3] + cp[1]
    p6 = [int(pts[i]) for i in range(6)]
    print("P first6={}".format(p6))

    g, angles = classify(pts)
    ai = [int(x) for x in angles]
    print("G gesture={} angles={}".format(g, ai))

    # warm cost of a second pass on a fresh frame
    img = sensor.snapshot(chn=CAM_CHN_ID_1)
    t0 = time.ticks_ms()
    tin = a2d_kp.run(img.to_numpy_ref())
    kpu_kp.set_input_tensor(0, tin)
    kpu_kp.run()
    for i in range(kpu_kp.outputs_size()):
        kpu_kp.get_output_tensor(i).release()
    print("W warm_kp={}ms".format(time.ticks_diff(time.ticks_ms(), t0)))

    del outs, tin, flat, pts
    print("DONE heap={}".format(gc.mem_free()))
finally:
    if sensor is not None:
        try:
            sensor.stop()
        except BaseException:
            pass
        try:
            sensor.deinit()
        except BaseException:
            pass
    print("FIN")
