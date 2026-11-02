# Settles the licensing post-process coordinate domain.
#
# Measured so far on /data/plate/real.jpg, whose plate really occupies
# (28,122)-(778,370) frame pixels (found by scanning for the blue body):
#   passing disp=[480,800] returned quad x 47..636, y 273..540
# y is 160 too low, which is the known letterbox pad leak. x looks *unscaled*,
# because 47..636 is 92% of the 640-wide model input while the real plate is 94%.
#
# Rather than infer the mapping, this probe runs the same frame under several
# display_size arguments and prints each quad next to the true rectangle. The
# argument whose quad matches after the y correction is the one the app uses.
#
# No Sensor; every kpu tensor is released in a finally block.

import gc

import image
import ulab.numpy as np
import nncase_runtime as nn
import aidemo
from libs.AI2D import Ai2d

PREVIEW_W = 800
PREVIEW_H = 480
DET_MODEL = "/sdcard/examples/kmodel/yolo_license_plate_det.kmodel"
DET_IN = [640, 640]
DET_CONF = 0.2
DET_NMS = 0.2
DET_MAX = 10
Y_OFF = 160                      # pad_top(128) * 800 / 640

VARIANTS = [
    ("disp=[480,800]", [PREVIEW_H, PREVIEW_W]),
    ("disp=[800,480]", [PREVIEW_W, PREVIEW_H]),
    ("disp=[640,640]", [DET_IN[1], DET_IN[0]]),
    ("disp=[800,640]", [PREVIEW_W, DET_IN[0]]),
    ("disp=[480,640]", [PREVIEW_H, DET_IN[0]]),
]


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


img = image.Image("/data/plate/real.jpg").to_rgb888()
w = img.width()
h = img.height()
ref = img.to_numpy_ref()
truth = blue_bbox(ref, w, h)
print("truth bbox =", truth)

chw = np.zeros((3, h, w), dtype=np.uint8)
chw[0] = ref[:, :, 0]
chw[1] = ref[:, :, 1]
chw[2] = ref[:, :, 2]
del ref, img

mw, mh = DET_IN
scale = min(float(mw) / PREVIEW_W, float(mh) / PREVIEW_H)
pad_top = int((mh - int(PREVIEW_H * scale)) / 2)
print("letterbox scale={} pad_top={}".format(scale, pad_top))

kpu = None
a2d = None
grid = None
try:
    kpu = nn.kpu()
    kpu.load_kmodel(DET_MODEL)

    a2d = Ai2d()
    a2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                       np.uint8, np.uint8)
    a2d.pad([0, 0, 0, 0, pad_top, pad_top, 0, 0], 0, [128, 128, 128])
    a2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
    a2d.build([1, 3, PREVIEW_H, PREVIEW_W], [1, 3, mh, mw])

    tin = a2d.run(chw)
    kpu.set_input_tensor(0, tin)
    kpu.run()
    ot = kpu.get_output_tensor(0)
    try:
        raw = ot.to_numpy()
    finally:
        ot.release()
    # One transpose, reused by every variant: the post-process does not mutate it.
    grid = raw[0].transpose().copy()
    print("grid dims = {}x{}".format(len(grid), len(grid[0])))
    del raw

    for label, disp in VARIANTS:
        try:
            res = aidemo.yolo_license_plate_det_postprocess(
                grid, [PREVIEW_H, PREVIEW_W], [DET_IN[1], DET_IN[0]],
                disp, DET_CONF, DET_NMS, DET_MAX)
            quads = res[0]
            if len(quads) == 0:
                print("{:16s} -> no box".format(label))
                continue
            q = quads[0]
            xs = [int(q[0]), int(q[2]), int(q[4]), int(q[6])]
            ys = [int(q[1]), int(q[3]), int(q[5]), int(q[7])]
            raw_txt = "x {}..{} y {}..{}".format(min(xs), max(xs), min(ys), max(ys))
            fixed = "x {}..{} y {}..{}".format(
                min(xs), max(xs), min(ys) - Y_OFF, max(ys) - Y_OFF)
            print("{:16s} -> raw {} | minus y_off {}".format(label, raw_txt, fixed))
        except BaseException as error:
            print("{:16s} -> FAILED: {}".format(label, error))
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
    del chw, grid
    gc.collect()

print("RESULT: DONE")
