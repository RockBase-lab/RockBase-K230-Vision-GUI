# Chooses between "use the detected quad as-is" and "widen along the plate axis".
#
# The stability probe showed the axis-aligned rect (the shipped behaviour) breaks
# the province character on four of six degraded frames, while the raw quad fixes
# all of them. Widening along the plate's own axis also worked but cost 107 ms per
# call because it rebuilt ai2d for a new crop size each time.
#
# Two questions remain, answered here on the two frames where the detector
# under-reads width most:
#   1. does the raw quad drop the last character on a wide plate?
#   2. if so, how much widening fixes it without breaking the province character?
#
# Every strategy is also timed with a reused ai2d, which is the production path,
# so the widening cost is measured honestly.

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
EXPECT = "渝A999U9"

CASES = [
    ("real", "/data/plate/real.jpg"),
    ("small", "/data/plate/small.jpg"),
    ("base", "/data/plate/var_base.jpg"),
    ("tiny", "/data/plate/var_tiny.jpg"),
]
# margin applied to the plate's own width axis; 1.00 is "no widening"
WIDEN = [1.00, 1.06, 1.12, 1.18]

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


def quad_shift(q):
    return np.array([float(q[0]), float(q[1]) - Y_OFF,
                     float(q[2]), float(q[3]) - Y_OFF,
                     float(q[4]), float(q[5]) - Y_OFF,
                     float(q[6]), float(q[7]) - Y_OFF])


def quad_widen(q, margin):
    """Widen along the plate's own width axis so the quad keeps its rotation.

    The top and bottom edge midpoints define the plate's height axis and the left
    and right edge midpoints its width axis, so a rotated plate is widened along
    its own horizontal rather than along the screen's.
    """
    x0, y0 = float(q[0]), float(q[1]) - Y_OFF
    x1, y1 = float(q[2]), float(q[3]) - Y_OFF
    x2, y2 = float(q[4]), float(q[5]) - Y_OFF
    x3, y3 = float(q[6]), float(q[7]) - Y_OFF
    tmx, tmy = (x0 + x1) * 0.5, (y0 + y1) * 0.5
    bmx, bmy = (x3 + x2) * 0.5, (y3 + y2) * 0.5
    dhx, dhy = bmx - tmx, bmy - tmy
    height = (dhx * dhx + dhy * dhy) ** 0.5
    if height < 1.0:
        height = 1.0
    lmx, lmy = (x0 + x3) * 0.5, (y0 + y3) * 0.5
    rmx, rmy = (x1 + x2) * 0.5, (y1 + y2) * 0.5
    dwx, dwy = rmx - lmx, rmy - lmy
    width = (dwx * dwx + dwy * dwy) ** 0.5
    if width < 1.0:
        width = 1.0
    target_w = height * PLATE_ASPECT * margin
    if width >= target_w:
        return quad_shift(q)
    ux, uy = dwx / width, dwy / width
    cx, cy = (lmx + rmx) * 0.5, (lmy + rmy) * 0.5
    half = target_w * 0.5
    half_h = height * 0.5
    vx, vy = -uy, ux
    return np.array([
        cx - ux * half - vx * half_h, cy - uy * half - vy * half_h,
        cx + ux * half - vx * half_h, cy + uy * half - vy * half_h,
        cx + ux * half + vx * half_h, cy + uy * half + vy * half_h,
        cx - ux * half + vx * half_h, cy - uy * half + vy * half_h])


