# Captures the Vision folder flyout, which is where the Plate icon is actually
# seen: member tiles are drawn at runtime from cache/member_icon_*.png, not from
# the baked top-level tiles.
#
# Crops to the folder panel so the capture compresses (a full frame does not), and
# the panel is where the icon change has to be judged.
#
# Run with --lvgl. The body is wrapped in try/finally so a failure cannot leave the
# folder layer open.

import gc
import sys
import ubinascii
import uctypes

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("core", "launcher", "ui.paging", "ui.beep", "net.service", "hardware.battery",
              "core.errors", "hardware.camera_fov"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv

_f = open("/sdcard/factory/gui/launcher.py")
gui_src = _f.read()
_f.close()
del _f

G = {"__name__": "gui_probe"}
exec(compile(gui_src, "launcher.py", "exec"), G)
# The tables and classes live in shell/ after the layer split; importing them
# here also keeps sys.path bootstrapped by the exec above.
from shell.registry import APPS
from shell.home import Launcher
from shell import tile_cache

tile_cache.ensure_cache_dir()

launcher = Launcher()
# _open_folder attaches the flyout to home_scr, which only exists after the home
# screen is built. Without this the call returns immediately and the capture shows
# an empty screen.
launcher.build_home()
meta = None
for item in APPS:
    if item["id"] == "vision":
        meta = item
        break

try:
    launcher._open_folder(meta)
    print("F1 folder opened, home_scr={}".format(launcher.home_scr is not None))
    for _ in range(6):
        lv.task_handler()
    lv.refr_now(lv.disp_get_default())

    # Panel geometry from _open_folder: 3 columns x 3 rows of 112 px cells with a
    # 12 px gap and a 76 px title strip, centred on 800x480. Vision has 9 members,
    # so the panel is 392x436 at (204, 22); the crop adds a margin around it.
    cx = 196
    cy = 14
    box_w = 408
    box_h = 452
    print("F2 crop region ({},{}) {}x{}".format(cx, cy, box_w, box_h))

    dsc = lv.snapshot_take(lv.scr_act(), lv.COLOR_FORMAT.ARGB8888)
    w = dsc.header.w
    h = dsc.header.h
    print("F3 snapshot {}x{}".format(w, h))
    ptr = int.from_bytes(bytes(memoryview(dsc.data)), "little")
    raw = uctypes.bytearray_at(ptr, w * h * 4)

    out = bytearray()
    for row in range(box_h):
        y = cy + row
        base = y * w * 4
        x = 0
        while x < box_w:
            p = base + (cx + x) * 4
            b = raw[p]
            g = raw[p + 1]
            r = raw[p + 2]
            run = 1
            while run < 255 and x + run < box_w:
                q = base + (cx + x + run) * 4
                if raw[q] != b or raw[q + 1] != g or raw[q + 2] != r:
                    break
                run += 1
            out.append(run - 1)
            out.append(b)
            out.append(g)
            out.append(r)
            x += run

    print("SHOT folder %d %d %d %d" % (box_w, box_h, len(raw), len(out)))
    b64 = ubinascii.b2a_base64(bytes(out)).decode().strip()
    for i in range(0, len(b64), 800):
        print("B64 " + b64[i:i + 800])
    print("SHOTEND folder")
    del raw, out, dsc
    gc.collect()
finally:
    try:
        launcher._close_folder()
        print("F4 folder closed")
    except BaseException as error:
        print("F4 close failed:", error)

print("RESULT: DONE")
