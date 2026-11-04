# Phase-0 facts a human-pose app needs before its layout is designed.
#
# Independent stages, so a failure in one still yields the rest:
#   P1  which pose kmodels the firmware image actually carries
#   P2  which aidemo post-processing spellings exist
#   P3  per-model: load time, output count, output shape
#   P4  full chain on the preferred model: snapshot / ai2d / kpu / post cost
#   P5  post-process result layout and which parameter order works
#   P6  17 keypoints of the largest person, plus the box-to-nose offset that
#       reveals whether y still carries the letterbox top pad
#   P7  skeleton draw cost: 6 polylines plus 17 joint dots, invalidate + refr
#
# Run with --lvgl and stand one person in front of the camera, full body if
# possible. Every KPU tensor is released in a finally block and the sensor is
# stopped in a finally block too, so the probe always terminates by itself.
#
# Coordinates: the AI channel is 800x480 RGBP888 letterboxed into 320x320, so
# scale = 0.4 and pad_top = 64 in output space. aidemo's y has historically not
# subtracted that pad (measured 160 px low on the detection models), which is why
# P6 prints the box and nose positions side by side instead of assuming either.

import gc
import os
import sys
import time

import lvgl as lv
import ulab.numpy as np
import nncase_runtime as nn
import aidemo
from libs.AI2D import Ai2d
from media.sensor import Sensor, CAM_CHN_ID_1, CAM_CHN_ID_2

# The app shares one explicit crop window between both channels; use the same
# helper so the timings and the coordinate domain match production.
sys.path.insert(0, "/sdcard/factory/gui")
try:
    from hardware.camera_fov import set_preview
except BaseException as _error:
    print("P0 camera_fov unavailable ({}), using crop=True".format(_error))
    set_preview = None

PREVIEW_W = 800
PREVIEW_H = 480
MODEL_IN = [320, 320]
MODEL_DIR = "/sdcard/examples/kmodel"
CANDIDATES = [
    "yolov8n-pose.kmodel",       # first choice: person_kp_postprocess is documented
    "yolo11n-pose.kmodel",
    "yolo26n-pose.kmodel",       # different post-process signature
]
CONF = 0.35
NMS = 0.45
KP_NUM = 17
KP_DIM = 3
FRAMES = 10

# COCO-17 edge list in 0-based array indices, grouped into chains so the app can
# draw six polylines instead of sixteen line objects. Cost in this binding tracks
# object count, not point count.
CHAINS = [
    ("arm_l", [5, 7, 9], 0x7FB6D9),
    ("arm_r", [6, 8, 10], 0xCC8A92),
    ("leg_l", [11, 13, 15], 0x7FB6D9),
    ("leg_r", [12, 14, 16], 0xCC8A92),
    ("torso", [5, 6, 12, 11, 5], 0xE6EAF0),
    ("head", [3, 1, 0, 2, 4], 0xC9AE7A),
]
KP_NAMES = ["nose", "eye_l", "eye_r", "ear_l", "ear_r",
            "sho_l", "sho_r", "elb_l", "elb_r", "wri_l", "wri_r",
            "hip_l", "hip_r", "kne_l", "kne_r", "ank_l", "ank_r"]


def pad_params():
    """Letterbox from 800x480 to a square input: (scale, pad_top)."""
    mw, mh = MODEL_IN
    scale = min(float(mw) / PREVIEW_W, float(mh) / PREVIEW_H)
    return scale, int((mh - int(PREVIEW_H * scale)) / 2)


SCALE, PAD_TOP = pad_params()
Y_OFF = int(PAD_TOP * PREVIEW_W / MODEL_IN[0])
print("P0 preview={}x{} model={}x{} scale={} pad_top={} y_off={}".format(
    PREVIEW_W, PREVIEW_H, MODEL_IN[0], MODEL_IN[1], SCALE, PAD_TOP, Y_OFF))


def seq_len(obj):
    """len() of a sequence, or -1 when obj is a scalar.

    hasattr(obj, "__len__") is not usable here: ulab ndarrays support len()
    without exposing __len__ as an attribute, so hasattr reports False and every
    array was mistaken for a scalar (measured on board).
    """
    try:
        return len(obj)
    except BaseException:
        return -1


