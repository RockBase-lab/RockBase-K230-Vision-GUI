# Confirms the quad argument form: the binding wants a *list of ndarrays*.
#
# Observed on board:
#   list of lists -> "expected ndarray"   (the element is not an ndarray)
#   ndarray       -> "expected list"      (the container is not a list)
# so the container is a list and its elements are ndarrays. This probe tries the
# element dtypes a C binding would accept and reports the crop layout, which is
# what the recognition step has to resize.
#
# No kpu, no sensor: pure aidemo.

import ulab.numpy as np
import aidemo

PREVIEW_W = 800
PREVIEW_H = 480

frame = np.zeros((3, PREVIEW_H, PREVIEW_W), dtype=np.uint8)
frame[:] = 128

QUAD = [300.0, 180.0, 500.0, 180.0, 500.0, 300.0, 300.0, 300.0]

variants = [
    ("list[np float]", [np.array(QUAD)]),
    ("list[np int16]", [np.array([int(v) for v in QUAD], dtype=np.int16)]),
    ("list[np uint16]", [np.array([int(v) for v in QUAD], dtype=np.uint16)]),
]

for label, quads in variants:
    try:
        res = aidemo.ocr_rec_preprocess(frame, [PREVIEW_H, PREVIEW_W], quads)
        print("OK  {:16s} len(res)={}".format(label, len(res)))
        for i in range(len(res)):
            item = res[i]
            try:
                n = len(item)
            except BaseException:
                n = -1
            print("      res[{}] len={} type={}".format(i, n, type(item)))
        imgs = res[0]
        if len(imgs) > 0:
            crop = imgs[0]
            print("      crop type={} dtype={}".format(type(crop), crop.dtype))
            try:
                print("      crop dims={}x{}x{}x{}".format(
                    len(crop), len(crop[0]), len(crop[0][0]), len(crop[0][0][0])))
            except BaseException as error:
                print("      crop dims failed:", error)
        try:
            boxes = res[1]
            print("      boxes len={} type={}".format(len(boxes), type(boxes)))
            if len(boxes) > 0:
                print("      box[0] len={}".format(len(boxes[0])))
        except BaseException as error:
            print("      boxes inspect failed:", error)
    except BaseException as error:
        print("ERR {:16s} -> {}".format(label, error))

print("RESULT: DONE")