class Rec:
    """Recognition kpu plus a reused ai2d, the way the app runs it."""

    def __init__(self):
        self.kpu = nn.kpu()
        self.kpu.load_kmodel(REC_MODEL)
        self.a2d = None
        self.shape = None

    def build(self, h, w):
        if self.a2d is not None:
            try:
                self.a2d.deinit()
            except BaseException:
                pass
            self.a2d = None
        a2d = Ai2d()
        a2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                           np.uint8, np.uint8)
        a2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
        a2d.build([1, 1, h, w], [1, 3, REC_IN[1], REC_IN[0]])
        self.a2d = a2d
        self.shape = (h, w)

    def decode(self, crop):
        h = len(crop[0][0])
        w = len(crop[0][0][0])
        rebuilt = False
        if self.shape != (h, w):
            self.build(h, w)
            rebuilt = True
        t0 = time.ticks_ms()
        tin = self.a2d.run(crop)
        self.kpu.set_input_tensor(0, tin)
        self.kpu.run()
        ms = time.ticks_diff(time.ticks_ms(), t0)
        ot = self.kpu.get_output_tensor(0)
        try:
            out = ot.to_numpy()
        finally:
            ot.release()
        flat = out.reshape((-1, DICT_SIZE))
        n = len(flat)
        text = ""
        low = 1e9
        prev = -1
        for i in range(n):
            row = flat[i]
            b1 = -1e9
            i1 = -1
            for c in range(DICT_SIZE):
                v = float(row[c])
                if v > b1:
                    b1 = v
                    i1 = c
            if b1 < low:
                low = b1
            if i1 > 0 and i1 != prev:
                text += DICT[i1 - 1] if i1 - 1 < DICT_SIZE else "?"
            prev = i1
        del flat
        return text, low, ms, rebuilt

    def release(self):
        if self.a2d is not None:
            try:
                self.a2d.deinit()
            except BaseException:
                pass
            self.a2d = None
        if self.kpu is not None:
            try:
                self.kpu.release()
            except BaseException:
                pass
            self.kpu = None
        self.shape = None


def run_case(tag, path, det_kpu, ai2d_det, rec):
    print("")
    print("===== {} (expect {})".format(tag, EXPECT))
    chw, w, h = load_chw(path)

    a2d, pad_top = ai2d_det
    mw, mh = DET_IN
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
        del chw
        return
    q = quads[0]
    print("  det quad raw = {}".format([int(q[k]) for k in range(8)]))

    for margin in WIDEN:
        quad = quad_widen(q, margin) if margin > 1.0 else quad_shift(q)
        try:
            crops = aidemo.ocr_rec_preprocess(chw, [PREVIEW_H, PREVIEW_W], [quad])
            imgs = crops[0]
            if len(imgs) == 0:
                print("  widen {:.2f} -> no crop".format(margin))
                continue
            crop = imgs[0]
            cw = len(crop[0][0][0])
            ch = len(crop[0][0])
            text, low, ms, rebuilt = rec.decode(crop)
            print("  widen {:.2f} crop={}x{} {}ms{} -> {!r} low={:.2f} {}".format(
                margin, cw, ch, ms, " (rebuild)" if rebuilt else "", text,
                low, "MATCH" if text == EXPECT else ""))
        except BaseException as error:
            print("  widen {:.2f} FAILED: {}".format(margin, error))

    del chw
    gc.collect()


print("mem_free start =", gc.mem_free())

det_kpu = None
ai2d_det = None
rec = None
try:
    det_kpu = nn.kpu()
    det_kpu.load_kmodel(DET_MODEL)
    mw, mh = DET_IN
    scale = min(float(mw) / PREVIEW_W, float(mh) / PREVIEW_H)
    pad_top = int((mh - int(PREVIEW_H * scale)) / 2)
    a2d = Ai2d()
    a2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                       np.uint8, np.uint8)
    a2d.pad([0, 0, 0, 0, pad_top, pad_top, 0, 0], 0, [128, 128, 128])
    a2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
    a2d.build([1, 3, PREVIEW_H, PREVIEW_W], [1, 3, mh, mw])
    ai2d_det = (a2d, pad_top)

    rec = Rec()
    for tag, path in CASES:
        try:
            run_case(tag, path, det_kpu, ai2d_det, rec)
        except BaseException as error:
            print("case {} failed: {}".format(tag, error))
except BaseException as error:
    print("setup FAILED:", error)
finally:
    if rec is not None:
        rec.release()
    if ai2d_det is not None:
        try:
            ai2d_det[0].deinit()
        except BaseException:
            pass
    if det_kpu is not None:
        try:
            det_kpu.release()
        except BaseException:
            pass
    gc.collect()

print("mem_free end =", gc.mem_free())
print("RESULT: DONE")
