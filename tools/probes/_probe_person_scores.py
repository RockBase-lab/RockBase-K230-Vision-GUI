# Diagnostic: run person_detect_yolov5n on live frames and print the score-slot
# maximum before thresholding, to tell "no person in view" apart from a broken
# decode. A healthy pipeline on an empty room scores < 0.1; a person in view
# should push the top score past 0.5.
#
# The whole-tensor max is NOT the score: coordinate channels (tx/ty) also reach
# ~1.0, which once faked a healthy-looking 1.003. Only channel offsets 4..5 of
# each anchor group [x,y,w,h,obj,cls] hold sigmoid scores.

import gc
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("apps.anchor_det", "apps.app_person", "apps", "apps.error",
              "ui.beep", "launcher", "camera_fov", "model_pool"):
    if _name in sys.modules:
        del sys.modules[_name]

import nncase_runtime as nn
import ulab.numpy as np
import aicube
from libs.AI2D import Ai2d
from media.sensor import Sensor, CAM_CHN_ID_1
from camera_fov import set_preview


def score_max(outs):
    best = 0.0
    for o in outs:
        # ulab caps at 4 dims: fold H,W into rows -> (H*W, 3 anchors, 6 slots)
        sc = o.reshape((o.shape[1] * o.shape[2], 3, 6))
        m = float(np.max(sc[:, :, 4:6]))
        if m > best:
            best = m
    return best


def main(sensor):
    kpu = nn.kpu()
    kpu.load_kmodel("/sdcard/examples/kmodel/person_detect_yolov5n.kmodel")
    ai2d = Ai2d()
    ai2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                       np.uint8, np.uint8)
    # 800x480 -> 640x640: scale = min(640/800, 640/480) = 0.8; pad_top = (640-384)/2 = 128
    ai2d.pad([0, 0, 0, 0, 128, 128, 0, 0], 0, [0, 0, 0])
    ai2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
    ai2d.build([1, 3, 480, 800], [1, 3, 640, 640])

    anchors = [10, 13, 16, 30, 33, 23, 30, 61, 62, 45, 59, 119,
               116, 90, 156, 198, 373, 326]

    for frame_i in range(10):
        try:
            img = sensor.snapshot(chn=CAM_CHN_ID_1)
        except BaseException as e:
            print("S snapshot fail:", e)
            time.sleep_ms(100)
            continue
        tin = ai2d.run(img.to_numpy_ref())
        kpu.set_input_tensor(0, tin)
        kpu.run()
        outs = []
        for i in range(kpu.outputs_size()):
            ot = kpu.get_output_tensor(i)
            try:
                outs.append(ot.to_numpy())
            finally:
                ot.release()
        best = score_max(outs)
        if frame_i == 0:
            for oi, o in enumerate(outs):
                print("T{} shape={} dtype={} min={:.3f} max={:.3f}".format(
                    oi, o.shape, o.dtype, float(np.min(o)), float(np.max(o))))
        dets = aicube.anchorbasedet_post_process(
            outs[0], outs[1], outs[2], [640, 640], [800, 480], [8, 16, 32],
            1, 0.01, 0.6, anchors, False)
        n = len(dets) if dets else 0
        print("F{} score_max={:.3f} dets={}".format(frame_i, best, n))
        if dets and n:
            for d in dets[:3]:
                print("   cls={} score={:.2f} box=({},{})-({},{})".format(
                    int(d[0]), float(d[1]), int(d[2]), int(d[3]),
                    int(d[4]), int(d[5])))
        time.sleep_ms(150)


sensor = None
try:
    sensor = Sensor(fps=30)
    sensor.reset()
    set_preview(sensor, CAM_CHN_ID_1, 800, 480)
    sensor.set_pixformat(Sensor.RGBP888, chn=CAM_CHN_ID_1)
    sensor.run()
    main(sensor)
finally:
    if sensor is not None:
        try:
            sensor.stop()
        except BaseException as e:
            print("sensor.stop:", e)
        try:
            sensor.deinit()
        except BaseException as e:
            print("sensor.deinit:", e)
    print("DONE mem_free={}".format(gc.mem_free()))
