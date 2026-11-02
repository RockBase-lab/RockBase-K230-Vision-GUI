# Diagnoses why the province character is unstable while the digits are correct.
#
# Compares crop strategies on the variants produced by
# tools/_gen_plate_variants.py and prints, per strategy:
#   - the decoded string
#   - the confidence of each emitted character
#   - the margin between top1 and top2 at the step that produced the first
#     character, which is the number that tells "the model is guessing" apart
#     from "the model is confident and wrong"
#
# Strategies:
#   A  current app behaviour: axis-aligned rect, widened to 3.14:1, 1.08 margin
#   B  same, but the margin applies to width only (height is not inflated)
#   C  the detected quad as-is (rotation preserved, only y corrected)
#   D  quad widened along its own width axis by the plate aspect
#
# Whether the recogniser emits logits or probabilities is measured here too: the
# raw top1 value is printed, so no assumption is baked into the app.
#
# No Sensor. Every kpu tensor is released in a finally block.

import gc
import time

import image
import ulab.numpy as np
import nncase_runtime as nn
import aidemo
from libs.AI2D import Ai2d

PREVIEW_W = 800
PREVIEW_H = 480
DET_MODEL = "/sdcard/examples/kmodel/yolo_license_plate_det.kmodel"
REC_MODEL = "/sdcard/examples/kmodel/licence_reco.kmodel"
DET_IN = [640, 640]
REC_IN = [220, 32]
DET_CONF = 0.2
DET_NMS = 0.2
DET_MAX = 10
Y_OFF = 160
PLATE_ASPECT = 3.14
MARGIN = 1.08
EXPECT = "渝A999U9"

VARIANTS = ["base", "small", "tiny", "blur", "cropped", "shifted"]

DICT = ["挂", "使", "领", "澳", "港", "皖", "沪", "津", "渝", "冀",
        "晋", "蒙", "辽", "吉", "黑", "苏", "浙", "京", "闽", "赣",
        "鲁", "豫", "鄂", "湘", "粤", "桂", "琼", "川", "贵", "云",
        "藏", "陕", "甘", "青", "宁", "新", "警", "学",
        "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
        "A", "B", "C", "D", "E", "F", "G", "H", "J", "K",
        "L", "M", "N", "P", "Q", "R", "S", "T", "U", "V",
        "W", "X", "Y", "Z", "_", "-"]
DICT_SIZE = len(DICT)


def load_chw(path):
    img = image.Image(path).to_rgb888()
    w = img.width()
    h = img.height()
    ref = img.to_numpy_ref()
    chw = np.zeros((3, h, w), dtype=np.uint8)
    chw[0] = ref[:, :, 0]
    chw[1] = ref[:, :, 1]
    chw[2] = ref[:, :, 2]
    del ref, img
    return chw, w, h


def quad_rect(q, margin_w, margin_h):
    """Axis-aligned rect from a quad, with independent width/height margins."""
    xs = [q[0], q[2], q[4], q[6]]
    ys = [q[1] - Y_OFF, q[3] - Y_OFF, q[5] - Y_OFF, q[7] - Y_OFF]
    x0, x1 = min(xs), max(xs)
    y0, y1 = min(ys), max(ys)
    bw = max(x1 - x0, 1.0)
    bh = max(y1 - y0, 1.0)
    cx, cy = (x0 + x1) * 0.5, (y0 + y1) * 0.5
    if bw / bh < PLATE_ASPECT:
        bw = bh * PLATE_ASPECT
    bw *= margin_w
    bh *= margin_h
    x0 = max(0.0, cx - bw / 2)
    x1 = min(float(PREVIEW_W - 1), cx + bw / 2)
    y0 = max(0.0, cy - bh / 2)
    y1 = min(float(PREVIEW_H - 1), cy + bh / 2)
    return np.array([x0, y0, x1, y0, x1, y1, x0, y1])


def quad_shift(q):
    """The detected quad with the pad removed, rotation intact."""
    return np.array([float(q[0]), float(q[1]) - Y_OFF,
                     float(q[2]), float(q[3]) - Y_OFF,
                     float(q[4]), float(q[5]) - Y_OFF,
                     float(q[6]), float(q[7]) - Y_OFF])


def quad_widen(q):
    """Widen the quad along its own width axis to the plate aspect."""
    x0, y0 = float(q[0]), float(q[1]) - Y_OFF
    x1, y1 = float(q[2]), float(q[3]) - Y_OFF
    x2, y2 = float(q[4]), float(q[5]) - Y_OFF
    x3, y3 = float(q[6]), float(q[7]) - Y_OFF
    # top edge midpoint and bottom edge midpoint give the plate's own axis
    tmx, tmy = (x0 + x1) * 0.5, (y0 + y1) * 0.5
    bmx, bmy = (x3 + x2) * 0.5, (y3 + y2) * 0.5
    dhx, dhy = bmx - tmx, bmy - tmy
    height = (dhx * dhx + dhy * dhy) ** 0.5
    if height < 1.0:
        height = 1.0
    # left and right edge midpoints give the width direction
    lmx, lmy = (x0 + x3) * 0.5, (y0 + y3) * 0.5
    rmx, rmy = (x1 + x2) * 0.5, (y1 + y2) * 0.5
    dwx, dwy = rmx - lmx, rmy - lmy
    width = (dwx * dwx + dwy * dwy) ** 0.5
    if width < 1.0:
        width = 1.0
    target_w = height * PLATE_ASPECT * MARGIN
    if width >= target_w:
        return quad_shift(q)
    ux, uy = dwx / width, dwy / width
    cx, cy = (lmx + rmx) * 0.5, (lmy + rmy) * 0.5
    half = target_w * 0.5
    nx0, ny0 = cx - ux * half, cy - uy * half
    nx1, ny1 = cx + ux * half, cy + uy * half
    half_h = height * 0.5
    vx, vy = -uy, ux
    return np.array([
        nx0 - vx * half_h, ny0 - vy * half_h,
        nx1 - vx * half_h, ny1 - vy * half_h,
        nx1 + vx * half_h, ny1 + vy * half_h,
        nx0 + vx * half_h, ny0 + vy * half_h])


