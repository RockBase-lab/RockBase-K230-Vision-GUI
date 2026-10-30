# Crop window shared by every app that opens the camera.
#
# crop=True keeps the largest integer downscale window, so an 800x480 preview
# shows 1600x960 of the 1920x1080 sensor (74% of the scene) while a photo, whose
# channel never crops, keeps all of it. An explicit window at the output aspect
# keeps the full height instead. Both channels of an AI app must share it: the
# overlays map detections by output size alone.
#
# Measured on board at 800x480: crop=(60,0,1800,1080) snapshots in 34.95 ms
# against 34.95 ms for crop=True. Cost is resolution: a subject covers 11% fewer
# pixels, in the picture and in a 320 model input, so the detection range of the
# smaller subjects falls by the same 11%.

SENSOR_W = 1920              # gc2093 native width
SENSOR_H = 1080


def preview_crop(out_w, out_h):
    """Return the widest centred crop (x, y, w, h) that keeps the output aspect."""
    w = SENSOR_H * out_w // out_h
    if w > SENSOR_W:
        return True
    return ((SENSOR_W - w) // 2, 0, w, SENSOR_H)


def set_preview(sensor, chn, w, h):
    """Configure one channel to the widest view at `w`x`h`.

    A rejected window raises AssertionError, which every app turns into its
    "camera unavailable" panel and so hides the cause; fall back to crop=True
    here and lose field of view rather than the camera.
    """
    try:
        sensor.set_framesize(w=w, h=h, chn=chn, crop=preview_crop(w, h))
    except AssertionError as error:
        print("[FOV] explicit crop rejected, using crop=True: {}".format(error))
        sensor.set_framesize(w=w, h=h, chn=chn, crop=True)
