# Measures the face-recognition chain: det -> 5 landmarks -> Umeyama -> 112x112
# affine -> face_recognition_mobile.kmodel -> 128-d feature.
#
# Prints load time, free heap, output shape, per-stage cost, and the score
# distribution of one subject across frames. The score definition is the one the
# inference pipeline uses: cos(a, b) / 2 + 0.5, so identical features give 1.0
# and orthogonal features give 0.5.
#
# Run with --lvgl and a face straight at the camera. Every KPU tensor is released
# and both models are dropped in a finally block.

import gc
import math
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("apps.app_face_points", "apps.app_face_det", "apps",
              "apps.error", "ui.beep", "apps.app_face_recognize"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv
import ulab.numpy as np
import nncase_runtime as nn
import aidemo
from libs.AI2D import Ai2d
from media.sensor import Sensor, CAM_CHN_ID_1

DET_MODEL = "/sdcard/examples/kmodel/face_detection_320.kmodel"
REG_MODEL = "/sdcard/examples/kmodel/face_recognition_mobile.kmodel"
ANCHOR = "/sdcard/examples/utils/prior_data_320.bin"
LM_IN = 192
REG_IN = 112
Y_OFF = 160

# The five canonical points the recognition model expects, in a 112x112 face.
UMEYAMA_112 = [
    38.2946, 51.6963,
    73.5318, 51.5014,
    56.0252, 71.7366,
    41.5493, 92.3655,
    70.7299, 92.2041,
]


def svd22(a):
    s = [0.0, 0.0]
    u = [0.0, 0.0, 0.0, 0.0]
    v = [0.0, 0.0, 0.0, 0.0]
    s[0] = (math.sqrt((a[0] - a[3]) ** 2 + (a[1] + a[2]) ** 2)
            + math.sqrt((a[0] + a[3]) ** 2 + (a[1] - a[2]) ** 2)) / 2
    s[1] = abs(s[0] - math.sqrt((a[0] - a[3]) ** 2 + (a[1] + a[2]) ** 2))
    v[2] = math.sin((math.atan2(2 * (a[0] * a[1] + a[2] * a[3]),
                                a[0] ** 2 - a[1] ** 2 + a[2] ** 2 - a[3] ** 2))
                    / 2) if s[0] > s[1] else 0
    v[0] = math.sqrt(1 - v[2] ** 2)
    v[1] = -v[2]
    v[3] = v[0]
    u[0] = -(a[0] * v[0] + a[1] * v[2]) / s[0] if s[0] != 0 else 1
    u[2] = -(a[2] * v[0] + a[3] * v[2]) / s[0] if s[0] != 0 else 0
    u[1] = (a[0] * v[1] + a[1] * v[3]) / s[1] if s[1] != 0 else -u[2]
    u[3] = (a[2] * v[1] + a[3] * v[3]) / s[1] if s[1] != 0 else u[0]
    v[0] = -v[0]
    v[2] = -v[2]
    return u, s, v


def umeyama_112(src):
    """Similarity transform from five observed points to the canonical face."""
    n = 5
    src_mean = [0.0, 0.0]
    dst_mean = [0.0, 0.0]
    for i in range(0, n * 2, 2):
        src_mean[0] += src[i]
        src_mean[1] += src[i + 1]
        dst_mean[0] += UMEYAMA_112[i]
        dst_mean[1] += UMEYAMA_112[i + 1]
    src_mean[0] /= n
    src_mean[1] /= n
    dst_mean[0] /= n
    dst_mean[1] /= n

    src_demean = [[0.0, 0.0] for _ in range(n)]
    dst_demean = [[0.0, 0.0] for _ in range(n)]
    for i in range(n):
        src_demean[i][0] = src[2 * i] - src_mean[0]
        src_demean[i][1] = src[2 * i + 1] - src_mean[1]
        dst_demean[i][0] = UMEYAMA_112[2 * i] - dst_mean[0]
        dst_demean[i][1] = UMEYAMA_112[2 * i + 1] - dst_mean[1]

    a = [[0.0, 0.0], [0.0, 0.0]]
    for i in range(2):
        for k in range(2):
            for j in range(n):
                a[i][k] += dst_demean[j][i] * src_demean[j][k]
            a[i][k] /= n

    t = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    u, s, v = svd22([a[0][0], a[0][1], a[1][0], a[1][1]])
    t[0][0] = u[0] * v[0] + u[1] * v[2]
    t[0][1] = u[0] * v[1] + u[1] * v[3]
    t[1][0] = u[2] * v[0] + u[3] * v[2]
    t[1][1] = u[2] * v[1] + u[3] * v[3]

    var = [0.0, 0.0]
    for i in range(n):
        var[0] += src_demean[i][0] * src_demean[i][0]
        var[1] += src_demean[i][1] * src_demean[i][1]
    var[0] /= n
    var[1] /= n
    scale = 1.0 / (var[0] + var[1]) * (s[0] + s[1])

    t[0][2] = dst_mean[0] - scale * (t[0][0] * src_mean[0]
                                     + t[0][1] * src_mean[1])
    t[1][2] = dst_mean[1] - scale * (t[1][0] * src_mean[0]
                                     + t[1][1] * src_mean[1])
    for i in (0, 1):
        for k in (0, 1):
            t[i][k] *= scale
    return [t[0][0], t[0][1], t[0][2], t[1][0], t[1][1], t[1][2]]


print("R1 models: det={} reg={}".format(
    __import__("os").stat(DET_MODEL)[6], __import__("os").stat(REG_MODEL)[6]))

anchors = np.frombuffer(open(ANCHOR, "rb").read(), dtype=np.float).reshape(
    (4200, 4))
print("R2 anchors {} mem_free={}".format(list(anchors.shape), gc.mem_free()))

sensor = None
kpu_det = None
kpu_reg = None
a2d_det = None
a2d_reg = None
try:
    t0 = time.ticks_ms()
    kpu_det = nn.kpu()
    kpu_det.load_kmodel(DET_MODEL)
    t_det = time.ticks_diff(time.ticks_ms(), t0)
    free_after_det = gc.mem_free()

    t0 = time.ticks_ms()
    kpu_reg = nn.kpu()
    kpu_reg.load_kmodel(REG_MODEL)
    t_reg = time.ticks_diff(time.ticks_ms(), t0)
    print("R3 load det={}ms reg={}ms mem_free: after_det={} after_reg={}".format(
        t_det, t_reg, free_after_det, gc.mem_free()))

    a2d_det = Ai2d()
    a2d_det.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                           np.uint8, np.uint8)
    a2d_det.pad([0, 0, 0, 0, 64, 64, 0, 0], 0, [104, 117, 123])
    a2d_det.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
    a2d_det.build([1, 3, 480, 800], [1, 3, 320, 320])

    a2d_reg = Ai2d()
    a2d_reg.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                           np.uint8, np.uint8)

    sensor = Sensor(fps=30)
    sensor.reset()
    sensor.set_framesize(w=800, h=480, chn=CAM_CHN_ID_1, crop=True)
    sensor.set_pixformat(Sensor.RGBP888, chn=CAM_CHN_ID_1)
    sensor.run()
    print("R4 sensor up mem_free={}".format(gc.mem_free()))

    print("R5 hold still, 4 s ...")
    for s in range(4, 0, -1):
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
        landms = res[1] if len(res) > 1 else None
        best = 0
        for i in range(1, len(boxes)):
            if boxes[i][2] * boxes[i][3] > boxes[best][2] * boxes[best][3]:
                best = i
        b = boxes[best]
        if int(b[2]) <= 0 or int(b[3]) <= 0:
            return None
        lm = landms[best] if landms and best < len(landms) else None
        return (int(b[0]), int(b[1]) - Y_OFF, int(b[2]), int(b[3])), lm

    def feature(img, lm):
        # Landmark y still carries the letterbox pad, so undo it before the
        # similarity transform maps the points into the 112x112 face.
        pts = []
        for i in range(5):
            pts.append(lm[i * 2])
            pts.append(lm[i * 2 + 1] - Y_OFF)
        m = umeyama_112(pts)
        a2d_reg.affine(nn.interp_method.cv2_bilinear, 0, 0, 127, 1, m)
        a2d_reg.build([1, 3, 480, 800], [1, 3, REG_IN, REG_IN])
        tin = a2d_reg.run(img.to_numpy_ref())
        kpu_reg.set_input_tensor(0, tin)
        kpu_reg.run()
        out = None
        for i in range(kpu_reg.outputs_size()):
            ot = kpu_reg.get_output_tensor(i)
            try:
                if out is None:
                    out = ot.to_numpy()
            finally:
                ot.release()
        del tin
        return out, m, pts

    img = sensor.snapshot(chn=CAM_CHN_ID_1)
    got = detect(img)
    if got is None:
        print("R6 no face: nothing in front of the camera")
    else:
        box, lm = got
        print("R6 largest box screen=({},{},{}x{}) lm={}".format(
            box[0], box[1], box[2], box[3], lm is not None))

        t0 = time.ticks_us()
        out, m, pts = feature(img, lm)
        t_first = time.ticks_diff(time.ticks_us(), t0) / 1000.0
        print("R7 first feature {:.2f} ms | outputs={} shape={}".format(
            t_first, 1, list(out.shape) if hasattr(out, "shape") else "?"))
        flat = out.flatten()
        dim = len(flat)
        print("R8 dim={} sample={}".format(
            dim, [round(float(flat[i]), 4) for i in range(min(4, dim))]))
        print("R9 affine={}".format([round(float(v), 4) for v in m]))

        norm = math.sqrt(sum(float(flat[i]) * float(flat[i])
                             for i in range(dim)))
        print("R10 raw norm={:.4f}".format(norm))

        # Collect one feature per frame to see how stable the same subject is.
        feats = []
        feats.append([float(flat[i]) / norm for i in range(dim)])
        times = [t_first]
        frames = 0
        while len(feats) < 12 and frames < 40:
            frames += 1
            img = sensor.snapshot(chn=CAM_CHN_ID_1)
            got = detect(img)
            if got is None:
                continue
            box, lm = got
            if lm is None:
                continue
            t0 = time.ticks_us()
            out, m, pts = feature(img, lm)
            times.append(time.ticks_diff(time.ticks_us(), t0) / 1000.0)
            f = out.flatten()
            n2 = math.sqrt(sum(float(f[i]) * float(f[i]) for i in range(dim)))
            if n2 <= 0:
                continue
            feats.append([float(f[i]) / n2 for i in range(dim)])
        print("R11 collected {} features in {} frames".format(
            len(feats), frames))
        print("R12 feature time min={:.2f} max={:.2f} mean={:.2f} ms".format(
            min(times), max(times), sum(times) / len(times)))

        # All pairs: the genuine score distribution for one subject.
        scores = []
        for i in range(len(feats)):
            for j in range(i + 1, len(feats)):
                dot = 0.0
                for k in range(dim):
                    dot += feats[i][k] * feats[j][k]
                scores.append(dot / 2.0 + 0.5)
        scores.sort()
        n = len(scores)
        if n:
            mean = sum(scores) / n
            var = sum((s - mean) ** 2 for s in scores) / n
            print("R13 genuine pairs={} min={:.4f} p10={:.4f} median={:.4f} "
                  "max={:.4f} mean={:.4f} sd={:.4f}".format(
                      n, scores[0], scores[n // 10], scores[n // 2],
                      scores[-1], mean, math.sqrt(var)))

        # A random orthogonal-ish vector stands in for an impostor feature so the
        # scale of the score can be sanity-checked (0.5 expected).
        rnd = [float((i * 37 % 101) - 50) for i in range(dim)]
        rn = math.sqrt(sum(v * v for v in rnd))
        dot = 0.0
        for k in range(dim):
            dot += feats[0][k] * (rnd[k] / rn)
        print("R14 vs synthetic vector: {:.4f} (expect near 0.5)".format(
            dot / 2.0 + 0.5))

        # How far apart are the five points after alignment? If the transform is
        # right, the mapped eyes sit on the canonical row (y ~ 51.6).
        t = m
        print("R15 mapped eye/mouth rows: "
              "l_eye_y={:.2f} r_eye_y={:.2f} nose_y={:.2f}".format(
                  t[3] * pts[0] + t[4] * pts[1] + t[5],
                  t[3] * pts[2] + t[4] * pts[3] + t[5],
                  t[3] * pts[4] + t[4] * pts[5] + t[5]))
        del out, flat
        gc.collect()
        print("R16 mem_free={}".format(gc.mem_free()))
finally:
    for name, obj, method in (("a2d_reg", a2d_reg, "deinit"),
                              ("a2d_det", a2d_det, "deinit"),
                              ("kpu_reg", kpu_reg, "release"),
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
    print("R17 cleanup done mem_free={}".format(gc.mem_free()))

print("RESULT: DONE")