def extract_pose(res):
    """Return (boxes, person) from an aidemo pose result, layout-agnostic.

    person_kp_postprocess returns [boxes, kps, scores]:
      boxes (N,4) x,y,w,h int16;  kps (N,17,3) x,y,score;  scores (N,)
    """
    boxes = None
    person = None
    n = seq_len(res)
    if n < 0:
        return None, None
    for i in range(n):
        el = res[i]
        ln = seq_len(el)
        if ln <= 0:
            continue
        first = el[0]
        flen = seq_len(first)
        if boxes is None and flen == 4:
            boxes = el
            continue
        if person is None and flen >= KP_NUM:
            person = first                 # -> the first person, (17,3)
    return boxes, person


def kp_at(person, k):
    """Return (x, y, score) of keypoint k, per-point or flat layout."""
    if seq_len(person[0]) >= 0:
        row = person[k]
        return (row[0], row[1], row[2])
    return (person[k * KP_DIM], person[k * KP_DIM + 1],
            person[k * KP_DIM + 2])


def kp_count(person):
    n = seq_len(person)
    if n > 0 and seq_len(person[0]) < 0:
        return n // KP_DIM
    return n


def sanity(person):
    """Fraction of keypoints inside the screen.

    The rgb-size parameter is ambiguous for the pose post-process (the square demo
    input cannot distinguish [H,W] from [W,H]). A swapped pair rescales the points
    and pushes most of them off screen, so this fraction separates the two without
    needing a visual check.
    """
    if person is None:
        return 0.0
    total = 0
    inside = 0
    for k in range(min(kp_count(person), KP_NUM)):
        try:
            x, y, _s = kp_at(person, k)
        except BaseException:
            continue
        total += 1
        if 0 <= x <= PREVIEW_W and 0 <= y <= PREVIEW_H:
            inside += 1
    return inside / total if total else 0.0

# ---------------------------------------------------------------- P1 assets
present = []
try:
    names = os.listdir(MODEL_DIR)
except BaseException as error:
    print("P1 listdir failed ({}), checking known names".format(error))
    names = []
for name in names:
    low = name.lower()
    if "pose" in low or "keypoint" in low:
        path = "{}/{}".format(MODEL_DIR, name)
        try:
            print("P1 pose asset {} bytes {}".format(os.stat(path)[6], path))
        except BaseException:
            print("P1 pose asset ? {}".format(path))
for name in CANDIDATES:
    path = "{}/{}".format(MODEL_DIR, name)
    try:
        size = os.stat(path)[6]
        present.append((name, path, size))
        print("P1 candidate YES {} bytes {}".format(size, path))
    except BaseException:
        print("P1 candidate no  {}".format(path))

# ------------------------------------------------------------- P2 aidemo syms
SYMS = ["person_kp_postprocess", "person_kp_post_process",
        "yolo26_pose_postprocess", "yolo11_pose_postprocess",
        "yolov8_pose_postprocess", "pose_postprocess"]
for sym in SYMS:
    print("P2 sym {} {}".format("yes" if hasattr(aidemo, sym) else "no", sym))

# ------------------------------------------------- P3 output layout per model
for name, path, _size in present:
    kpu = None
    try:
        t0 = time.ticks_ms()
        kpu = nn.kpu()
        kpu.load_kmodel(path)
        load_ms = time.ticks_diff(time.ticks_ms(), t0)
        n_out = kpu.outputs_size()
        desc = "?"
        getter = getattr(kpu, "get_output_shapes", None)
        if getter is not None:
            try:
                desc = str(getter())
            except BaseException as error:
                desc = "get_output_shapes failed: {}".format(error)
        print("P3 {} load={}ms outputs={} shapes={} mem_free={}".format(
            name, load_ms, n_out, desc, gc.mem_free()))
    except BaseException as error:
        print("P3 {} load failed: {}".format(name, error))
    finally:
        if kpu is not None:
            try:
                kpu.release()
            except BaseException as error:
                print("P3 {} release failed: {}".format(name, error))
        gc.collect()

