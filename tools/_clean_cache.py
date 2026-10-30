# Maintenance script: empty the image cache directories.
# Use case: after a byte-order or size logic change the old cache no longer applies
# (the new file names carry _bgr so it would not be read anyway, but it wastes space).
# Also useful for reclaiming space.
# Usage: python tools/_run_remote.py COM56 115200 tools/_clean_cache.py

import os

DIRS = ("/data/cache/thumbs", "/data/cache/full")


def step(msg):
    print("[CLEAN]", msg)


def purge(path):
    try:
        names = os.listdir(path)
    except OSError as e:
        step("{}: {}".format(path, e))
        return 0, 0
    freed = 0
    removed = 0
    for n in names:
        full = path + "/" + n
        try:
            freed += os.stat(full)[6]
            os.remove(full)
            removed += 1
        except OSError as e:
            step("  remove '{}' failed: {}".format(n, e))
    step("{}: removed {} files, freed {:.1f}MB".format(
        path, removed, freed / (1024.0 * 1024.0)))
    return removed, freed


total = 0
for d in DIRS:
    _, f = purge(d)
    total += f
step("total freed {:.1f}MB".format(total / (1024.0 * 1024.0)))

# Report the remaining space
try:
    st = os.statvfs("/data")
    step("/data free = {:.1f}MB".format(st[0] * st[3] / (1024.0 * 1024.0)))
except BaseException as e:
    step("statvfs failed: {}".format(e))

step("done")
