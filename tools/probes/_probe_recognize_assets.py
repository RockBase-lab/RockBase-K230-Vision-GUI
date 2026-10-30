# Checks the assets and aidemo symbols a face-recognition app needs.
#
# Files only: no Sensor and no kmodel load, so it cannot wedge the board. Prints
# the presence and size of both recognition models and the state of the feature
# database directory.

import os

import aidemo

FILES = [
    "/sdcard/examples/kmodel/face_detection_320.kmodel",
    "/sdcard/examples/kmodel/face_recognition.kmodel",
    "/sdcard/examples/kmodel/face_recognition_mobile.kmodel",
    "/sdcard/examples/utils/prior_data_320.bin",
]

for path in FILES:
    try:
        print("FILE {} {}".format(os.stat(path)[6], path))
    except BaseException as e:
        print("FILE -1 {} ({})".format(path, e))

DB = "/sdcard/examples/utils/db"
try:
    names = os.listdir(DB)
except BaseException as e:
    print("DB MISSING {} ({})".format(DB, e))
    names = None
if names is not None:
    bins = [n for n in names if n.endswith(".bin")]
    print("DB entries={} bins={} sample={}".format(
        len(names), len(bins), bins[:5]))
    total = 0
    for n in bins[:20]:
        try:
            total += os.stat(DB + "/" + n)[6]
        except BaseException:
            pass
    print("DB first20 bytes={}".format(total))

SYMS = [
    "face_recognition_post_process",
    "face_det_post_process",
    "invert_affine_transform",
]
for name in SYMS:
    print("SYM {} {}".format("yes" if hasattr(aidemo, name) else "no", name))

try:
    print("FS free /sdcard:", os.statvfs("/sdcard")[3] // 1024, "KB blocks free")
except BaseException as e:
    print("FS statvfs error", e)

print("RESULT: DONE")
