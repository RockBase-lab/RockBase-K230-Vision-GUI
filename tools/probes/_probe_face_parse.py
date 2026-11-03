# Measure the face_parse pipeline contract on one real face before writing
# app_mask: parse output shape, per-stage cost, and which (det, rgb_size)
# combination face_parse_post_process maps correctly into a 640x384 ARGB buffer.
#
# Two candidates are drawn and their mask bounding boxes compared against the
# expected screen box scaled to 640x384:
#   A: y-corrected det + rgb_size=[800,480] (screen domain)
#   B: raw det (pad included) + rgb_size=[800,640] (rgb_param domain)
#
# Point the camera at a face before running. Run with --lvgl.

import gc
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")

import ulab.numpy as np
import nncase_runtime as nn
import aidemo
from libs.AI2D import Ai2d
from media.sensor import Sensor, CAM_CHN_ID_1
from camera_fov import set_preview
from model_pool import get as model_get

PREVIEW_W = 800
PREVIEW_H = 480
OUT_W = 640
OUT_H = 384
Y_OFF = 160

DET_MODEL = "/sdcard/examples/kmodel/face_detection_320.kmodel"
PARSE_MODEL = "/sdcard/examples/kmodel/face_parse.kmodel"
ANCHOR_PATH = "/sdcard/examples/utils/prior_data_320.bin"

