# Does face_parse_post_process actually write pixels into a ulab ndarray, and
# does it leave the rest of the buffer untouched?
#
# Both matter for app_mask: the mask overlay reuses one buffer per frame, so a
# function that neither clears nor writes produces either a stale smear or a
# blank overlay. Run with --lvgl (it needs the camera and the model pool).

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

sensor = None
try:
    anchors = np.frombuffer(open("/sdcard/examples/utils/prior_data_320.bin",
                                  "rb").read(),
                            dtype=np.float).reshape((4200, 4))
    kpu_det = model_get("/sdcard/examples/kmodel/face_detection_320.kmodel")
    kpu_parse = model_get("/sdcard/examples/kmodel/face_parse.kmodel")

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

    boxes = None
    for _ in range(40):
        img = sensor.snapshot(chn=CAM_CHN_ID_1)
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
        if len(res) and res[0]:
            boxes = res[0]
            break
        time.sleep_ms(100)

    if boxes is None:
        print("RESULT no face detected")
        raise SystemExit(0)

    best = 0
    for i in range(1, len(boxes)):
        if boxes[i][2] * boxes[i][3] > boxes[best][2] * boxes[best][3]:
            best = i
    b = boxes[best]
    det = [float(b[0]), float(b[1]) - Y_OFF, float(b[2]), float(b[3])]
    print("DET corr={}".format([int(v) for v in det]))

    x1, y1, w, h = det
    scale = 320 * 2.0 / (2.7 * (h if h > w else w))
    m = [scale, 0, 160 - scale * (x1 + w / 2.0),
         0, scale, 160 - scale * (y1 + h / 2.0)]
    a2d_parse.affine(nn.interp_method.cv2_bilinear, 0, 0, 127, 1, m)
    a2d_parse.build([1, 3, PREVIEW_H, PREVIEW_W], [1, 3, 320, 320])

    img = sensor.snapshot(chn=CAM_CHN_ID_1)
    tin = a2d_parse.run(img.to_numpy_ref())
    kpu_parse.set_input_tensor(0, tin)
    kpu_parse.run()
    parse_res = None
    for i in range(kpu_parse.outputs_size()):
        ot = kpu_parse.get_output_tensor(i)
        try:
            arr = ot.to_numpy()
            if parse_res is None:
                parse_res = arr
        finally:
            ot.release()
    del tin
    print("PARSE out={}".format(parse_res.shape))

    def report(tag, buf):
        a = buf[:, :, 3]
        nz = int(np.sum(a > 0))
        ys = [r for r in range(OUT_H) if np.max(a[r]) > 0]
        xs = [c for c in range(OUT_W) if np.max(a[:, c]) > 0]
        box = None if not ys or not xs else (min(xs), min(ys), max(xs), max(ys))
        vals = sorted({int(a[ys[0], c]) for c in range(min(OUT_W, 64))})[:6]
        print("{} alpha_nonzero={} bbox={} sample_alpha={}".format(
            tag, nz, box, vals))

    buf = np.zeros((OUT_H, OUT_W, 4), dtype=np.uint8)
    t0 = time.ticks_ms()
    aidemo.face_parse_post_process(buf, [PREVIEW_W, PREVIEW_H],
                                   [OUT_W, OUT_H], 320, det, parse_res)
    ms = time.ticks_diff(time.ticks_ms(), t0)
    print("P post={}ms".format(ms))
    report("FIRST", buf)

    second = np.zeros((OUT_H, OUT_W, 4), dtype=np.uint8)
    aidemo.face_parse_post_process(second, [PREVIEW_W, PREVIEW_H],
                                   [OUT_W, OUT_H], 320, det, parse_res)
    same = int(np.sum((buf != second))) == 0
    print("S deterministic_same={}".format(same))
    del second, buf

    print("DONE")
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
