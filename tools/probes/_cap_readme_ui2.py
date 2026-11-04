# Captures the remaining flat screens for the README: Battery and Touch. Both draw
# entirely with LVGL bitmaps, so RLE keeps the transfer small.
#
# Output is SHOT/B64 text; decode with tools/decode_lv_snapshot.py.

import sys
import time
import ubinascii
import uctypes

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("core", "apps", "core.errors", "apps.app_battery", "apps.app_touch",
              "ui.beep", "ui.paging", "hardware.backlight", "launcher", "core.config"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv

_f = open("/sdcard/factory/gui/launcher.py")
_src = _f.read()
_f.close()
del _f

G = {"__name__": "gui_probe"}
exec(compile(_src, "launcher.py", "exec"), G)
# The tables and classes live in shell/ after the layer split; importing them
# here also keeps sys.path bootstrapped by the exec above.
from shell.registry import APPS
from shell.home import Launcher
from shell import tile_cache

tile_cache.ensure_cache_dir()


def shot(tag):
    for _ in range(3):
        lv.task_handler()
        time.sleep_ms(50)
    dsc = lv.snapshot_take(lv.scr_act(), lv.COLOR_FORMAT.ARGB8888)
    w, h = dsc.header.w, dsc.header.h
    ptr = int.from_bytes(bytes(memoryview(dsc.data)), "little")
    raw = uctypes.bytearray_at(ptr, w * h * 4)
    out = bytearray()
    y = 0
    while y < h:
        base = y * w * 4
        run = 1
        b0 = raw[base]
        g0 = raw[base + 1]
        r0 = raw[base + 2]
        for x in range(1, w):
            o = base + x * 4
            b = raw[o]
            g = raw[o + 1]
            r = raw[o + 2]
            if b == b0 and g == g0 and r == r0 and run < 256:
                run += 1
            else:
                out.append(run - 1)
                out.append(b0)
                out.append(g0)
                out.append(r0)
                b0, g0, r0 = b, g, r
                run = 1
        out.append(run - 1)
        out.append(b0)
        out.append(g0)
        out.append(r0)
        y += 1
    dsc.buf_free()
    print("SHOT {} {} {} {} {}".format(tag, w, h, w * h * 4, len(out)))
    b64 = ubinascii.b2a_base64(bytes(out)).decode().strip()
    for i in range(0, len(b64), 800):
        print("B64 " + b64[i:i + 800])
    print("SHOTEND " + tag)
    print("[CAP] {} ok".format(tag))


def top_of(app_id):
    for item in APPS:
        if item["id"] == app_id:
            return item
    return None


launcher = Launcher()
launcher.build_home()

for app_id in ("battery", "touch"):
    meta = top_of(app_id)
    if meta is None:
        print("[CAP] {} missing".format(app_id))
        continue
    try:
        launcher.open_app(meta)
        if launcher.active_app is None:
            print("[CAP] {} showed a panel".format(app_id))
            continue
        # A battery reading only exists after the 500 ms divider settle plus one
        # 320 ms sample window, so run the loop for ~3 s before the snapshot.
        for _ in range(45):
            launcher.active_app.step()
            lv.task_handler()
            time.sleep_ms(60)
        shot("app_" + app_id)
    except BaseException as error:
        print("[CAP] {} failed: {}".format(app_id, error))
    finally:
        try:
            launcher.close_app()
        except BaseException as close_error:
            print("[CAP] {} close failed: {}".format(app_id, close_error))

print("DONE")