sensor = None
try:
    anchors = np.frombuffer(open(ANCHOR_PATH, "rb").read(),
                            dtype=np.float).reshape((4200, 4))
    kpu_det = model_get(DET_MODEL)
    kpu_parse = model_get(PARSE_MODEL)
    print("P pool ok mem_free={}".format(gc.mem_free()))

    sensor = Sensor(fps=30)
    sensor.reset()
    set_preview(sensor, CAM_CHN_ID_1, PREVIEW_W, PREVIEW_H)
    sensor.set_pixformat(Sensor.RGBP888, chn=CAM_CHN_ID_1)
    sensor.run()

    a2d_det = Ai2d()
    a2d_det.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                          np.uint8, np.uint8)
    a2d_det.pad([0, 0, 0, 0, 64, 64, 0, 0], 0, [104, 117, 123])
    a2d_det.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
    a2d_det.build([1, 3, PREVIEW_H, PREVIEW_W], [1, 3, 320, 320])

    a2d_parse = Ai2d()
    a2d_parse.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                            np.uint8, np.uint8)

    def detect(img):
        tin = a2d_det.run(img.to_numpy_ref())
        kpu_det.set_input_tensor(0, tin)
        kpu_det.run()
        outs = []
        for i in range(kpu_det.outputs_size()):
            ot = kpu_det.get_output_tensor(i)
            try:
                outs.append(ot.to_numpy())
            finally:
                ot.release()
        res = aidemo.face_det_post_process(0.5, 0.2, 320, anchors,
                                           [PREVIEW_W, PREVIEW_H + Y_OFF], outs)
        del outs, tin
        return res

    boxes = None
    for attempt in range(60):
        img = sensor.snapshot(chn=CAM_CHN_ID_1)
        t0 = time.ticks_ms()
        res = detect(img)
        det_ms = time.ticks_diff(time.ticks_ms(), t0)
        if len(res) and res[0]:
            boxes = res[0]
            print("D attempt={} det={}ms raw={}".format(
                attempt, det_ms,
                [[int(v) for v in b] for b in boxes[:3]]))
            break
        time.sleep_ms(100)
    if boxes is None:
        print("RESULT no face detected")
        raise SystemExit(0)

    # largest face
    best = 0
    for i in range(1, len(boxes)):
        if boxes[i][2] * boxes[i][3] > boxes[best][2] * boxes[best][3]:
            best = i
    raw = [float(v) for v in boxes[best]]
    corr = [raw[0], raw[1] - Y_OFF, raw[2], raw[3]]
    print("C best raw={} corr={}".format(
        [int(v) for v in raw], [int(v) for v in corr]))

    # affine crop from the corrected (screen-domain) box, official formula
    x1, y1, w, h = corr
    factor = 2.7
    edge = 320
    cx0 = x1 + w / 2.0
    cy0 = y1 + h / 2.0
    scale = edge * 2.0 / (factor * (h if h > w else w))
    m = [scale, 0, edge / 2.0 - scale * cx0, 0, scale, edge / 2.0 - scale * cy0]
    a2d_parse.affine(nn.interp_method.cv2_bilinear, 0, 0, 127, 1, m)
    a2d_parse.build([1, 3, PREVIEW_H, PREVIEW_W], [1, 3, 320, 320])

    img = sensor.snapshot(chn=CAM_CHN_ID_1)
    t0 = time.ticks_ms()
    tin = a2d_parse.run(img.to_numpy_ref())
    kpu_parse.set_input_tensor(0, tin)
    kpu_parse.run()
    outs = []
    for i in range(kpu_parse.outputs_size()):
        ot = kpu_parse.get_output_tensor(i)
        try:
            outs.append(ot.to_numpy())
        finally:
            ot.release()
    parse_ms = time.ticks_diff(time.ticks_ms(), t0)
    print("I parse={}ms outs={}".format(
        parse_ms, [o.shape for o in outs]))
    parse_res = outs[0]

    # Warm cost: the first run pays kmodel first-inference overhead. Two more
    # runs on fresh frames report what a steady-state frame actually costs.
    for k in range(2):
        img = sensor.snapshot(chn=CAM_CHN_ID_1)
        t0 = time.ticks_ms()
        tin = a2d_parse.run(img.to_numpy_ref())
        kpu_parse.set_input_tensor(0, tin)
        kpu_parse.run()
        n = 0
        for i in range(kpu_parse.outputs_size()):
            ot = kpu_parse.get_output_tensor(i)
            try:
                arr = ot.to_numpy()
                n += 1
            finally:
                ot.release()
        print("W iter={} warm_parse={}ms outs={}".format(
            k, time.ticks_diff(time.ticks_ms(), t0), n))
        del tin

    def bbox_of(buf):
        # rows/cols where any pixel has nonzero alpha (byte offset 3)
        a = buf[:, :, 3]
        ys = [r for r in range(OUT_H) if np.max(a[r]) > 0]
        xs = [c for c in range(OUT_W) if np.max(a[:, c]) > 0]
        if not ys or not xs:
            return None
        return (min(xs), min(ys), max(xs), max(ys),
                int(np.sum(a > 0)))

    exp_x = int(raw[0] * OUT_W / PREVIEW_W)
    exp_y = int((raw[1] - Y_OFF) * OUT_H / PREVIEW_H)
    exp_w = int(raw[2] * OUT_W / PREVIEW_W)
    exp_h = int(raw[3] * OUT_H / PREVIEW_H)
    print("X expected box in 640x384: [{},{},{}x{}]".format(
        exp_x, exp_y, exp_w, exp_h))

    for tag, det_arg, rgb_arg in (("A", corr, [PREVIEW_W, PREVIEW_H]),
                                  ("B", raw, [PREVIEW_W, PREVIEW_H + Y_OFF])):
        buf = np.zeros((OUT_H, OUT_W, 4), dtype=np.uint8)
        t0 = time.ticks_ms()
        try:
            aidemo.face_parse_post_process(buf, rgb_arg, [OUT_W, OUT_H], 320,
                                           det_arg, parse_res)
            ms = time.ticks_diff(time.ticks_ms(), t0)
            print("S {} rgb={} det={} post={}ms bbox+count={}".format(
                tag, rgb_arg, [int(v) for v in det_arg], ms, bbox_of(buf)))
        except BaseException as e:
            print("S {} FAILED: {}".format(tag, e))
        del buf

    # Clearing cost. The function writes only inside the mask and leaves every
    # other pixel alone, so a reused buffer smears unless it is zeroed first
    # (the official example allocates a fresh np.zeros each frame). Measure the
    # three candidate clears so the app can pick one that fits a 137 ms frame.
    dur = np.zeros((OUT_H, OUT_W, 4), dtype=np.uint8)

    t0 = time.ticks_ms()
    fresh = np.zeros((OUT_H, OUT_W, 4), dtype=np.uint8)
    print("L fresh_zeros={}ms".format(time.ticks_diff(time.ticks_ms(), t0)))
    del fresh

    print("H has_fill={} has_slice={}".format(
        hasattr(dur, "fill"), hasattr(dur, "__setitem__")))

    if hasattr(dur, "fill"):
        t0 = time.ticks_ms()
        dur.fill(0)
        print("L ndarray_fill0={}ms".format(
            time.ticks_diff(time.ticks_ms(), t0)))
    else:
        # Row-wise write: 384 assignments of one 2560-byte row, no temp buffer.
        row = np.zeros((OUT_W, 4), dtype=np.uint8)
        t0 = time.ticks_ms()
        for r in range(OUT_H):
            dur[r] = row
        print("L row_loop_clear={}ms".format(
            time.ticks_diff(time.ticks_ms(), t0)))
        del row

    # Second frame into the same buffer with no clear: any pixel that survives
    # outside the new mask proves the function does not clear by itself.
    aidemo.face_parse_post_process(dur, [PREVIEW_W, PREVIEW_H], [OUT_W, OUT_H],
                                   320, corr, parse_res)
    first = bbox_of(dur)
    aidemo.face_parse_post_process(dur, [PREVIEW_W, PREVIEW_H], [OUT_W, OUT_H],
                                   320, corr, parse_res)
    second = bbox_of(dur)
    print("R reuse_no_clear first={} second={} same={}".format(
        first, second, first == second))
    del dur, outs, parse_res
    print("DONE mem_free={}".format(gc.mem_free()))
finally:
    if sensor is not None:
        try:
            sensor.stop()
        except BaseException:
            pass
        try:
            sensor.deinit()
        except BaseException:
            pass
    print("FIN")
