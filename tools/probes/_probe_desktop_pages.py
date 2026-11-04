# Capture desktop page 1 and the Face flyout, to confirm the trim and the
# member-icon cleanup left everything that survives intact.
#
# Run through tools/_run_remote.py with --lvgl.
import os
import sys

sys.path.insert(0, "/sdcard/factory/gui")

try:
    del sys.modules["launcher"]
except KeyError:
    pass

import lvgl as lv
import launcher


def shot(tag):
    import ubinascii
    import uctypes

    dsc = lv.snapshot_take(lv.scr_act(), lv.COLOR_FORMAT.ARGB8888)
    w, h = dsc.header.w, dsc.header.h
    ptr = int.from_bytes(bytes(memoryview(dsc.data)), "little")
    raw = uctypes.bytearray_at(ptr, w * h * 4)
    out = bytearray()
    for y in range(h):
        base = y * w * 4
        run = 0
        pb = pg = pr = -1
        for x in range(w):
            o = base + x * 4
            b = raw[o]
            g = raw[o + 1]
            r = raw[o + 2]
            if b == pb and g == pg and r == pr:
                run += 1
                if run == 255:
                    out.append(254)
                    out.append(pb)
                    out.append(pg)
                    out.append(pr)
                    run = 0
            else:
                if run:
                    out.append(run - 1)
                    out.append(pb)
                    out.append(pg)
                    out.append(pr)
                pb, pg, pr = b, g, r
                run = 1
        if run:
            out.append(run - 1)
            out.append(pb)
            out.append(pg)
            out.append(pr)
    print("SHOT %s %d %d %d %d" % (tag, w, h, len(raw), len(out)))
    b64 = ubinascii.b2a_base64(bytes(out)).decode().strip()
    for i in range(0, len(b64), 800):
        print("B64 " + b64[i:i + 800])
    print("SHOTEND " + tag)
    try:
        dsc.buf_free()
    except Exception:
        pass


from shell import tile_cache
from shell.registry import APPS, FOLDER_MEMBERS

tile_cache.ensure_cache_dir()
parent = lv.obj()
tile_cache.bake_all_tiles(parent)

L = launcher.Launcher()
L.build_home()
for _ in range(3):
    lv.task_handler()
print("HOME_PAGE", L.pager.page if L.pager else 0)
shot("desk1")

# Face flyout: every member icon must still resolve after the cleanup.
face = [m for m in APPS if m["id"] == "face"][0]
L._open_folder(face)
for _ in range(4):
    lv.task_handler()
print("FACE_OPEN", L.folder is not None)
icons = [m["icon"].rsplit("/", 1)[-1] for m in FOLDER_MEMBERS["face"]]
cache = "/sdcard/factory/gui/assets/cache"
for n in icons:
    p = cache + "/member_" + n
    # This firmware has no os.path, so probe with os.stat directly.
    try:
        os.stat(p)
        print("MEMBER", n, "present")
    except BaseException:
        print("MEMBER", n, "MISSING")
shot("facefolder")
print("PROBE_DONE")
