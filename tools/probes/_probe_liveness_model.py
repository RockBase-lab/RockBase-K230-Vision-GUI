# Measures the liveness chain: det -> largest box -> letterboxed crop to 112x112 ->
# face_liveness_rgb.kmodel -> one score.
#
# Prints load time, free heap, output shape and the score per frame, so the score
# scale and a usable threshold come from measurement rather than the reference
# constant. Run with --lvgl and a face in front of the camera; repeat with a photo
# of a face held to the camera to see the other class.
#
# Every KPU tensor is released and every model/Ai2d is dropped in the finally block.

import gc
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("core", "apps.app_face_points", "apps.app_face_det", "apps",
              "core.errors", "ui.beep", "apps.app_face_liveness"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv
import ulab.numpy as np
import nncase_runtime as nn
import aidemo
from libs.AI2D import Ai2d
from media.sensor import Sensor, CAM_CHN_ID_1

DET_MODEL = "/sdcard/examples/kmodel/face_detection_320.kmodel"
LIVE_MODEL = "/sdcard/examples/kmodel/face_liveness_rgb.kmodel"
ANCHOR = "/sdcard/examples/utils/prior_data_320.bin"
LIVE_IN = 112
Y_OFF = 160
N_FRAMES = 24


def letterbox_pad_param(src_w, src_h, dst_w, dst_h):
    """Centre a src rectangle inside dst with equal padding, keeping the aspect."""
    ratio = min(float(dst_w) / src_w, float(dst_h) / src_h)
    new_w = int(src_w * ratio)
    new_h = int(src_h * ratio)
    pad_w = (dst_w - new_w) // 2
    pad_h = (dst_h - new_h) // 2
    return pad_h, dst_h - new_h - pad_h, pad_w, dst_w - new_w - pad_w, ratio


anchors = np.frombuffer(open(ANCHOR, "rb").read(), dtype=np.float).reshape(
    (4200, 4))

sensor = None
kpu_det = None
kpu_live = None
a2d_det = None
a2d_live = None
print("L1 start mem_free={}".format(gc.mem_free()))
try:
    t0 = time.ticks_ms()
    kpu_det = nn.kpu()
    kpu_det.load_kmodel(DET_MODEL)
    t_det = time.ticks_diff(time.ticks_ms(), t0)

    t0 = time.ticks_ms()
    kpu_live = nn.kpu()
    kpu_live.load_kmodel(LIVE_MODEL)
    print("L2 load det={}ms live={}ms mem_free={}".format(
        t_det, time.ticks_diff(time.ticks_ms(), t0), gc.mem_free()))

    a2d_det = Ai2d()
    a2d_det.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                           np.uint8, np.uint8)
    a2d_det.pad([0, 0, 0, 0, 64, 64, 0, 0], 0, [104, 117, 123])
    a2d_det.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
    a2d_det.build([1, 3, 480, 800], [1, 3, 320, 320])

    a2d_live = Ai2d()
    a2d_live.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                            np.uint8, np.uint8)

    sensor = Sensor(fps=30)
    sensor.reset()
    sensor.set_framesize(w=800, h=480, chn=CAM_CHN_ID_1, crop=True)
    sensor.set_pixformat(Sensor.RGBP888, chn=CAM_CHN_ID_1)
    sensor.run()
    print("L3 sensor up mem_free={}".format(gc.mem_free()))

    print("L4 hold still, 3 s ...")
    for s in range(3, 0, -1):
        print("   ", s)
        time.sleep_ms(1000)

    def detect(img):
        tin = a2d_det.run(img.to_numpy_ref())
        kpu_det.set_input_tensor(0, tin)
        kpu_det.run()
        outs = []
        for i in range(kpu_det.outputs_size()):
            ot = kpu_det.get_output_tensor(i)
            try:
                outs.append(ot.to_numpy())
            finally:
                ot.release()
        try:
            res = aidemo.face_det_post_process(0.5, 0.2, 320, anchors,
                                               [800, 480 + Y_OFF], outs)
        finally:
            del outs, tin
        if len(res) == 0 or not res[0]:
            return None
        boxes = res[0]
        best = 0
        for i in range(1, len(boxes)):
            if boxes[i][2] * boxes[i][3] > boxes[best][2] * boxes[best][3]:
                best = i
        b = boxes[best]
        return (int(b[0]), int(b[1]) - Y_OFF, int(b[2]), int(b[3]))

    def liveness(img, box, pad_w=0, pad_h=0):
        """Score one face, optionally with the crop expanded by pad_w / pad_h.

        Both modes are measurable in one run because the expansion changes the score
        a lot: the box-only crop leaves no context for the model to judge.
        """
        x, y, w, h = box
        cw = w + pad_w
        ch = h + pad_h
        cx = x - (cw - w) // 2
        cy = y - (ch - h) // 2
        if cx < 0:
            cw += cx
            cx = 0
        if cy < 0:
            ch += cy
            cy = 0
        if cx + cw > 800:
            cw = 800 - cx
        if cy + ch > 480:
            ch = 480 - cy
        if cw <= 1 or ch <= 1:
            return None, None
        top, bottom, left, right, scale = letterbox_pad_param(cw, ch, LIVE_IN,
                                                              LIVE_IN)
        # Same op order as the app: crop, resize, pad.
        a2d_live.crop(cx, cy, cw, ch)
        a2d_live.resize(nn.interp_method.tf_bilinear,
                        nn.interp_mode.half_pixel)
        a2d_live.pad([0, 0, 0, 0, top, bottom, left, right], 0, [128, 128, 128])
        a2d_live.build([1, 3, 480, 800], [1, 3, LIVE_IN, LIVE_IN])
        tin = a2d_live.run(img.to_numpy_ref())
        kpu_live.set_input_tensor(0, tin)
        t0 = time.ticks_us()
        kpu_live.run()
        outs = []
        for i in range(kpu_live.outputs_size()):
            ot = kpu_live.get_output_tensor(i)
            try:
                outs.append(ot.to_numpy())
            finally:
                ot.release()
        ms = time.ticks_diff(time.ticks_us(), t0) / 1000.0
        del tin
        return outs, ms

    img = sensor.snapshot(chn=CAM_CHN_ID_1)
    box = detect(img)
    if box is None:
        print("L5 no face detected: nothing to measure")
    else:
        print("L5 largest box ({},{},{}x{})".format(box[0], box[1], box[2], box[3]))
        outs, ms = liveness(img, box)
        print("L6 outputs={} shapes={} run={:.2f} ms".format(
            len(outs), [list(o.shape) for o in outs], ms))
        for i, o in enumerate(outs):
            flat = o.flatten()
            print("L7 out[{}] len={} values={}".format(
                i, len(flat),
                [round(float(flat[k]), 5) for k in range(min(6, len(flat)))]))
        print("L7a both values sum to {:.4f} -> a two-class softmax".format(
            sum(float(outs[0].flatten()[k]) for k in range(2))))

        # Collect live-probability (index 0) under both crop modes.
        #
        # Only fully visible faces are scored: a box cut off by the frame edge gives
        # a truncated crop, and the model read those as spoof. The skip count is
        # printed so a run with nothing valid is obvious instead of looking like a
        # real measurement.
        for mode, pw, ph in (("box only", 0, 0), ("expanded", 224, 192)):
            scores = []
            times = []
            frames = 0
            clipped = 0
            while len(scores) < N_FRAMES and frames < N_FRAMES * 6:
                frames += 1
                img = sensor.snapshot(chn=CAM_CHN_ID_1)
                box = detect(img)
                if box is None:
                    continue
                bx, by, bw, bh = box
                if bx < 8 or by < 8 or bx + bw > 792 or by + bh > 472:
                    clipped += 1
                    continue
                outs, ms = liveness(img, box, pw, ph)
                if not outs:
                    continue
                times.append(ms)
                flat = outs[0].flatten()
                scores.append(float(flat[0]))
            if not scores:
                print("L8 {}: no usable frame ({} clipped)".format(mode, clipped))
                continue
            scores.sort()
            n = len(scores)
            print("L8 {:<9} n={} clipped={} min={:.4f} p25={:.4f} "
                  "median={:.4f} p75={:.4f} max={:.4f}".format(
                      mode, n, clipped, scores[0], scores[n // 4],
                      scores[n // 2], scores[3 * n // 4], scores[-1]))
            print("L9 {:<9} raw={}".format(
                mode, " ".join("{:.3f}".format(s) for s in scores)))
            print("L10 {:<9} run min={:.2f} mean={:.2f} max={:.2f} ms".format(
                mode, min(times), sum(times) / len(times), max(times)))
        gc.collect()
        print("L11 mem_free={}".format(gc.mem_free()))
finally:
    for name, obj, method in (("a2d_live", a2d_live, "deinit"),
                              ("a2d_det", a2d_det, "deinit"),
                              ("kpu_live", kpu_live, "release"),
                              ("kpu_det", kpu_det, "release")):
        if obj is not None:
            try:
                getattr(obj, method)()
            except BaseException as e:
                print("CLEAN {} {} error: {}".format(name, method, e))
    if sensor is not None:
        for method in ("stop", "deinit"):
            try:
                getattr(sensor, method)()
            except BaseException as e:
                print("CLEAN sensor.{} error: {}".format(method, e))
    gc.collect()
    print("L12 cleanup done mem_free={}".format(gc.mem_free()))

print("RESULT: DONE")
