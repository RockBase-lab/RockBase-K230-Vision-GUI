# Does the boot progress bar interpolate, and what does the motion cost?
#
# Wraps Splash._set_pct to record (timestamp, percent) for every value actually
# pushed to the bar, then runs boot_load. A jump shows up as one large delta; a
# smooth slide shows up as several small deltas a few milliseconds apart.
#
# Run with --lvgl. Boot only, no main loop.

import gc
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("apps", "apps.error", "ui", "ui.beep", "ui.splash", "launcher",
              "camera_fov", "model_pool", "font_pool", "ocr_dict",
              "net", "net.service"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv

_src = open("/sdcard/factory/gui/launcher.py").read()
G = {"__name__": "gui_probe"}
exec(compile(_src, "launcher.py", "exec"), G)
G["_ensure_cache_dir"]()

splash = G["splash_ui"].Splash()

# Record every value that reaches the bar, with the time it happened.
trace = []
_orig = splash._set_pct


def _spy(value):
    before = splash._pct
    _orig(value)
    if splash._pct != before:
        trace.append((time.ticks_ms(), splash._pct))


splash._set_pct = _spy

t0 = time.ticks_ms()
try:
    G["boot_load"](splash)
except BaseException as error:
    print("A1 boot_load failed: {}".format(error))
    try:
        sys.print_exception(error)
    except BaseException:
        pass

boot_ms = time.ticks_diff(time.ticks_ms(), t0)
print("A1 boot {}ms ({}.{} s) mem_free={}KB".format(
    boot_ms, boot_ms // 1000, (boot_ms % 1000) // 100, gc.mem_free() // 1024))

# Per-transition summary: how many intermediate frames each jump produced, and how
# long its motion took. One frame = a hard jump.
print("A2 transitions={}".format(len(trace)))
prev_pct = None
prev_ms = None
jumps = 0
motion_ms = 0
for ms, pct in trace:
    if prev_pct is not None:
        delta_pct = pct - prev_pct
        gap = time.ticks_diff(ms, prev_ms)
        if gap > 30:
            jumps += 1
        else:
            motion_ms += gap
        print("A3 {:>3}% ({:+d}) after {}ms".format(pct, delta_pct, gap))
    else:
        print("A3 {:>3}% (start)".format(pct))
    prev_pct = pct
    prev_ms = ms

print("A4 final={}% frames={} hard_jumps={} motion={}ms".format(
    splash._pct, len(trace), jumps, motion_ms))
print("RESULT: DONE")
