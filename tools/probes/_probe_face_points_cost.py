# Phase 1 measurements for the 100-point face landmark app.
#
# Two independent parts, so a failure in one still yields the other:
#   P  - draw cost: ten lv.line objects carrying the landmark point set, one
#        set_points per group per frame, then invalidate + refr_now. Under FULL
#        render every invalidation repaints the screen, so this is the real
#        per-frame cost.
#   M  - model: det -> largest box -> affine 192x192 -> face_landmark.kmodel ->
#        coordinate restore, printed as a bounding box plus the eye centres so
#        they can be compared with the detector's own 5 points.
#
# Run with --lvgl. The point groups follow the standard 106-point layout; the
# ten groups reference 100 points of it, the rest are unused here.

import gc
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("apps.app_face_det", "apps.app_face_points", "apps",
              "apps.error", "ui.beep"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv
import ulab.numpy as np

DET_MODEL = "/sdcard/examples/kmodel/face_detection_320.kmodel"
LM_MODEL = "/sdcard/examples/kmodel/face_landmark.kmodel"
ANCHOR = "/sdcard/examples/utils/prior_data_320.bin"

# (name, indices, closed) -- the drawing order of the app.
GROUPS = [
    ("basin", [1, 9, 10, 11, 12, 13, 14, 15, 16, 2, 3, 4, 5, 6, 7, 8, 0, 24,
               23, 22, 21, 20, 19, 18, 32, 31, 30, 29, 28, 27, 26, 25, 17], True),
    ("lbrow", [43, 44, 45, 47, 46, 50, 51, 49, 48], False),
    ("rbrow", [97, 98, 99, 100, 101, 105, 104, 103, 102], False),
    ("leye", [35, 36, 33, 37, 39, 42, 40, 41], True),
    ("reye", [89, 90, 87, 91, 93, 96, 94, 95], True),
    ("pupil", [34, 88], False),
    ("bridge", [72, 73, 74, 86], False),
    ("wing", [77, 78, 79, 80, 85, 84, 83], False),
    ("outlip", [52, 55, 56, 53, 59, 58, 61, 68, 67, 71, 63, 64], True),
    ("inlip", [65, 54, 60, 57, 69, 70, 62, 66], True),
]
CONTOUR = {"basin", "lbrow", "rbrow", "outlip", "inlip"}
LM_IN = 192
N_POINTS = 106

# ------------------------------------------------------------------ P: draw
print("P1 groups={} points referenced={} model outputs={}".format(
    len(GROUPS), sum(len(g[1]) for g in GROUPS), N_POINTS))

scr = lv.scr_act()
bg = lv.obj(scr)
bg.set_size(800, 480)
bg.align(lv.ALIGN.TOP_LEFT, 0, 0)
bg.set_style_bg_color(lv.color_hex(0x101418), 0)
bg.set_style_bg_opa(lv.OPA.COVER, 0)
bg.set_style_border_width(0, 0)
bg.set_style_pad_all(0, 0)
bg.clear_flag(lv.obj.FLAG.SCROLLABLE)


def mk_line(color, width):
    ln = lv.line(bg)
    ln.set_points([{"x": 0, "y": 0}] * 2, 2)
    ln.set_style_line_width(width, 0)
    ln.set_style_line_color(lv.color_hex(color), 0)
    ln.set_style_line_opa(lv.OPA.COVER, 0)
    try:
        ln.set_style_line_rounded(False, 0)
    except BaseException:
        pass
    return ln


COLORS = [0x00E5A0, 0xFFC857, 0xFFC857, 0x64B5F6, 0x64B5F6, 0xFF6B6B,
          0xFF8A65, 0xFF8A65, 0xE88BA8, 0xC2708A]
lines = [mk_line(COLORS[i], 2) for i in range(len(GROUPS))]


def frame_points(shift):
    """One 800x480 face-shaped point set, shifted for the animation."""
    out = []
    for gi, (name, idx, closed) in enumerate(GROUPS):
        pts = []
        for k, _ in enumerate(idx):
            x = 200 + (k * 61 + gi * 37) % 400 + shift
            y = 120 + (k * 43 + gi * 53) % 240
            pts.append({"x": x, "y": y})
        if closed and len(pts) > 2:
            pts.append({"x": pts[0]["x"], "y": pts[0]["y"]})
        out.append((gi, pts))
    return out


_tick = [0]


def push(active_only=None):
    _tick[0] = (_tick[0] + 1) % 8
    for gi, pts in frame_points(_tick[0]):
        active = active_only is None or GROUPS[gi][0] in active_only
        if active:
            lines[gi].set_points(pts, len(pts))
            lines[gi].clear_flag(lv.obj.FLAG.HIDDEN)
        else:
            lines[gi].add_flag(lv.obj.FLAG.HIDDEN)


def bench(tag, fn, n=10):
    fn()
    lv.refr_now(lv.disp_get_default())
    t0 = time.ticks_us()
    for _ in range(n):
        fn()
        lv.refr_now(lv.disp_get_default())
    ms = time.ticks_diff(time.ticks_us(), t0) / 1000.0 / n
    print("P2 %-28s %6.2f ms/frame  %5.1f Hz" % (tag, ms, 1000.0 / ms if ms else 0))
    return ms


t0 = time.ticks_us()
lv.refr_now(lv.disp_get_default())
print("P2 %-28s %6.2f ms" % (
    "baseline refr_now", time.ticks_diff(time.ticks_us(), t0) / 1000.0))

push()
lv.refr_now(lv.disp_get_default())

bench("All: 10 lines / 110 pts", lambda: push())
bench("Contour: 5 lines / 71 pts", lambda: push(CONTOUR))
for n in (1, 2, 3, 5, 8):
    subset = {GROUPS[i][0] for i in range(n)}
    bench("{} line(s), others hidden".format(n), (lambda s=subset: push(s)))
push()
bench("All, width 1", lambda: [ln.set_style_line_width(1, 0) for ln in lines])
push()
bench("All, width 3", lambda: [ln.set_style_line_width(3, 0) for ln in lines])
for ln in lines:
    ln.set_style_line_width(2, 0)
push()
bench("All, rounded True", lambda: [ln.set_style_line_rounded(True, 0)
                                    for ln in lines])
for ln in lines:
    try:
        ln.set_style_line_rounded(False, 0)
    except BaseException:
        pass

t0 = time.ticks_us()
for _ in range(10):
    push()
print("P3 set_points only (no repaint): %.2f ms/frame"
      % (time.ticks_diff(time.ticks_us(), t0) / 1000.0 / 10))

for ln in lines:
    try:
        ln.delete()
    except BaseException:
        pass
del lines
bg.delete()
gc.collect()
print("P4 after cleanup mem_free={}".format(gc.mem_free()))

# ----------------------------------------------------------------- M: model
import aidemo
import nncase_runtime as nn
from libs.AI2D import Ai2d
from media.sensor import Sensor, CAM_CHN_ID_1
from apps.app_face_det import y_pad_offset

Y_OFF = y_pad_offset()
print("M1 y_off={} (subtract before mapping points into screen space)".format(Y_OFF))

anchors = np.frombuffer(open(ANCHOR, "rb").read(), dtype=np.float).reshape((4200, 4))
print("M2 anchors {}".format(list(anchors.shape)))

sensor = None
kpu_det = None
kpu_lm = None
a2d_det = None
a2d_lm = None
try:
    sensor = Sensor(fps=30)
    sensor.reset()
    sensor.set_framesize(w=800, h=480, chn=CAM_CHN_ID_1, crop=True)
    sensor.set_pixformat(Sensor.RGBP888, chn=CAM_CHN_ID_1)
    sensor.run()

    t0 = time.ticks_ms()
    kpu_det = nn.kpu()
    kpu_det.load_kmodel(DET_MODEL)
    t_det = time.ticks_diff(time.ticks_ms(), t0)
    t0 = time.ticks_ms()
    kpu_lm = nn.kpu()
    kpu_lm.load_kmodel(LM_MODEL)
    t_lm = time.ticks_diff(time.ticks_ms(), t0)
    print("M3 load det={}ms lm={}ms mem_free={}".format(t_det, t_lm, gc.mem_free()))

    a2d_det = Ai2d()
    a2d_det.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                           np.uint8, np.uint8)
    a2d_det.pad([0, 0, 0, 0, 64, 64, 0, 0], 0, [104, 117, 123])
    a2d_det.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
    a2d_det.build([1, 3, 480, 800], [1, 3, 320, 320])

    a2d_lm = Ai2d()
    a2d_lm.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                          np.uint8, np.uint8)

    print("M4 hold still, a face in front of the camera, 5 s ...")
    for s in range(5, 0, -1):
        print("   ", s)
        time.sleep_ms(1000)

    img = sensor.snapshot(chn=CAM_CHN_ID_1)

    t0 = time.ticks_us()
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
    det = aidemo.face_det_post_process(
        0.5, 0.2, 320, anchors, [800, 480 + Y_OFF], outs)
    t_detrun = time.ticks_diff(time.ticks_us(), t0) / 1000.0
    del outs, tin
    print("M5 det {:.2f} ms | boxes={}".format(
        t_detrun, 0 if len(det) == 0 else len(det[0])))

    if len(det) == 0 or not det[0]:
        print("M6 no face: hold a face straight at the camera and rerun")
    else:
        boxes = det[0]
        best = 0
        for i in range(1, len(boxes)):
            if boxes[i][2] * boxes[i][3] > boxes[best][2] * boxes[best][3]:
                best = i
        bx, by, bw, bh = (int(boxes[best][0]), int(boxes[best][1]) - Y_OFF,
                          int(boxes[best][2]), int(boxes[best][3]))
        print("M6 largest box screen=({},{},{}x{})".format(bx, by, bw, bh))

        # affine: centre the box with a 1.5x margin into the 192 model square
        scale = float(LM_IN) / (max(bw, bh) * 1.5)
        cx = (bx + bw / 2.0) * scale
        cy = (by + bh / 2.0) * scale
        half = LM_IN / 2.0
        m = np.zeros((2, 3), dtype=np.float)
        m[0, 0] = scale
        m[0, 2] = half - cx
        m[1, 1] = scale
        m[1, 2] = half - cy
        print("M7 affine scale={:.4f} tx={:.1f} ty={:.1f}".format(
            scale, half - cx, half - cy))

        t0 = time.ticks_us()
        a2d_lm.affine(nn.interp_method.cv2_bilinear, 0, 0, 127, 1,
                      [m[0, 0], m[0, 1], m[0, 2],
                       m[1, 0], m[1, 1], m[1, 2]])
        a2d_lm.build([1, 3, 480, 800], [1, 3, LM_IN, LM_IN])
        a2d_lm.run(img.to_numpy_ref())
        print("M8 affine+build+run (first) {:.2f} ms".format(
            time.ticks_diff(time.ticks_us(), t0) / 1000.0))

        t0 = time.ticks_us()
        tin2 = a2d_lm.run(img.to_numpy_ref())
        kpu_lm.set_input_tensor(0, tin2)
        kpu_lm.run()
        lm_outs = []
        for i in range(kpu_lm.outputs_size()):
            ot = kpu_lm.get_output_tensor(i)
            try:
                lm_outs.append(ot.to_numpy())
            finally:
                ot.release()
        t_lmrun = time.ticks_diff(time.ticks_us(), t0) / 1000.0
        print("M9 lm run {:.2f} ms | outputs={} shapes={}".format(
            t_lmrun, len(lm_outs), [list(o.shape) for o in lm_outs]))

        pred = lm_outs[0].flatten()
        print("M10 raw len={} first4={}".format(
            len(pred), [round(float(pred[i]), 3) for i in range(min(4, len(pred)))]))
        # (p + 1) * half maps the normalised output into model pixels
        for i in range(len(pred)):
            pred[i] = (pred[i] + 1.0) * half

        inv = aidemo.invert_affine_transform(m).flatten()
        n_kp = len(pred) // 2
        xs = []
        ys = []
        for k in range(n_kp):
            ox = pred[k * 2]
            oy = pred[k * 2 + 1]
            nx = ox * inv[0] + oy * inv[1] + inv[2]
            ny = ox * inv[3] + oy * inv[4] + inv[5]
            pred[k * 2] = nx
            pred[k * 2 + 1] = ny
            xs.append(float(nx))
            ys.append(float(ny))
        print("M11 kp={} x=[{:.0f},{:.0f}] y=[{:.0f},{:.0f}]".format(
            n_kp, min(xs), max(xs), min(ys), max(ys)))
        inside = 0
        for k in range(n_kp):
            if 0 <= xs[k] <= 800 and 0 <= ys[k] <= 480:
                inside += 1
        print("M12 inside screen {}/{}".format(inside, n_kp))

        for name, idx, closed in GROUPS:
            gx = sum(xs[i] for i in idx) / len(idx)
            gy = sum(ys[i] for i in idx) / len(idx)
            print("M13 {:<7s} centre=({:.0f},{:.0f})".format(name, gx, gy))
        bx_c = bx + bw / 2.0
        by_c = by + bh / 2.0
        print("M13 box centre=({:.0f},{:.0f})".format(bx_c, by_c))

        if len(det) > 1 and det[1]:
            lp = det[1][best]
            print("M14 det 5-pt eye/nose/mouth: ({:.0f},{:.0f}) ({:.0f},{:.0f}) "
                  "({:.0f},{:.0f}) ({:.0f},{:.0f}) ({:.0f},{:.0f})".format(
                      lp[0], lp[1] - Y_OFF, lp[2], lp[3] - Y_OFF,
                      lp[4], lp[5] - Y_OFF, lp[6], lp[7] - Y_OFF,
                      lp[8], lp[9] - Y_OFF))

        # second pass: is a per-frame affine+build affordable?
        t0 = time.ticks_us()
        for _ in range(5):
            a2d_lm.affine(nn.interp_method.cv2_bilinear, 0, 0, 127, 1,
                          [m[0, 0], m[0, 1], m[0, 2],
                           m[1, 0], m[1, 1], m[1, 2]])
            a2d_lm.build([1, 3, 480, 800], [1, 3, LM_IN, LM_IN])
        print("M15 affine+build only: {:.2f} ms".format(
            time.ticks_diff(time.ticks_us(), t0) / 1000.0 / 5))

        del lm_outs, pred, tin2
        gc.collect()
        print("M16 mem_free={}".format(gc.mem_free()))
finally:
    for name, obj, method in (("a2d_lm", a2d_lm, "deinit"),
                              ("a2d_det", a2d_det, "deinit"),
                              ("kpu_lm", kpu_lm, "release"),
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
    print("M17 cleanup done mem_free={}".format(gc.mem_free()))

print("RESULT: DONE")
