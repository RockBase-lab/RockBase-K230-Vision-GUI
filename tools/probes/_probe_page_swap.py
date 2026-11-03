# Verify the swapped page assignment and capture both pages.
#
# Run through tools/_run_remote.py with --lvgl.
import sys

sys.path.insert(0, "/sdcard/factory/gui")

try:
    del sys.modules["launcher"]
except KeyError:
    pass

import lvgl as lv
import launcher

ids = [m["id"] for m in launcher.APPS]
print("COUNT", len(ids))
print("PAGE1", ",".join(ids[:8]))
print("PAGE2", ",".join(ids[8:]))
print("NETWORK_P2", "network" in ids[8:])
print("SETTINGS_P2", "settings" in ids[8:])
print("FACE_P1", "face" in ids[:8])
print("IMU_P1", "imu" in ids[:8])

launcher._ensure_cache_dir()
launcher.bake_all_tiles(lv.obj())
print("TILES", len(launcher.BAKED_TILES))

L = launcher.Launcher()
L.build_home()
for _ in range(3):
    lv.task_handler()
print("PAGER", L.pager.page_count if L.pager else 1)


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


shot("swap_p1")

if L.pager is not None:
    L.pager.go(1, animate=False)
    L.home_page = 1
    for _ in range(5):
        lv.task_handler()
    print("PAGE_NOW", L.pager.page)
shot("swap_p2")
print("PROBE_DONE")
