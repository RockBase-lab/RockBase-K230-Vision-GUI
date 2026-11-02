# Tests asymmetric widening: keep the left edge, extend only to the right.
#
# The previous probe showed symmetric widening breaks the province character:
# on the wide frame the box is 19% short and the whole shortfall is on the right,
# so growing both sides pushes the left edge into background and the first
# character loses its left half. Extending only to the right keeps the character
# that is already correctly framed.
#
# This also measures how many of the six frames decode correctly under each of:
#   L1  left edge fixed, right edge = left + height * 3.14
#   L2  same with a 1.06 margin
#   C   raw quad, no widening (the reference)
#
# No Sensor; the rec ai2d is reused, which is the production path.

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
    ("blur", "/data/plate/var_blur.jpg"),
    ("cropped", "/data/plate/var_cropped.jpg"),
    ("shifted", "/data/plate/var_shifted.jpg"),
]
RIGHT_MARGINS = [1.00, 1.06, 1.12]

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


def quad_right_only(q, margin):
    """Keep the left edge, move the right edge out to the plate aspect."""
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
    if target_w <= width:
        return quad_shift(q)

    ux, uy = dwx / width, dwy / width
    vx, vy = -uy, ux
    half_h = height * 0.5
    # left edge stays where the detector put it, right edge moves out
    nx0, ny0 = lmx, lmy
    nx1, ny1 = lmx + ux * target_w, lmy + uy * target_w
    return np.array([
        nx0 - vx * half_h, ny0 - vy * half_h,
        nx1 - vx * half_h, ny1 - vy * half_h,
        nx1 + vx * half_h, ny1 + vy * half_h,
        nx0 + vx * half_h, ny0 + vy * half_h])


class Rec:
    def __init__(self):
        self.kpu = nn.kpu()
        self.kpu.load_kmodel(REC_MODEL)
        self.a2d = None
        self.shape = None

    def decode(self, crop):
        h = len(crop[0][0])
        w = len(crop[0][0][0])
        if self.shape != (h, w):
            if self.a2d is not None:
                try:
                    self.a2d.deinit()
                except BaseException:
                    pass
            a2d = Ai2d()
            a2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                               np.uint8, np.uint8)
            a2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
            a2d.build([1, 1, h, w], [1, 3, REC_IN[1], REC_IN[0]])
            self.a2d = a2d
            self.shape = (h, w)
        tin = self.a2d.run(crop)
        self.kpu.set_input_tensor(0, tin)
        self.kpu.run()
        ot = self.kpu.get_output_tensor(0)
        try:
            out = ot.to_numpy()
        finally:
            ot.release()
        flat = out.reshape((-1, DICT_SIZE))
        n = len(flat)
        text = ""
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
            if i1 > 0 and i1 != prev:
                text += DICT[i1 - 1] if i1 - 1 < DICT_SIZE else "?"
            prev = i1
        del flat
        return text

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


tally = {}
tally_labels = ["C raw", "R1.00", "R1.06", "R1.12"]
for label in tally_labels:
    tally[label] = 0

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
    ai2d_det = a2d

    rec = Rec()

    for tag, path in CASES:
        print("")
        print("===== {} (expect {})".format(tag, EXPECT))
        chw, w, h = load_chw(path)
        tin = ai2d_det.run(chw)
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
            continue
        q = quads[0]

        variants = [("C raw", quad_shift(q))]
        for m in RIGHT_MARGINS:
            variants.append(("R{:.2f}".format(m), quad_right_only(q, m)))

        for label, quad in variants:
            try:
                crops = aidemo.ocr_rec_preprocess(
                    chw, [PREVIEW_H, PREVIEW_W], [quad])
                imgs = crops[0]
                if len(imgs) == 0:
                    print("  {:6s} -> no crop".format(label))
                    continue
                crop = imgs[0]
                text = rec.decode(crop)
                ok = text == EXPECT
                if ok:
                    tally[label] += 1
                print("  {:6s} crop={}x{} -> {!r} {}".format(
                    label, len(crop[0][0][0]), len(crop[0][0]), text,
                    "MATCH" if ok else ""))
            except BaseException as error:
                print("  {:6s} FAILED: {}".format(label, error))
        del chw
        gc.collect()
except BaseException as error:
    print("setup FAILED:", error)
finally:
    if rec is not None:
        rec.release()
    if ai2d_det is not None:
        try:
            ai2d_det.deinit()
        except BaseException:
            pass
    if det_kpu is not None:
        try:
            det_kpu.release()
        except BaseException:
            pass
    gc.collect()

print("")
print("===== score out of {}".format(len(CASES)))
for label in tally_labels:
    print("  {:6s} {}/{}".format(label, tally[label], len(CASES)))
print("RESULT: DONE")
