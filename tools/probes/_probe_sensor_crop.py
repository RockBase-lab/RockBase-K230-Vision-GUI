# Print the sensor rectangle that the firmware keeps for crop=True.
#
# The apps do not call crop=True any more: camera_fov.set_preview passes an
# explicit window. This probe keeps the integer-downscale path measurable, which
# is both the fallback and the baseline for the field-of-view comparison.
# crop=True keeps the largest integer downscale factor, so a 5:3 output gets a
# narrower window than the sensor allows. Prints the rectangle for a given
# output size so the FOV loss stays a measured fact.
#
# Pure Python probe: no Sensor instance, no LVGL, no media init.

from media.sensor import Sensor

SENSOR_W = 1920
SENSOR_H = 1080


def pct(part, total):
    """Percentage to one decimal, rounded, so it matches the value in docs/."""
    tenths = (part * 1000 + total // 2) // total
    return "{}.{}%".format(tenths // 10, tenths % 10)


def show(name, tw, th):
    try:
        x, y, w, h = Sensor._calculate_crop(SENSOR_W, SENSOR_H, tw, th)
    except BaseException as error:
        print("P {} error {}".format(name, error))
        return
    print("P {} crop=({},{},{},{}) keep_w={} keep_h={} downscale={}x{}".format(
        name, x, y, w, h, pct(w, SENSOR_W), pct(h, SENSOR_H),
        w // tw, h // th))


print("P0 has _calculate_crop: {}".format(hasattr(Sensor, "_calculate_crop")))
show("preview_800x480", 800, 480)
print("P9 done")
