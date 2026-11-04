# Capture the progress bar itself at two mid-boot percentages, so the slide is
# verified as rendered pixels rather than as changed numbers.
#
# Hooks Splash._set_pct: when the bar reaches each target percent, snapshot the bar
# strip and RLE-encode it. Decode on the host with
#   python tools/decode_lv_snapshot.py <capture.txt> "tmp/bar_%s.png"
#
# Run with --lvgl. Boot only, no main loop.

import gc
import sys
import ubinascii
import uctypes

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("core", "apps", "core.errors", "ui", "ui.beep", "shell.splash", "launcher",
              "hardware.camera_fov", "core.model_pool", "core.font_pool", "core.ocr_dict",
              "net", "net.service"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv

_src = open("/sdcard/factory/gui/launcher.py").read()
G = {"__name__": "gui_probe"}
exec(compile(_src, "launcher.py", "exec"), G)
# The tables and classes live in shell/ after the layer split; importing them
# here also keeps sys.path bootstrapped by the exec above.
from shell import tile_cache
from shell.boot import boot_load
from shell import splash as splash_ui

tile_cache.ensure_cache_dir()

splash = splash_ui.Splash()

# Bar strip on the 800x480 screen: BAR_W 520 centred, BAR_DY -22 from centre.
CROP_X = 140
CROP_Y = 206
CROP_W = 520
CROP_H = 34
TARGETS = (30, 70)

shots = {}
_orig = splash._set_pct


def _capture(tag):
    dsc = lv.snapshot_take(lv.scr_act(), lv.COLOR_FORMAT.ARGB8888)
    w = dsc.header.w
    h = dsc.header.h
    ptr = int.from_bytes(bytes(memoryview(dsc.data)), "little")
    raw = uctypes.bytearray_at(ptr, w * h * 4)
    out = bytearray()
    for row in range(CROP_H):
        base = (CROP_Y + row) * w * 4
        x = 0
        while x < CROP_W:
            p = base + (CROP_X + x) * 4
            b = raw[p]
            g = raw[p + 1]
            r = raw[p + 2]
            run = 1
            while run < 255 and x + run < CROP_W:
                q = base + (CROP_X + x + run) * 4
                if raw[q] != b or raw[q + 1] != g or raw[q + 2] != r:
                    break
                run += 1
            out.append(run - 1)
            out.append(b)
            out.append(g)
            out.append(r)
            x += run
    del raw
    try:
        dsc.buf_free()
    except BaseException:
        pass
    shots[tag] = (bytes(out), w, h)
    print("S {}% captured{} runs={}".format(
        splash._pct, "", len(out) // 4))


def _spy(value):
    before = splash._pct
    _orig(value)
    if splash._pct == before:
        return
    for target in TARGETS:
        if splash._pct >= target and target not in shots:
            _capture(target)


splash._set_pct = _spy

try:
    boot_load(splash)
except BaseException as error:
    print("A1 boot_load failed: {}".format(error))
    try:
        sys.print_exception(error)
    except BaseException:
        pass

print("A2 final={}% shots={}".format(splash._pct, sorted(shots.keys())))
for target in TARGETS:
    if target not in shots:
        print("A3 {}% MISSING".format(target))
        continue
    data, w, h = shots[target]
    print("SHOT bar{} {} {} {} {}".format(target, CROP_W, CROP_H, w * h * 4,
                                          len(data)))
    b64 = ubinascii.b2a_base64(data).decode().strip()
    for i in range(0, len(b64), 800):
        print("B64 " + b64[i:i + 800])
    print("SHOTEND bar{}".format(target))
    del data

print("RESULT: DONE")
