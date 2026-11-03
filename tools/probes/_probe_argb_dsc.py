# Headless (no sensor, no display): confirm the mechanics of showing the
# segment masks buffer as an LVGL image. Tests three unknowns at once:
#   1) np.frombuffer(bytearray).reshape(4D) works and stays writable
#   2) lv.img_dsc_t accepts a ulab ndarray (vs bytearray) for "data"
#   3) the dsc header cf/w/h round-trips
# No widgets are created, so no --lvgl needed; lv.img_dsc_t is a plain struct.

import ulab.numpy as np
import lvgl as lv

W, H = 640, 384

# 1) bytearray-backed numpy view, 4D like the official masks buffer
buf = bytearray(W * H * 4)
try:
    arr = np.frombuffer(buf, dtype=np.uint8).reshape((1, H, W, 4))
    print("T1 frombuffer OK shape={}".format(arr.shape))
    arr[0][0][0][0] = 123          # write through the view
    print("T1 write-through buf[0]={}".format(buf[0]))
except BaseException as e:
    print("T1 FAIL:", e)

# 2a) dsc from a bytearray
try:
    d1 = lv.img_dsc_t({"header": {"cf": lv.COLOR_FORMAT.ARGB8888, "w": W, "h": H},
                       "data_size": len(buf), "data": buf})
    print("T2a bytearray dsc OK cf={} w={} h={}".format(
        d1.header.cf, d1.header.w, d1.header.h))
except BaseException as e:
    print("T2a FAIL:", e)

# 2b) dsc directly from the ndarray
try:
    d2 = lv.img_dsc_t({"header": {"cf": lv.COLOR_FORMAT.ARGB8888, "w": W, "h": H},
                       "data_size": arr.size, "data": arr})
    print("T2b ndarray dsc OK cf={} w={} h={}".format(
        d2.header.cf, d2.header.w, d2.header.h))
except BaseException as e:
    print("T2b FAIL:", e)

# 3) does a numpy zeros (not frombuffer) also work as data?
try:
    z = np.zeros((1, H, W, 4), dtype=np.uint8)
    d3 = lv.img_dsc_t({"header": {"cf": lv.COLOR_FORMAT.ARGB8888, "w": W, "h": H},
                       "data_size": z.size, "data": z})
    print("T3 zeros-ndarray dsc OK")
except BaseException as e:
    print("T3 FAIL:", e)

print("DONE")