# ------------------------------------------------------------- P4 full chain
sensor = None
kpu = None
ai2d = None
raw = None
result = None
try:
    if not present:
        print("P4 skipped: no pose kmodel on board")
    else:
        chosen = present[0]
        print("P4 chosen model {} ({} bytes)".format(chosen[0], chosen[2]))

        ai2d = Ai2d()
        ai2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                            np.uint8, np.uint8)
        ai2d.pad([0, 0, 0, 0, PAD_TOP, PAD_TOP, 0, 0], 0, [128, 128, 128])
        ai2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
        ai2d.build([1, 3, PREVIEW_H, PREVIEW_W], [1, 3, MODEL_IN[1], MODEL_IN[0]])

        kpu = nn.kpu()
        t0 = time.ticks_ms()
        kpu.load_kmodel(chosen[1])
        print("P4 load {}ms mem_free={}".format(
            time.ticks_diff(time.ticks_ms(), t0), gc.mem_free()))

        sensor = Sensor(fps=30)
        sensor.reset()
        if set_preview is not None:
            set_preview(sensor, CAM_CHN_ID_1, PREVIEW_W, PREVIEW_H)
            set_preview(sensor, CAM_CHN_ID_2, PREVIEW_W, PREVIEW_H)
        else:
            sensor.set_framesize(w=PREVIEW_W, h=PREVIEW_H, chn=CAM_CHN_ID_1,
                                 crop=True)
            sensor.set_framesize(w=PREVIEW_W, h=PREVIEW_H, chn=CAM_CHN_ID_2,
                                 crop=True)
        sensor.set_pixformat(Sensor.RGBP888, chn=CAM_CHN_ID_1)
        sensor.set_pixformat(Sensor.RGB888, chn=CAM_CHN_ID_2)
        sensor.run()
        print("P4 sensor up mem_free={}".format(gc.mem_free()))

        print("P4 hold still, 3 s ...")
        for s in range(3, 0, -1):
            print("   ", s)
            time.sleep_ms(1000)

        def infer(img):
            """One frame: ai2d -> kpu -> release -> numpy. Returns (raw, times)."""
            t_ai = time.ticks_us()
            tin = ai2d.run(img.to_numpy_ref())
            t_ai = time.ticks_diff(time.ticks_us(), t_ai)

            t_kp = time.ticks_us()
            kpu.set_input_tensor(0, tin)
            kpu.run()
            t_kp = time.ticks_diff(time.ticks_us(), t_kp)

            t_out = time.ticks_us()
            outs = []
            for i in range(kpu.outputs_size()):
                ot = kpu.get_output_tensor(i)
                try:
                    outs.append(ot.to_numpy())
                finally:
                    ot.release()          # a leaked tensor crashes the board
            t_out = time.ticks_diff(time.ticks_us(), t_out)
            del tin
            return outs, (t_ai / 1000.0, t_kp / 1000.0, t_out / 1000.0)

        sums = [0.0, 0.0, 0.0, 0.0]
        shapes = None
        ok = 0
        for i in range(FRAMES):
            t_snap = time.ticks_us()
            ai_img = sensor.snapshot(chn=CAM_CHN_ID_1)
            t_snap = time.ticks_diff(time.ticks_us(), t_snap) / 1000.0
            outs, stage = infer(ai_img)
            if shapes is None:
                shapes = [list(o.shape) if hasattr(o, "shape") else "?" for o in outs]
            sums[0] += t_snap
            sums[1] += stage[0]
            sums[2] += stage[1]
            sums[3] += stage[2]
            if raw is None:
                raw = outs
            else:
                del outs
            ok += 1
            gc.collect()

        print("P4 outputs={} shape={}".format(len(raw), shapes))
        print("P4 avg ms snap={:.2f} ai2d={:.2f} kpu={:.2f} to_numpy={:.2f} "
              "infer={:.2f} frames={}".format(
                  sums[0] / ok, sums[1] / ok, sums[2] / ok, sums[3] / ok,
                  (sums[1] + sums[2] + sums[3]) / ok, ok))

        # separate channel cost: the app needs a packed RGB888 preview frame
        t0 = time.ticks_us()
        for _ in range(5):
            disp_img = sensor.snapshot(chn=CAM_CHN_ID_2)
        print("P4 avg ms preview snapshot={:.2f}".format(
            time.ticks_diff(time.ticks_us(), t0) / 5000.0))
        del disp_img
        gc.collect()

        # ----------------------------------------------- P5 result layout
        post_name = None
        for sym in ("person_kp_postprocess", "person_kp_post_process"):
            if hasattr(aidemo, sym):
                post_name = sym
                break
        print("P5 post symbol={}".format(post_name))

        best = None
        best_score = -1.0
        if post_name is not None:
            fn = getattr(aidemo, post_name)
            variants = [
                ("raw a(H,W)(m) conf nms",
                 lambda f=fn: f(raw[0], [PREVIEW_H, PREVIEW_W], MODEL_IN, CONF, NMS)),
                ("raw a(W,H)(m) conf nms",
                 lambda f=fn: f(raw[0], [PREVIEW_W, PREVIEW_H], MODEL_IN, CONF, NMS)),
                ("raw a(H,W)(m)(d) conf nms",
                 lambda f=fn: f(raw[0], [PREVIEW_H, PREVIEW_W], MODEL_IN,
                                [PREVIEW_H, PREVIEW_W], CONF, NMS)),
                ("transposed a(H,W)(m) conf nms",
                 lambda f=fn: f(raw[0].transpose().copy(),
                                [PREVIEW_H, PREVIEW_W], MODEL_IN, CONF, NMS)),
            ]
            for tag, call in variants:
                try:
                    res = call()
                    boxes, person = extract_pose(res)
                    score = sanity(person)
                    print("P5 OK   {} in_screen={:.2f}".format(tag, score))
                    if score > best_score:
                        best_score = score
                        best = res
                except BaseException as error:
                    print("P5 FAIL {} -> {}".format(tag, error))

        if hasattr(aidemo, "yolo26_pose_postprocess"):
            try:
                res = aidemo.yolo26_pose_postprocess(
                    raw[0], [PREVIEW_H, PREVIEW_W], MODEL_IN,
                    [PREVIEW_H, PREVIEW_W], 1, KP_NUM, KP_DIM, CONF, 30)
                boxes, person = extract_pose(res)
                score = sanity(person)
                print("P5 OK   yolo26_pose_postprocess in_screen={:.2f}".format(score))
                if score > best_score:
                    best_score = score
                    best = res
            except BaseException as error:
                print("P5 FAIL yolo26_pose_postprocess -> {}".format(error))

        result = best
        if result is None:
            print("P5 no post-process variant worked")
        else:
            try:
                n = len(result)
            except BaseException:
                n = -1
            print("P5 chosen in_screen={:.2f} len={}".format(best_score, n))
            for i in range(n if n > 0 else 0):
                el = result[i]
                try:
                    ln = len(el)
                except BaseException:
                    ln = -1
                head = "?"
                try:
                    head = str(el[0])[:110]
                except BaseException:
                    pass
                print("P5   [{}] len={} head={}".format(i, ln, head))

            # ---------------------------------------- P6 keypoint anatomy
            boxes, person = extract_pose(result)
            if person is None or kp_count(person) == 0:
                print("P6 no person in view")
            else:
                count = kp_count(person)
                print("P6 entries={} count={} box={}".format(
                    len(person), count,
                    [round(v, 1) for v in boxes[0]] if boxes else "?"))
                for k in range(min(count, KP_NUM)):
                    try:
                        x, y, s = kp_at(person, k)
                        print("P6 kp {:2d} {:<6s} x={:6.1f} y={:6.1f} s={:.3f}".format(
                            k, KP_NAMES[k] if k < len(KP_NAMES) else "?", x, y, s))
                    except BaseException as error:
                        print("P6 kp {:2d} unreadable {}".format(k, error))
                if boxes:
                    by = boxes[0][1]
                    bh = boxes[0][3]
                    nose_y = kp_at(person, 0)[1]
                    print("P6 box y={:.1f} h={:.1f} nose_y={:.1f} delta={:.1f} "
                          "(delta near 0.1*h = no pad, delta near "
                          "0.1*h + {} = pad still in y)".format(
                              by, bh, nose_y, nose_y - by, Y_OFF))
