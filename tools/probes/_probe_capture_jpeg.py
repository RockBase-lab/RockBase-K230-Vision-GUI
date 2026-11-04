# Verifies the photo-screenshot path: LVGL snapshot -> ulab channel swap ->
# JPEG written to /data, which the PC can pull as one small file.
#
# RLE expands a camera frame past 1 MB, so a screenshot with a live preview needs
# this route instead. Writes only under /data/capture/ and never touches the sensor.

import gc
import os
import uctypes

import lvgl as lv
import image
import ulab.numpy as np

W, H = 800, 480
OUT = "/data/capture"


def ensure_dir(path):
    cur = ""
    for part in path.strip("/").split("/"):
        cur += "/" + part
        try:
            os.mkdir(cur)
        except OSError:
            pass


ensure_dir(OUT)
print("heap before={}KB".format(gc.mem_free() // 1024))

# A screen with both flat areas and a gradient, so the JPEG size is representative.
scr = lv.obj()
scr.set_style_bg_color(lv.color_hex(0x12141A), 0)
for i in range(60):
    box = lv.obj(scr)
    box.set_pos(20 + (i % 10) * 78, 40 + (i // 10) * 70)
    box.set_size(64, 56)
    box.set_style_bg_color(lv.color_hex(0x203040 + i * 0x080402), 0)
    box.set_style_border_width(0, 0)
lv.scr_load(scr)
for _ in range(3):
    lv.task_handler()
print("snapshot...")
dsc = lv.snapshot_take(scr, lv.COLOR_FORMAT.ARGB8888)
w, h = dsc.header.w, dsc.header.h
ptr = int.from_bytes(bytes(memoryview(dsc.data)), "little")
raw = uctypes.bytearray_at(ptr, w * h * 4)
print("snapshot {}x{} heap={}KB".format(w, h, gc.mem_free() // 1024))

try:
    img = image.Image(w, h, image.RGB888)
    dst = img.to_numpy_ref()
    src = np.frombuffer(raw, dtype=np.uint8).reshape((h, w, 4))
    dst[:, :, 0] = src[:, :, 2]
    dst[:, :, 1] = src[:, :, 1]
    dst[:, :, 2] = src[:, :, 0]
    del src
    print("channel swap ok heap={}KB".format(gc.mem_free() // 1024))

    try:
        img.compress(85)
        print("compress ok")
    except BaseException as error:
        print("compress FAIL ({})".format(error))

    path = OUT + "/caps_test.jpg"
    try:
        img.save(path)
        print("save ok bytes={}".format(os.stat(path)[6]))
    except BaseException as error:
        print("save FAIL ({})".format(error))

    del img
except BaseException as error:
    print("swap FAIL ({})".format(error))

dsc.buf_free()
print("heap after={}KB".format(gc.mem_free() // 1024))
print("DONE")
