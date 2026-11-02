# Decides which crop the recognition model actually wants.
#
# Known from earlier runs:
#   - detection works: the quad lands on the plate after subtracting y_off
#   - ocr_rec_preprocess returns a correct but *grayscale* crop, shape (1,1,H,W)
#   - feeding that crop produced one unrelated character, both at a 1-channel and
#     a 3-channel ai2d build
#
# So the question is the colour content, not the geometry. This probe builds the
# crop several ways and prints the decoded text for each, with the expected
# answer next to it:
#   A  grayscale crop from ocr_rec_preprocess            (1 channel)
#   B  same crop replicated into three planes            (3 channels, grey)
#   C  the raw RGB rectangle cut from the frame          (3 channels, colour)
#
# No Sensor; every kpu tensor is released in a finally block.

import gc
import time

import image
import ulab.numpy as np
import nncase_runtime as nn
import aidemo
from libs.AI2D import Ai2d

PREVIEW_W = 800
PREVIEW_H = 480
REC_MODEL = "/sdcard/examples/kmodel/licence_reco.kmodel"
REC_IN = [220, 32]
EXPECT = "渝A999U9"

DICT = ["挂", "使", "领", "澳", "港", "皖", "沪", "津", "渝", "冀",
        "晋", "蒙", "辽", "吉", "黑", "苏", "浙", "京", "闽", "赣",
        "鲁", "豫", "鄂", "湘", "粤", "桂", "琼", "川", "贵", "云",
        "藏", "陕", "甘", "青", "宁", "新", "警", "学",
        "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
        "A", "B", "C", "D", "E", "F", "G", "H", "J", "K",
        "L", "M", "N", "P", "Q", "R", "S", "T", "U", "V",
        "W", "X", "Y", "Z", "_"]


def load_rgb(path):
    img = image.Image(path).to_rgb888()
    return img


def to_chw(ref, w, h):
    chw = np.zeros((3, h, w), dtype=np.uint8)
    chw[0] = ref[:, :, 0]
    chw[1] = ref[:, :, 1]
    chw[2] = ref[:, :, 2]
    return chw


def blue_bbox(ref, w, h):
    x0, y0, x1, y1 = w, h, -1, -1
    for y in range(0, h, 2):
        row = ref[y]
        for x in range(0, w, 2):
            px = row[x]
            r = int(px[0])
            b = int(px[2])
            if b > 110 and r < 90 and b - r > 60:
                if x < x0:
                    x0 = x
                if x > x1:
                    x1 = x
                if y < y0:
                    y0 = y
                if y > y1:
                    y1 = y
    return None if x1 < 0 else (x0, y0, x1, y1)


def run_rec(kpu, array, channels):
    """array (1,C,H,W) -> ai2d to 220x32 -> ctc text."""
    h = array.shape[2]
    w = array.shape[3]
    a2d = Ai2d()
    try:
        a2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                           np.uint8, np.uint8)
        a2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
        a2d.build([1, channels, h, w], [1, 3, REC_IN[1], REC_IN[0]])
        tin = a2d.run(array)
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
    flat = out.reshape((-1, 74))
    idx = np.argmax(flat, axis=1)
    text = ""
    prev = -1
    for i in range(len(idx)):
        v = int(idx[i])
        if v > 0 and v != prev:
            text += DICT[v - 1] if v - 1 < len(DICT) else "?"
        prev = v
    del flat, idx
    return text


print("expected text:", EXPECT)

img = load_rgb("/data/plate/real.jpg")
w = img.width()
h = img.height()
ref = img.to_numpy_ref()
chw = to_chw(ref, w, h)
box = blue_bbox(ref, w, h)
print("blue bbox:", box)

kpu = None
try:
    kpu = nn.kpu()
    kpu.load_kmodel(REC_MODEL)

    # ---- A: grayscale crop from the reference pre-process ----
    x0, y0, x1, y1 = box
    quad = np.array([float(x0), float(y0), float(x1), float(y0),
                     float(x1), float(y1), float(x0), float(y1)])
    res = aidemo.ocr_rec_preprocess(chw, [PREVIEW_H, PREVIEW_W], [quad])
    gray = res[0][0]
    gh = len(gray[0][0])
    gw = len(gray[0][0][0])
    print("gray crop {}x{}".format(gw, gh))
    t0 = time.ticks_ms()
    print("A gray 1ch      -> {!r} ({}ms)".format(
        run_rec(kpu, gray, 1), time.ticks_diff(time.ticks_ms(), t0)))

    # ---- B: the same gray replicated into three planes ----
    rep = np.zeros((1, 3, gh, gw), dtype=np.uint8)
    rep[0][0] = gray[0][0]
    rep[0][1] = gray[0][0]
    rep[0][2] = gray[0][0]
    print("B gray 3ch repl -> {!r}".format(run_rec(kpu, rep, 3)))

    # ---- C: the colour rectangle cut straight from the frame ----
    cw = x1 - x0
    chh = y1 - y0
    rgb = np.zeros((1, 3, chh, cw), dtype=np.uint8)
    for c in range(3):
        rgb[0][c] = chw[c][y0:y1, x0:x1]
    print("colour crop {}x{}".format(cw, chh))
    print("C colour 3ch    -> {!r}".format(run_rec(kpu, rgb, 3)))

    # ---- D: colour rectangle, channel order reversed ----
    rgb2 = np.zeros((1, 3, chh, cw), dtype=np.uint8)
    rgb2[0][0] = chw[2][y0:y1, x0:x1]
    rgb2[0][1] = chw[1][y0:y1, x0:x1]
    rgb2[0][2] = chw[0][y0:y1, x0:x1]
    print("D colour BGR    -> {!r}".format(run_rec(kpu, rgb2, 3)))

    del rep, rgb, rgb2, gray, chw
except BaseException as error:
    print("FAILED:", error)
finally:
    if kpu is not None:
        try:
            kpu.release()
        except BaseException as error:
            print("release failed:", error)
    gc.collect()

print("RESULT: DONE")
