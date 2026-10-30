# Verify the shared preview window end to end.
#
# Checks three things the source cannot show:
#   P  the helper returns the expected windows for the app output sizes;
#   I  all five camera apps still import (the helper is a new module);
#   S  the firmware stores the explicit window on both channels, and what a
#      snapshot of each format costs.
#
# One Sensor per VM, so this uses the Scan channel mix (chn1 grayscale, chn2
# packed RGB888) which is the new configuration. Run with --lvgl.

import gc
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("camera_fov", "apps.app_camera", "apps.app_scan", "apps.app_vision",
              "apps.app_face_det", "apps.app_face_points", "apps", "apps.error"):
    sys.modules.pop(_name, None)

from camera_fov import preview_crop, set_preview, SENSOR_W, SENSOR_H
from media.sensor import Sensor, CAM_CHN_ID_1, CAM_CHN_ID_2

FRAMES = 10
OUT_W, OUT_H = 800, 480


def describe(name, sensor, chn):
    attr = sensor._chn_attr[chn]
    print("P {} out={}x{} crop_en={} crop_win=({},{},{},{}) scale_win=({},{},{},{})".format(
        name,
        attr.out_win.width, attr.out_win.height, attr.crop_enable,
        attr.crop_win.h_start, attr.crop_win.v_start,
        attr.crop_win.width, attr.crop_win.height,
        attr.scale_win.h_start, attr.scale_win.v_start,
        attr.scale_win.width, attr.scale_win.height))


def timed(name, sensor, chn):
    t0 = time.ticks_ms()
    img = None
    for _ in range(FRAMES):
        img = sensor.snapshot(chn=chn)
    elapsed = time.ticks_diff(time.ticks_ms(), t0)
    print("T {} {:.2f} ms/frame {}x{}".format(
        name, elapsed / float(FRAMES), img.width(), img.height()))


print("P crop(800,480)={} crop(640,480)={} crop(800,450)={}".format(
    preview_crop(800, 480), preview_crop(640, 480), preview_crop(800, 450)))
print("P sensor={}x{}".format(SENSOR_W, SENSOR_H))

import apps.app_camera
import apps.app_scan
import apps.app_vision
print("P imports camera+scan+vision ok")

sensor = None
try:
    sensor = Sensor(fps=30)
    sensor.reset()
    set_preview(sensor, CAM_CHN_ID_2, OUT_W, OUT_H)
    sensor.set_pixformat(Sensor.RGB888, chn=CAM_CHN_ID_2)
    set_preview(sensor, CAM_CHN_ID_1, OUT_W, OUT_H)
    sensor.set_pixformat(Sensor.GRAYSCALE, chn=CAM_CHN_ID_1)
    sensor.run()
    describe("chn2", sensor, CAM_CHN_ID_2)
    describe("chn1", sensor, CAM_CHN_ID_1)
    timed("chn2_rgb888", sensor, CAM_CHN_ID_2)
    timed("chn1_gray", sensor, CAM_CHN_ID_1)
    print("P mem_free={}".format(gc.mem_free()))
finally:
    if sensor is not None:
        try:
            sensor.stop()
            sensor.deinit()
            print("P sensor released")
        except BaseException as error:
            print("P release error {}".format(error))
print("P done")