finally:
    if sensor is not None:
        for method in ("stop", "deinit"):
            try:
                getattr(sensor, method)()
            except BaseException as error:
                print("P4 sensor.{} failed: {}".format(method, error))
    if ai2d is not None:
        try:
            ai2d.deinit()
        except BaseException as error:
            print("P4 ai2d.deinit failed: {}".format(error))
    if kpu is not None:
        try:
            kpu.release()
        except BaseException as error:
            print("P4 kpu.release failed: {}".format(error))
    gc.collect()
    print("P4 released mem_free={}".format(gc.mem_free()))

# ------------------------------------------------------------- P7 draw cost
# Synthetic standing pose, in screen coordinates, so the numbers are comparable
# with docs/performance-budget.md. Six polylines plus seventeen dots is the
# layout the app plans to use.
PX = [400, 385, 415, 372, 428, 380, 420, 355, 445, 340, 460,
      385, 415, 380, 420, 378, 422]
PY = [110, 100, 100, 108, 108, 175, 175, 240, 240, 300, 300,
      285, 285, 370, 370, 455, 455]

bg = lv.obj(lv.scr_act())
bg.set_size(PREVIEW_W, PREVIEW_H)
bg.align(lv.ALIGN.TOP_LEFT, 0, 0)
bg.set_style_bg_color(lv.color_hex(0x101418), 0)
bg.set_style_bg_opa(lv.OPA.COVER, 0)
bg.set_style_border_width(0, 0)
bg.set_style_pad_all(0, 0)
bg.clear_flag(lv.obj.FLAG.SCROLLABLE)

