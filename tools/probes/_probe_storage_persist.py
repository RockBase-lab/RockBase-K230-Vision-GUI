# Measures the two candidate locations for the face feature database.
#
# Run 1 with MODE = "write", reboot the board, then run again with
# MODE = "check". Only a check-only run proves persistence: a write+read run
# recreates the marker and would report success either way.
#
# Nothing is loaded and no Sensor is created, so it cannot wedge the board.

import os

MODE = "write"          # "write" | "check"

TARGETS = [
    "/sdcard",
    "/sdcard/examples/utils/db",
    "/data",
    "/data/faces",
]
MARK = "probe_persist_marker.bin"


def free_kb(path):
    try:
        st = os.statvfs(path)
        return st[3] * st[1] // 1024
    except BaseException as e:
        return "err:{}".format(e)


def write_mark(path):
    try:
        os.mkdir(path)
    except BaseException:
        pass
    full = path + "/" + MARK
    try:
        f = open(full, "wb")
        f.write(b"persist-check-8bytes")
        f.close()
        del f
        return True, os.stat(full)[6]
    except BaseException as e:
        return False, str(e)


def read_mark(path):
    full = path + "/" + MARK
    try:
        f = open(full, "rb")
        data = f.read()
        f.close()
        del f
        return "PRESENT len={} {}".format(len(data), data[:12])
    except BaseException as e:
        return "ABSENT ({})".format(e)


if MODE in ("write", "both"):
    for path in TARGETS:
        ok, info = write_mark(path)
        print("W {} write={} info={} free_kb={}".format(path, ok, info,
                                                        free_kb(path)))

print("--- state now ---")
for path in TARGETS:
    print("R {} free_kb={} {}".format(path, free_kb(path), read_mark(path)))
print("RESULT: DONE")
