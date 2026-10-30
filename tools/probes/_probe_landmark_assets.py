# Checks the resources and aidemo symbols a 106-point landmark app needs.
# Reads file sizes and hasattr only: no Sensor, no kmodel load, so it cannot
# wedge the board.

import os

import aidemo

FILES = [
    "/sdcard/examples/kmodel/face_detection_320.kmodel",
    "/sdcard/examples/kmodel/face_landmark.kmodel",
    "/sdcard/examples/utils/prior_data_320.bin",
]

for path in FILES:
    try:
        print("FILE {} {}".format(os.stat(path)[6], path))
    except BaseException as e:
        print("FILE -1 {} ({})".format(path, e))

SYMS = [
    "face_det_post_process", "face_landmark_post_process",
    "face_mesh_post_process", "invert_affine_transform",
    "polylines", "contours", "face_align",
]
for name in SYMS:
    print("SYM {} {}".format(
        "yes" if hasattr(aidemo, name) else "no", name))

import nncase_runtime as nn
print("SYM nn.ai2d affine:", hasattr(nn.ai2d, "affine") if hasattr(nn, "ai2d")
      else "no ai2d attr")
print("SYM interp cv2_bilinear:", hasattr(nn.interp_method, "cv2_bilinear"))
print("RESULT: DONE")
