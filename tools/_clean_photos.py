# One-off maintenance script: clean up the old photo directory /sdcard/photos.
#
# Background: photos moved from /sdcard/photos to /data/photos (/data is the larger
# data partition; /sdcard holds only GUI assets). The photos left in the old
# directory have to be removed by hand so they stop occupying the resource
# partition.
#
# Usage (either one):
#   python tools/_run_remote.py COM56 115200 tools/_clean_photos.py
#   or in the board REPL: exec(open('/sdcard/tools/_clean_photos.py').read())
#
# Only .jpg files are deleted, not the directory itself (an empty directory costs no
# space and keeping it avoids deleting unrelated files by mistake).

import os

OLD_DIR = "/sdcard/photos"
NEW_DIR = "/data/photos"


def step(msg):
    print("[CLEAN]", msg)


def list_jpg(path):
    try:
        names = os.listdir(path)
    except OSError as e:
        step("listdir '{}' failed: {}".format(path, e))
        return []
    return [n for n in names if n.lower().endswith(".jpg")]


def clean(path):
    names = list_jpg(path)
    step("{}: {} jpg files".format(path, len(names)))
    freed = 0
    failed = 0
    for n in names:
        full = path + "/" + n
        try:
            size = os.stat(full)[6]
            os.remove(full)
            freed += size
        except OSError as e:
            failed += 1
            if failed <= 3:
                step("  remove '{}' failed: {}".format(n, e))
    step("{}: removed {} files, {} failed, freed {:.1f}MB".format(
        path, len(names) - failed, failed, freed / (1024.0 * 1024.0)))


print("[CLEAN] start")
clean(OLD_DIR)

# Confirm the new directory is usable (the app creates it on the first capture; this
# only prepares it early and reports free space)
cur = ""
for part in NEW_DIR.strip("/").split("/"):
    cur += "/" + part
    try:
        os.mkdir(cur)
    except OSError:
        pass
try:
    st = os.statvfs(NEW_DIR)
    total = st[0] * st[2] / (1024.0 * 1024.0)
    free = st[0] * st[3] / (1024.0 * 1024.0)
    step("{} ready: total={:.1f}MB free={:.1f}MB".format(NEW_DIR, total, free))
except BaseException as e:
    step("{} statvfs failed: {}".format(NEW_DIR, e))

# Also report the remaining /sdcard space so it is easy to tell whether space was
# actually reclaimed
try:
    st = os.statvfs("/sdcard")
    total = st[0] * st[2] / (1024.0 * 1024.0)
    free = st[0] * st[3] / (1024.0 * 1024.0)
    step("/sdcard: total={:.1f}MB free={:.1f}MB".format(total, free))
except BaseException as e:
    step("/sdcard statvfs failed: {}".format(e))

step("done")
