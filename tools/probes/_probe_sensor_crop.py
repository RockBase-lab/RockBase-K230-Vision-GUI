# Print the sensor rectangle that the camera app's preview keeps.
#
# The preview channel is configured as set_framesize(w=800, h=480, crop=True).
# The firmware turns crop=True into a centred crop with an integer downscale
# factor, so the retained field of view is narrower than the sensor frame even
# though the 1600x960 window it keeps is exactly 5:3. Print the rectangle for a
# given preview size so the FOV loss stays a measured fact.
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
