# Compare an explicit crop window against crop=True on the same sensor run.
#
# crop=True keeps only the largest integer downscale window (1600x960 for an
# 800x480 output), which throws away 26% of the scene. An explicit window at
# the output aspect keeps more. This probe answers two questions that cannot be
# read from the source:
#   P  does the firmware accept an explicit crop whose width is not a multiple
#      of 16, and what crop_win / scale_win does it end up with;
#   T  does the wider window cost anything per snapshot.
#
# Run with --lvgl so the display and media manager come up exactly as they do
# for the apps; the probe itself creates no widgets.

import gc
import sys
import time

from media.sensor import (Sensor, CAM_CHN_ID_1, CAM_CHN_ID_2)

SENSOR_W = 1920
SENSOR_H = 1080
OUT_W = 800
OUT_H = 480
# Full sensor height, width chosen to keep the 5:3 output aspect.
WIDE = (60, 0, 1800, 1080)
FRAMES = 20

sensor = None


def describe(name, chn):
    """Print the channel attributes the firmware actually stored."""
    try:
        attr = sensor._chn_attr[chn]
        print("P {} out={}x{} crop_en={} crop_win=({},{},{},{}) "
              "scale_en={} scale_win=({},{},{},{})".format(
                  name,
                  attr.out_win.width, attr.out_win.height,
                  attr.crop_enable,
                  attr.crop_win.h_start, attr.crop_win.v_start,
                  attr.crop_win.width, attr.crop_win.height,
                  attr.scale_enable,
                  attr.scale_win.h_start, attr.scale_win.v_start,
                  attr.scale_win.width, attr.scale_win.height))
    except BaseException as error:
        print("P {} attr error {}".format(name, error))


def timed(name, chn):
    """Snapshot FRAMES times and report the mean cost and the real frame size."""
    try:
        t0 = time.ticks_ms()
        img = None
        for _ in range(FRAMES):
            img = sensor.snapshot(chn=chn)
        elapsed = time.ticks_diff(time.ticks_ms(), t0)
        print("T {} {:.2f} ms/frame  frame={}x{}".format(
            name, elapsed / float(FRAMES), img.width(), img.height()))
    except BaseException as error:
        print("T {} snapshot error {}".format(name, error))


try:
    sensor = Sensor(fps=30)
    sensor.reset()
    # chn2 = explicit window, chn1 = crop=True baseline, same format and output
    # size so the comparison isolates the crop.
    sensor.set_framesize(w=OUT_W, h=OUT_H, chn=CAM_CHN_ID_2, crop=WIDE)
    sensor.set_pixformat(Sensor.RGB888, chn=CAM_CHN_ID_2)
    sensor.set_framesize(w=OUT_W, h=OUT_H, chn=CAM_CHN_ID_1, crop=True)
    sensor.set_pixformat(Sensor.RGB888, chn=CAM_CHN_ID_1)
    sensor.run()
    print("P run ok, mem_free={}".format(gc.mem_free()))
    describe("wide", CAM_CHN_ID_2)
    describe("base", CAM_CHN_ID_1)
    # Warm up both channels so the first frame's allocation is not timed.
    sensor.snapshot(chn=CAM_CHN_ID_2)
    sensor.snapshot(chn=CAM_CHN_ID_1)
    # Save one frame per channel: the wider window must show the same subject
    # smaller, which proves the ISP applied crop_win rather than merely
    # accepting it.
    for name, chn in (("wide", CAM_CHN_ID_2), ("base", CAM_CHN_ID_1)):
        img = sensor.snapshot(chn=chn)
        img.to_jpeg().save("/data/photos/fov_{}.jpg".format(name), quality=85)
        print("S saved /data/photos/fov_{}.jpg".format(name))
    timed("wide", CAM_CHN_ID_2)
    timed("base", CAM_CHN_ID_1)
except BaseException as error:
    print("P setup error {}".format(error))
    sys.print_exception(error)
finally:
    if sensor is not None:
        for method in ("stop", "deinit"):
            try:
                getattr(sensor, method)()
            except BaseException as error:
                print("CLEAN sensor.{} error {}".format(method, error))
print("P9 done")
