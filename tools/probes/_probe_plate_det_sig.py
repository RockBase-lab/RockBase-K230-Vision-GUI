# Cracks the real argument semantics of the two plate post-processes.
#
# Facts so far, on /data/plate/real.jpg where the plate really spans
# (28,122)-(778,370):
#   yolo_license_plate_det_postprocess returned x 47..636, y 273..540 for every
#   display_size tried, so argument 4 is not the display size in any usable
#   sense; after subtracting 160 from y the vertical edges are right, but the
#   right edge falls 142 px short, which cuts the last character.
#
# Two candidate explanations, tested here:
#   - argument 2 wants [w, h] rather than [h, w], so the x scale is wrong
#   - the other pair (LPD_640 + licence_det_postprocess) returns frame
#     coordinates directly, as its reference use implies (it divides by
#     rgb888p_size when drawing and never passes a display size)
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
YOLO_MODEL = "/sdcard/examples/kmodel/yolo_license_plate_det.kmodel"
LPD_MODEL = "/sdcard/examples/kmodel/LPD_640.kmodel"
DET_IN = [640, 640]
CONF = 0.2
NMS = 0.2
MAX = 10
TRUTH = (28, 122, 778, 370)


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


def run_model(path, chw, pad_colour):
    """Load a model, run it once on chw, return the first output numpy array."""
    kpu = nn.kpu()
    a2d = None
    try:
        kpu.load_kmodel(path)
        mw, mh = DET_IN
        scale = min(float(mw) / PREVIEW_W, float(mh) / PREVIEW_H)
        pad_top = int((mh - int(PREVIEW_H * scale)) / 2)
        a2d = Ai2d()
        a2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                           np.uint8, np.uint8)
        a2d.pad([0, 0, 0, 0, pad_top, pad_top, 0, 0], 0, pad_colour)
        a2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
        a2d.build([1, 3, PREVIEW_H, PREVIEW_W], [1, 3, mh, mw])
        tin = a2d.run(chw)
        kpu.set_input_tensor(0, tin)
        kpu.run()
        ot = kpu.get_output_tensor(0)
        try:
            out = ot.to_numpy()
        finally:
            ot.release()
        return out, kpu
    finally:
        if a2d is not None:
            try:
                a2d.deinit()
            except BaseException:
                pass


def show(label, quads):
    if len(quads) == 0:
        print("  {:22s} -> no box".format(label))
        return
    q = quads[0]
    xs = [int(q[0]), int(q[2]), int(q[4]), int(q[6])]
    ys = [int(q[1]), int(q[3]), int(q[5]), int(q[7])]
    print("  {:22s} -> x {}..{} y {}..{} | truth x {}..{} y {}..{}".format(
        label, min(xs), max(xs), min(ys), max(ys),
        TRUTH[0], TRUTH[2], TRUTH[1], TRUTH[3]))


chw, w, h = load_chw("/data/plate/real.jpg")
print("frame {}x{}  truth={}".format(w, h, TRUTH))

# ---------------- yolo model + yolo post-process, varied arguments ----------------
print("")
print("== yolo_license_plate_det.kmodel ==")
try:
    out, kpu = run_model(YOLO_MODEL, chw, [128, 128, 128])
    print("  raw dims {}x{}x{}".format(len(out), len(out[0]), len(out[0][0])))
    grid = out[0].transpose().copy()
    del out

    variants = [
        ("rgb=[h,w] disp=[h,w]", [PREVIEW_H, PREVIEW_W], [PREVIEW_H, PREVIEW_W]),
        ("rgb=[w,h] disp=[h,w]", [PREVIEW_W, PREVIEW_H], [PREVIEW_H, PREVIEW_W]),
        ("rgb=[h,w] disp=[w,h]", [PREVIEW_H, PREVIEW_W], [PREVIEW_W, PREVIEW_H]),
        ("rgb=[w,h] disp=[w,h]", [PREVIEW_W, PREVIEW_H], [PREVIEW_W, PREVIEW_H]),
        ("rgb=[model] disp=[model]",
         [DET_IN[1], DET_IN[0]], [DET_IN[1], DET_IN[0]]),
    ]
    for label, rgb, disp in variants:
        try:
            res = aidemo.yolo_license_plate_det_postprocess(
                grid, rgb, [DET_IN[1], DET_IN[0]], disp, CONF, NMS, MAX)
            show(label, res[0])
        except BaseException as error:
            print("  {:22s} -> FAILED: {}".format(label, error))
    del grid
finally:
    try:
        kpu.release()
    except BaseException:
        pass
    gc.collect()

# ---------------- LPD model + licence_det_postprocess ----------------
print("")
print("== LPD_640.kmodel + licence_det_postprocess ==")
try:
    out, kpu = run_model(LPD_MODEL, chw, [0, 0, 0])
    print("  raw dims {}x{}x{}".format(len(out), len(out[0]), len(out[0][0])))
    try:
        res = aidemo.licence_det_postprocess(
            out, [PREVIEW_H, PREVIEW_W], [DET_IN[1], DET_IN[0]], CONF, NMS)
        print("  result dims {}".format(len(res)))
        show("licence_det rgb=[h,w]", res[0])
    except BaseException as error:
        print("  licence_det rgb=[h,w] FAILED:", error)
    try:
        res = aidemo.licence_det_postprocess(
            out, [PREVIEW_W, PREVIEW_H], [DET_IN[1], DET_IN[0]], CONF, NMS)
        show("licence_det rgb=[w,h]", res[0])
    except BaseException as error:
        print("  licence_det rgb=[w,h] FAILED:", error)
    del out
finally:
    try:
        kpu.release()
    except BaseException:
        pass
    gc.collect()

del chw
print("RESULT: DONE")
