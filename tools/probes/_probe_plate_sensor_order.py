# Decides whether the sensor's RGBP888 channel has the same channel order as the
# RGB888 channel.
#
# Why it matters: the recognition crop is greyscale, so a swapped red and blue
# plane shifts the luminance of a coloured plate slightly. Every offline
# measurement was made from a JPEG converted to RGB888, so the app assumes the
# camera plane order matches that file path. A mismatch would not crash anything
# and would not move the box; it would only degrade the decoded string, which is
# exactly the kind of fault that wastes a day.
#
# Method: snapshot both channels of the same sensor in the same frame and compare
# the same pixel position. The two channels are configured from one crop window
# (camera_fov.set_preview), so a position maps to the same scene point.
#
# Prints per-pixel triples for several positions plus the mean absolute difference
# between both possible orders, so the answer is numeric rather than eyeballed.
#
# No lv objects, so --lvgl is not needed; the sensor is closed in a finally block.

import gc
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")

from media.sensor import Sensor, CAM_CHN_ID_1, CAM_CHN_ID_2
from camera_fov import set_preview

W = 800
H = 480

sensor = None
try:
    sensor = Sensor(fps=30)
    sensor.reset()
    set_preview(sensor, CAM_CHN_ID_1, W, H)
    sensor.set_pixformat(Sensor.RGBP888, chn=CAM_CHN_ID_1)
    set_preview(sensor, CAM_CHN_ID_2, W, H)
    sensor.set_pixformat(Sensor.RGB888, chn=CAM_CHN_ID_2)
    sensor.run()
    time.sleep_ms(400)

    packed = sensor.snapshot(chn=CAM_CHN_ID_2)      # (H, W, 3)
    planar = sensor.snapshot(chn=CAM_CHN_ID_1)      # (3, H, W)
    print("packed format={} planar format={}".format(
        packed.format(), planar.format()))

    pa = packed.to_numpy_ref()
    pl = planar.to_numpy_ref()
    print("packed dims {}x{}x{}  planar dims {}x{}x{}".format(
        len(pa), len(pa[0]), len(pa[0][0]),
        len(pl), len(pl[0]), len(pl[0][0])))

    POINTS = [(100, 100), (200, 240), (400, 240), (600, 120), (700, 360)]
    same = 0
    swapped = 0
    both = 0
    for (x, y) in POINTS:
        p = pa[y][x]
        c0 = int(pl[0][y][x])
        c1 = int(pl[1][y][x])
        c2 = int(pl[2][y][x])
        print("({:3d},{:3d}) packed=[{:3d},{:3d},{:3d}] planar=[{:3d},{:3d},{:3d}]".format(
            x, y, int(p[0]), int(p[1]), int(p[2]), c0, c1, c2))
        d_same = abs(c0 - int(p[0])) + abs(c1 - int(p[1])) + abs(c2 - int(p[2]))
        d_swap = abs(c0 - int(p[2])) + abs(c1 - int(p[1])) + abs(c2 - int(p[0]))
        same += d_same
        swapped += d_swap
        both += 1

    print("")
    print("total |diff| same-order = {}   swapped-order = {}".format(same, swapped))
    if same < swapped:
        print("VERDICT: planar channel order matches packed (RGB vs RGB)")
    elif swapped < same:
        print("VERDICT: planar order is the REVERSE of packed")
    else:
        print("VERDICT: indistinguishable on this scene (grey walls?)")

    # The app builds its greyscale crop from the planar channel, so this is the
    # number that matters: how far one is from the other after greying.
    tot = 0
    for i in range(0, len(POINTS)):
        x, y = POINTS[i]
        p = pa[y][x]
        grey_packed = (int(p[0]) * 299 + int(p[1]) * 587 + int(p[2]) * 114) // 1000
        grey_planar = (int(pl[0][y][x]) * 299 + int(pl[1][y][x]) * 587
                       + int(pl[2][y][x]) * 114) // 1000
        print("  grey packed={} planar={} delta={}".format(
            grey_packed, grey_planar, abs(grey_packed - grey_planar)))
        tot += abs(grey_packed - grey_planar)
    print("  mean grey delta = {:.1f}".format(tot / len(POINTS) if POINTS else 0))

    del pa, pl, packed, planar
    gc.collect()
    print("mem_free:", gc.mem_free())
except BaseException as error:
    print("FAILED:", error)
    sys.print_exception(error)
finally:
    if sensor is not None:
        for name in ("stop", "deinit"):
            try:
                getattr(sensor, name)()
                print("sensor.{} ok".format(name))
            except BaseException as error:
                print("sensor.{} error: {}".format(name, error))

print("RESULT: DONE")
