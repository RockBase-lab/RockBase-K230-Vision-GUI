# Does segment_postprocess clear the mask buffer it is given?
#
# The Segment app reuses one ARGB buffer for the whole session. If the helper
# leaves pixels it does not paint, a previous object's mask stays on screen once
# the object moves away. Decisive test: plant one alpha value far from any
# detection, run the helper, read it back.
#
# Run with --lvgl. Point the camera at an object.

import gc
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")

import ulab.numpy as np
import nncase_runtime as nn
import aidemo
from libs.AI2D import Ai2d
from media.sensor import Sensor, CAM_CHN_ID_1
from hardware.camera_fov import set_preview
from core.model_pool import get as model_get

W = 640
H = 384
CONF = 0.30
NMS = 0.45
MASK_TH = 0.50

sensor = None
try:
    kpu = model_get("/sdcard/examples/kmodel/yolov8n_seg_320.kmodel")

    sensor = Sensor(fps=30)
    sensor.reset()
    set_preview(sensor, CAM_CHN_ID_1, 320, 320)
    sensor.set_pixformat(Sensor.RGBP888, chn=CAM_CHN_ID_1)
    sensor.run()

    a2d = Ai2d()
    a2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                       np.uint8, np.uint8)
    a2d.build([1, 3, 320, 320], [1, 3, 320, 320])

    buf = np.zeros((H, W, 4), dtype=np.uint8)
    marker = 0
    for _ in range(60):
        img = sensor.snapshot(chn=CAM_CHN_ID_1)
        tin = a2d.run(img.to_numpy_ref())
        kpu.set_input_tensor(0, tin)
        kpu.run()
        outs = []
        for i in range(kpu.outputs_size()):
            ot = kpu.get_output_tensor(i)
            try:
                outs.append(ot.to_numpy())
            finally:
                ot.release()
        if float(np.max(outs[0][0][4:84])) < CONF:
            del outs, tin
            time.sleep_ms(50)
            continue

        # Plant a marker in a corner, well away from a centred object.
        buf[3, 3, 3] = 199
        res = aidemo.segment_postprocess(outs, [320, 320], [320, 320],
                                         [H, W], CONF, NMS, MASK_TH, buf)
        del outs, tin
        marker = int(buf[3, 3, 3])
        painted = int(np.sum(buf[:, :, 3] > 0))
        n = len(res[0]) if res and res[0] else 0
        print("R objs={} painted={} marker_kept={} (199 = not cleared)".format(
            n, painted, marker == 199))
        break
    else:
        print("RESULT no detection in 60 frames")

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