def decode(kpu, crop):
    """crop -> ai2d(220x32) -> per-character confidence and margin."""
    h = len(crop[0][0])
    w = len(crop[0][0][0])
    a2d = Ai2d()
    try:
        a2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                           np.uint8, np.uint8)
        a2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
        a2d.build([1, 1, h, w], [1, 3, REC_IN[1], REC_IN[0]])
        tin = a2d.run(crop)
        kpu.set_input_tensor(0, tin)
        kpu.run()
    finally:
        try:
            a2d.deinit()
        except BaseException:
            pass

    ot = kpu.get_output_tensor(0)
    try:
        out = ot.to_numpy()
    finally:
        ot.release()

    flat = out.reshape((-1, DICT_SIZE))
    n = len(flat)
    text = ""
    confs = []
    margins = []
    prev = -1
    for i in range(n):
        row = flat[i]
        # top1 and top2 without sorting the whole row
        b1 = -1e9
        b2 = -1e9
        i1 = -1
        for c in range(DICT_SIZE):
            v = float(row[c])
            if v > b1:
                b2 = b1
                b1 = v
                i1 = c
            elif v > b2:
                b2 = v
        margins.append(b1 - b2)
        if i1 > 0 and i1 != prev:
            text += DICT[i1 - 1] if i1 - 1 < DICT_SIZE else "?"
            confs.append(b1)
        prev = i1
    del flat
    return text, confs, margins


def run_case(tag, path):
    print("")
    print("===== {} (expect {})".format(tag, EXPECT))
    chw, w, h = load_chw(path)

    det_kpu = None
    rec_kpu = None
    a2d = None
    try:
        det_kpu = nn.kpu()
        det_kpu.load_kmodel(DET_MODEL)
        rec_kpu = nn.kpu()
        rec_kpu.load_kmodel(REC_MODEL)

        mw, mh = DET_IN
        scale = min(float(mw) / PREVIEW_W, float(mh) / PREVIEW_H)
        pad_top = int((mh - int(PREVIEW_H * scale)) / 2)
        a2d = Ai2d()
        a2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                           np.uint8, np.uint8)
        a2d.pad([0, 0, 0, 0, pad_top, pad_top, 0, 0], 0, [128, 128, 128])
        a2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
        a2d.build([1, 3, PREVIEW_H, PREVIEW_W], [1, 3, mh, mw])

        tin = a2d.run(chw)
        det_kpu.set_input_tensor(0, tin)
        det_kpu.run()
        ot = det_kpu.get_output_tensor(0)
        try:
            raw = ot.to_numpy()
        finally:
            ot.release()
        grid = raw[0].transpose().copy()
        del raw
        res = aidemo.yolo_license_plate_det_postprocess(
            grid, [PREVIEW_H, PREVIEW_W], [DET_IN[1], DET_IN[0]],
            [PREVIEW_H, PREVIEW_W], DET_CONF, DET_NMS, DET_MAX)
        del grid
        quads = res[0]
        if len(quads) == 0:
            print("  no detection")
            return
        q = quads[0]
        print("  det quad raw = {}".format([int(q[k]) for k in range(8)]))

        strategies = [
            ("A cur  3.14/1.08/1.08", quad_rect(q, MARGIN, MARGIN)),
            ("B w-only 3.14/1.08/1.00", quad_rect(q, MARGIN, 1.0)),
            ("C quad as-is", quad_shift(q)),
            ("D quad widen", quad_widen(q)),
        ]

        for label, quad in strategies:
            try:
                crops = aidemo.ocr_rec_preprocess(
                    chw, [PREVIEW_H, PREVIEW_W], [quad])
                imgs = crops[0]
                if len(imgs) == 0:
                    print("  {:24s} -> no crop".format(label))
                    continue
                crop = imgs[0]
                ch = len(crop[0][0])
                cw = len(crop[0][0][0])
                t0 = time.ticks_ms()
                text, confs, margins = decode(rec_kpu, crop)
                ms = time.ticks_diff(time.ticks_ms(), t0)
                first_conf = confs[0] if confs else 0.0
                first_margin = margins[0] if margins else 0.0
                ok = text == EXPECT
                print("  {:24s} crop={}x{} {}ms -> {!r} {}".format(
                    label, cw, ch, ms, text, "MATCH" if ok else ""))
                print("  {:24s}   first_char margin={:.2f} conf={:.2f} "
                      "| min_conf={:.2f}".format(
                          "", first_margin, first_conf,
                          min(confs) if confs else 0.0))
            except BaseException as error:
                print("  {:24s} FAILED: {}".format(label, error))

        del chw
    except BaseException as error:
        print("  FAILED:", error)
    finally:
        if a2d is not None:
            try:
                a2d.deinit()
            except BaseException:
                pass
        for name, kpu in (("det", det_kpu), ("rec", rec_kpu)):
            if kpu is None:
                continue
            try:
                kpu.release()
            except BaseException as error:
                print("  {} release failed: {}".format(name, error))
        gc.collect()


print("mem_free start =", gc.mem_free())
for name in VARIANTS:
    path = "/data/plate/var_" + name + ".jpg"
    try:
        run_case(name, path)
    except BaseException as error:
        print("case {} failed: {}".format(name, error))
print("mem_free end =", gc.mem_free())
print("RESULT: DONE")
