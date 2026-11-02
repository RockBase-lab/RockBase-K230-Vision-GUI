# Decides the recognition input channel count.
#
# Evidence so far:
#   - the frame must be CHW (3,H,W): a (200,120,40) pixel came out as grey 135,
#     which is the luminance of that exact colour, so the layout was read right
#   - the crop reports shape (1,1,H,W) and size H*W, i.e. one channel
#
# The reference pipeline builds ai2d for a 3-channel crop. If the crop really has
# one channel, a 3-channel build reads past the buffer (silent garbage) while a
# 1-channel build matches the data. This probe prints the plane count and tries
# both builds, so the app uses whichever the runtime accepts.

import gc
import time

import ulab.numpy as np
import nncase_runtime as nn
import aidemo
from libs.AI2D import Ai2d

PREVIEW_W = 800
PREVIEW_H = 480
REC_MODEL = "/sdcard/examples/kmodel/licence_reco.kmodel"
REC_IN = [220, 32]

DICT = ["挂", "使", "领", "澳", "港", "皖", "沪", "津", "渝", "冀",
        "晋", "蒙", "辽", "吉", "黑", "苏", "浙", "京", "闽", "赣",
        "鲁", "豫", "鄂", "湘", "粤", "桂", "琼", "川", "贵", "云",
        "藏", "陕", "甘", "青", "宁", "新", "警", "学",
        "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
        "A", "B", "C", "D", "E", "F", "G", "H", "J", "K",
        "L", "M", "N", "P", "Q", "R", "S", "T", "U", "V",
        "W", "X", "Y", "Z", "_"]

# Coloured frame so grey conversion is verifiable, and CHW because that layout
# produced the correct luminance above.
frame = np.zeros((3, PREVIEW_H, PREVIEW_W), dtype=np.uint8)
frame[0][:] = 200
frame[1][:] = 120
frame[2][:] = 40

QUAD = [300.0, 180.0, 500.0, 180.0, 500.0, 300.0, 300.0, 300.0]
crop = None
try:
    res = aidemo.ocr_rec_preprocess(frame, [PREVIEW_H, PREVIEW_W], [np.array(QUAD)])
    imgs = res[0]
    crop = imgs[0]
    print("crop shape:", crop.shape, "size:", crop.size)
    print("crop planes:", len(crop[0]))
    print("plane0 rows:", len(crop[0][0]))
    try:
        print("plane0 row0[0:6]:", [int(crop[0][0][0][i]) for i in range(6)])
    except BaseException as error:
        print("sample failed:", error)
    if len(crop[0]) > 1:
        try:
            print("plane1 row0[0:6]:", [int(crop[0][1][0][i]) for i in range(6)])
        except BaseException as error:
            print("plane1 sample failed:", error)
except BaseException as error:
    print("crop FAILED:", error)

if crop is not None:
    kpu = None
    try:
        kpu = nn.kpu()
        kpu.load_kmodel(REC_MODEL)
    except BaseException as error:
        print("load FAILED:", error)
        kpu = None

    for ch in (1, 3):
        a2d = None
        try:
            a2d = Ai2d()
            a2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                               np.uint8, np.uint8)
            a2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
            a2d.build([1, ch, 120, 200], [1, 3, REC_IN[1], REC_IN[0]])
            tin = a2d.run(crop)
            print("build C={} accepted".format(ch))
            if kpu is None:
                continue
            kpu.set_input_tensor(0, tin)
            t0 = time.ticks_ms()
            kpu.run()
            ms = time.ticks_diff(time.ticks_ms(), t0)
            ot = kpu.get_output_tensor(0)
            try:
                out = ot.to_numpy()
                flat = out.reshape((-1, 74))
                idx = np.argmax(flat, axis=1)
                text = ""
                prev = -1
                for i in range(len(idx)):
                    v = int(idx[i])
                    if v > 0 and v != prev:
                        text += DICT[v - 1] if v - 1 < len(DICT) else "?"
                    prev = v
                print("  C={} kpu={}ms text={}".format(ch, ms, text))
                del flat
            finally:
                ot.release()
        except BaseException as error:
            print("  C={} FAILED: {}".format(ch, error))
        finally:
            if a2d is not None:
                try:
                    a2d.deinit()
                except BaseException:
                    pass

    if kpu is not None:
        try:
            kpu.release()
        except BaseException as error:
            print("release failed:", error)

gc.collect()
print("RESULT: DONE")