lines = []
for _name, _idx, color in CHAINS:
    ln = lv.line(bg)
    ln.set_points([{"x": 0, "y": 0}] * 2, 2)
    ln.set_style_line_width(3, 0)
    ln.set_style_line_color(lv.color_hex(color), 0)
    ln.set_style_line_opa(lv.OPA.COVER, 0)
    try:
        ln.set_style_line_rounded(True, 0)
    except BaseException:
        pass
    ln.add_flag(lv.obj.FLAG.HIDDEN)
    lines.append(ln)

dots = []
for _ in range(KP_NUM):
    dot = lv.obj(bg)
    dot.set_size(8, 8)
    dot.set_style_radius(4, 0)
    dot.set_style_bg_color(lv.color_hex(0x5CE8C0), 0)
    dot.set_style_bg_opa(lv.OPA.COVER, 0)
    dot.set_style_border_width(0, 0)
    dot.set_style_pad_all(0, 0)
    dot.clear_flag(lv.obj.FLAG.SCROLLABLE)
    dot.clear_flag(lv.obj.FLAG.CLICKABLE)
    dot.add_flag(lv.obj.FLAG.HIDDEN)
    dots.append(dot)


def paint(ndots, nlines):
    for i in range(len(lines)):
        if i < nlines:
            ln = lines[i]
            pts = [{"x": PX[k], "y": PY[k]} for k in CHAINS[i][1]]
            ln.set_points(pts, len(pts))
            ln.clear_flag(lv.obj.FLAG.HIDDEN)
        else:
            lines[i].add_flag(lv.obj.FLAG.HIDDEN)
    for i in range(len(dots)):
        if i < ndots:
            dots[i].set_pos(PX[i] - 4, PY[i] - 4)
            dots[i].clear_flag(lv.obj.FLAG.HIDDEN)
        else:
            dots[i].add_flag(lv.obj.FLAG.HIDDEN)


def bench(tag, fn, n=10):
    fn()
    lv.refr_now(lv.disp_get_default())
    t0 = time.ticks_us()
    for _ in range(n):
        fn()
        lv.refr_now(lv.disp_get_default())
    ms = time.ticks_diff(time.ticks_us(), t0) / 1000.0 / n
    print("P7 {:<34s} {:6.2f} ms".format(tag, ms))
    return ms


bench("baseline (nothing visible)", lambda: paint(0, 0))
bench("6 polylines w3", lambda: paint(0, 6))
bench("17 dots", lambda: paint(17, 0))
bench("6 polylines + 17 dots", lambda: paint(17, 6))
print("P7 mem_free={}".format(gc.mem_free()))
print("RESULT: DONE")
