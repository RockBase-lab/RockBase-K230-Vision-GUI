# Why this probe exists
#
# ocr_rec_preprocess returned a crop shaped (1, 1, 120, 200) from a frame passed as
# (3, 480, 800). The reference pipeline builds ai2d for a 3-channel crop, so either
# the frame layout is not CHW, or the crop really is single channel. Both cases
# matter: a 3-channel build over 1-channel data reads past the buffer and silently
# produces garbage, which would look like "the model is inaccurate".
#
# This probe feeds the same quad with several input layouts and prints the crop
# shape each time. The layout that yields 3 channels is the one to use.
#
# No sensor, no kpu: pure aidemo, so it cannot wedge the board.

import ulab.numpy as np
import aidemo

PREVIEW_W = 800
PREVIEW_H = 480


def shape_of(a):
    try:
        return "shape={}".format(a.shape)
    except BaseException:
        pass
    parts = []
    cur = a
    for _ in range(5):
        try:
            parts.append(len(cur))
        except BaseException:
            break
        try:
            cur = cur[0]
        except BaseException:
            break
    return "lenwalk={}".format(parts)


def make_chw():
    a = np.zeros((3, PREVIEW_H, PREVIEW_W), dtype=np.uint8)
    a[0][:] = 200
    a[1][:] = 120
    a[2][:] = 40
    return a


def make_hwc():
    a = np.zeros((PREVIEW_H, PREVIEW_W, 3), dtype=np.uint8)
    for y in range(0, PREVIEW_H, 4):
        for x in range(0, PREVIEW_W, 4):
            a[y][x][0] = 200
            a[y][x][1] = 120
            a[y][x][2] = 40
    return a


QUAD = [300.0, 180.0, 500.0, 180.0, 500.0, 300.0, 300.0, 300.0]
quads = [np.array(QUAD)]

for label, frame in (("CHW (3,H,W)", make_chw()), ("HWC (H,W,3)", make_hwc())):
    print("---", label, shape_of(frame), "dtype", frame.dtype)
    try:
        res = aidemo.ocr_rec_preprocess(frame, [PREVIEW_H, PREVIEW_W], quads)
        imgs = res[0]
        print("    crops:", len(imgs))
        if len(imgs) > 0:
            crop = imgs[0]
            print("    crop:", shape_of(crop), "dtype:", crop.dtype)
            try:
                print("    crop size:", crop.size)
            except BaseException as error:
                print("    size unavailable:", error)
            # mean of the first plane, so a layout mix-up is visible as a value
            try:
                print("    crop[0][0][0][0:4]:", [int(crop[0][0][0][i]) for i in range(4)])
            except BaseException as error:
                print("    sample failed:", error)
        if len(res) > 1 and len(res[1]) > 0:
            print("    box[0]:", shape_of(res[1][0]))
    except BaseException as error:
        print("    FAILED:", error)
    del frame

print("RESULT: DONE")
