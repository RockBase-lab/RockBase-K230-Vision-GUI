# Facts a plate app needs before any UI is written.
#
# Independent stages, so a failure in one still yields the rest:
#   P1  detection model: load time
#   P2  ai2d -> kpu on a synthetic frame: output dims, kpu cost
#   P3  yolo_license_plate_det_postprocess: signature, return layout
#   P4  recognition model: load time, output dims (the CTC class count)
#   P5  ocr_rec_preprocess: return layout for a hand-made quad
#
# No Sensor and no MediaManager: ai2d and the KPU do not need the capture device,
# so the probe cannot wedge the board on a camera or an audio handle. Every KPU
# tensor is released in a finally block.
#
# Hard rule measured on board: never call get_output_tensor() before kpu.run().
# The call blocks with no exception and no serial output, which looks exactly
# like a dead board. Instrument the output only after a run.
#
# The synthetic frame is a flat grey 800x480 RGBP888 array, so a real plate is
# absent and P3 is expected to return no boxes. That still proves the argument
# count, which is what the app has to get right; box geometry is measured later
# with the camera and a printed plate.

import gc
import time

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


def dims(a, depth=0):
    """Comma-joined leading dimensions of a nested sequence.

    ulab ndarrays answer len() but do not always expose __len__ or .shape, so the
    dimensions are walked with len() instead of reading an attribute.
    """
    try:
        n = len(a)
    except BaseException:
        return "scalar"
    if depth >= 4 or n == 0:
        return str(n)
    return "{},{}".format(n, dims(a[0], depth + 1))


def load_model(label, path):
    t0 = time.ticks_ms()
    kpu = nn.kpu()
    kpu.load_kmodel(path)
    print("{} load={}ms".format(label, time.ticks_diff(time.ticks_ms(), t0)))
    return kpu


def run_one(label, kpu, in_w, in_h, out_w, out_h, pad_top, pad_colour):
    """ai2d a flat grey frame into the model and report the output dims.

    pad_top is applied in output space after the resize, which is how this
    binding behaves; callers pass the letterbox amount for their input.
    """
    a2d = Ai2d()
    a2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                       np.uint8, np.uint8)
    if pad_top:
        a2d.pad([0, 0, 0, 0, pad_top, pad_top, 0, 0], 0, pad_colour)
    a2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
    a2d.build([1, 3, in_h, in_w], [1, 3, out_h, out_w])

    frame = np.zeros((3, in_h, in_w), dtype=np.uint8)
    frame[:] = 128
    t0 = time.ticks_ms()
    tin = a2d.run(frame)
    a2d_ms = time.ticks_diff(time.ticks_ms(), t0)
    del frame

    kpu.set_input_tensor(0, tin)
    t0 = time.ticks_ms()
    kpu.run()
    kpu_ms = time.ticks_diff(time.ticks_ms(), t0)
    print("{} ai2d={}ms kpu={}ms".format(label, a2d_ms, kpu_ms))

    out = None
    ot = kpu.get_output_tensor(0)
    try:
        out = ot.to_numpy()
        print("{} out dims={}".format(label, dims(out)))
    finally:
        ot.release()
    a2d.deinit()
    return out


# ---------------- P1 / P2 detection ----------------
det = None
raw_det = None
try:
    det = load_model("P1 det", DET_MODEL)
except BaseException as error:
    print("P1 FAILED:", error)

if det is not None:
    try:
        mw, mh = DET_IN
        scale = min(float(mw) / PREVIEW_W, float(mh) / PREVIEW_H)
        pad_top = int((mh - int(PREVIEW_H * scale)) / 2)
        print("P2 scale={} pad_top={}".format(scale, pad_top))
        raw_det = run_one("P2 det", det, PREVIEW_W, PREVIEW_H,
                          mw, mh, pad_top, [128, 128, 128])
        try:
            print("P2 det max={}".format(float(np.max(raw_det))))
        except BaseException as error:
            print("P2 det max failed:", error)
    except BaseException as error:
        print("P2 FAILED:", error)

# ---------------- P3 detection post-process ----------------
det_res = None
if raw_det is not None:
    try:
        # The example transposes results[0][0] before copying: the tensor comes
        # out channel-first, so the post-process needs anchors on axis 0.
        try:
            grid = raw_det[0].transpose().copy()
            print("P3 transposed dims={}".format(dims(grid)))
        except BaseException as error:
            print("P3 transpose failed ({}), using raw[0].copy()".format(error))
            grid = raw_det[0].copy()
            print("P3 raw dims={}".format(dims(grid)))

        t0 = time.ticks_ms()
        det_res = aidemo.yolo_license_plate_det_postprocess(
            grid, [PREVIEW_H, PREVIEW_W], [DET_IN[1], DET_IN[0]],
            [PREVIEW_H, PREVIEW_W], DET_CONF, DET_NMS, DET_MAX)
        print("P3 post={}ms dims={}".format(
            time.ticks_diff(time.ticks_ms(), t0), dims(det_res)))
        try:
            print("P3 boxes={}".format(len(det_res[0])))
        except BaseException as error:
            print("P3 boxes len failed:", error)
        if det_res and len(det_res[0]) > 0:
            print("P3 first box dims={}".format(dims(det_res[0][0])))
        del grid
    except BaseException as error:
        print("P3 FAILED:", error)
else:
    print("P3 skipped: no detection tensor")

del raw_det
gc.collect()

# ---------------- P4 recognition model ----------------
rec = None
try:
    rec = load_model("P4 rec", REC_MODEL)
except BaseException as error:
    print("P4 FAILED:", error)

if rec is not None:
    try:
        # Straight into the recognition input: no crop needed to learn the class
        # count, and this keeps P4 independent of whether P5 works.
        run_one("P4 rec", rec, REC_IN[0], REC_IN[1], REC_IN[0], REC_IN[1],
                0, None)
    except BaseException as error:
        print("P4 run FAILED:", error)

# ---------------- P5 ocr_rec_preprocess layout ----------------
if det_res is not None and len(det_res[0]) > 0:
    quads = det_res[0]
    print("P5 using detected quads")
else:
    # Hand-made centred quad, 400x120, as flat [x0,y0,...] in image coordinates.
    quads = [[300, 180, 500, 180, 500, 300, 300, 300]]
    print("P5 no detection, using a synthetic quad")

try:
    frame2 = np.zeros((3, PREVIEW_H, PREVIEW_W), dtype=np.uint8)
    frame2[:] = 128
    t0 = time.ticks_ms()
    crops = aidemo.ocr_rec_preprocess(frame2, [PREVIEW_H, PREVIEW_W], quads)
    print("P5 crop={}ms dims={}".format(
        time.ticks_diff(time.ticks_ms(), t0), dims(crops)))
    imgs = crops[0]
    print("P5 crops={}".format(len(imgs)))
    if len(imgs) > 0:
        print("P5 crop[0] dims={}".format(dims(imgs[0])))
        print("P5 crop[0] type={}".format(type(imgs[0])))
    try:
        print("P5 boxes dims={}".format(dims(crops[1])))
    except BaseException as error:
        print("P5 boxes unavailable:", error)
    del frame2
except BaseException as error:
    print("P5 FAILED:", error)

# ---------------- teardown ----------------
for name, kpu in (("P6 det", det), ("P6 rec", rec)):
    if kpu is None:
        continue
    try:
        kpu.release()
        print("{} released".format(name))
    except BaseException as error:
        print("{} release failed: {}".format(name, error))
gc.collect()
print("RESULT: DONE")
