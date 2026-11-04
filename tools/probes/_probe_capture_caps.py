# Reports which transfer encodings this firmware supports for board-side screen
# capture. RLE is enough for flat UI (about 46 KB per 800x480 frame) but expands a
# camera frame to over 1 MB, so a photo screenshot needs a real compressor.
#
# Read-only: allocates one small test image and never touches the sensor.

print("== modules ==")
for _name in ("zlib", "uzlib", "ubinascii", "ujson", "gc"):
    try:
        __import__(_name)
        print("{}: OK".format(_name))
    except BaseException as _e:
        print("{}: NO ({})".format(_name, _e))

import image
import lvgl as lv

print("== image ==")
print("formats RGB888={} RGB565={} BGRA8888={}".format(
    getattr(image, "RGB888", "?"), getattr(image, "RGB565", "?"),
    getattr(image, "BGRA8888", "?")))

try:
    small = image.Image(64, 48, image.RGB888)
    print("alloc 64x48 RGB888: ok size={}".format(small.size()))
    ref = small.to_numpy_ref()
    print("numpy shape={} dtype={}".format(ref.shape, getattr(ref, "dtype", "?")))
except BaseException as error:
    print("alloc/numpy: FAIL ({})".format(error))

print("== snapshot ==")
try:
    scr = lv.obj()
    lv.scr_load(scr)
    lv.task_handler()
    lv.task_handler()
    dsc = lv.snapshot_take(scr, lv.COLOR_FORMAT.ARGB8888)
    print("snapshot ok w={} h={} cf={}".format(dsc.header.w, dsc.header.h,
                                               dsc.header.cf))
    dsc.buf_free()
except BaseException as error:
    print("snapshot FAIL ({})".format(error))

print("== jpeg ==")
try:
    big = image.Image(800, 480, image.RGB888)
    print("alloc 800x480 RGB888: ok size={}".format(big.size()))
    out = big.compress(85)
    data = out.to_bytes()
    print("compress(85) ok bytes={}".format(len(data)))
    del out
    del big
except BaseException as error:
    print("jpeg FAIL ({})".format(error))

print("DONE")
