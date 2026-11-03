# Rule 14 check: load all 16 BOOT_MODELS, then handkp_det as a 17th resident
# kpu, and confirm the 17th still infers. No display needed.

import gc
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")

import ulab.numpy as np
import nncase_runtime as nn
from media.sensor import Sensor, CAM_CHN_ID_1
from camera_fov import set_preview
from model_pool import boot_load, get, loaded_count

boot_load()
print("BOOT resident={} heap={}".format(loaded_count(), gc.mem_free()))

t0 = time.ticks_ms()
kpu_kp = get("/sdcard/examples/kmodel/handkp_det.kmodel")
print("LOAD17 handkp_det {}ms resident={} heap={}".format(
    time.ticks_diff(time.ticks_ms(), t0), loaded_count(), gc.mem_free()))

sensor = None
try:
    sensor = Sensor(fps=30)
    sensor.reset()
    set_preview(sensor, CAM_CHN_ID_1, 800, 480)
    sensor.set_pixformat(Sensor.RGBP888, chn=CAM_CHN_ID_1)
    sensor.run()

    from libs.AI2D import Ai2d
    a2d = Ai2d()
    a2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                      np.uint8, np.uint8)
    a2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
    a2d.build([1, 3, 400, 400], [1, 3, 256, 256])

    img = sensor.snapshot(chn=CAM_CHN_ID_1)
    crop = img.to_numpy_ref()[100:500, 200:600]
    t0 = time.ticks_ms()
    tin = a2d.run(crop)
    kpu_kp.set_input_tensor(0, tin)
    kpu_kp.run()
    out = kpu_kp.get_output_tensor(0).to_numpy()
    kpu_kp.get_output_tensor(0).release()
    print("INFER17 {}ms shape={} sum={}".format(
        time.ticks_diff(time.ticks_ms(), t0), out.shape,
        float(np.sum(out > 0))))
    print("HEAP after={} resident={}".format(gc.mem_free(), loaded_count()))
    print("RESULT: DONE")
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
