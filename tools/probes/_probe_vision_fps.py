# Run the Vision app inside the real launcher and report its frame rate.
#
# The preview window moved from the firmware's crop=True (1600x960) to an
# explicit 1800x1080, so the ISP now scales 2.25:1 instead of 2:1. Output size
# and every coordinate are unchanged, but the scaling path is new, so the frame
# rate has to be measured rather than assumed.
#
# Prints the app's own top-bar statistics, which are the numbers a user sees.

import gc
import sys
import time

import lvgl as lv

for _k in ("apps.app_vision", "app_vision", "apps", "camera_fov"):
    if _k in sys.modules:
        del sys.modules[_k]

src = open("/sdcard/factory/gui/launcher.py").read()
glb = {"__name__": "gui_mod"}
try:
    exec(compile(src, "/sdcard/factory/gui/launcher.py", "exec"), glb)
except SystemExit:
    pass

launcher = glb["Launcher"]()
for fn in ("_ensure_cache_dir", "_init_cache_dir"):
    f = getattr(launcher, fn, None)
    if f is not None:
        try:
            f()
        except BaseException as e:
            print("I", fn, "failed:", e)

scr, content, home_btn = launcher._build_shell("Vision", show_clock=True)
ctx = glb["AppContext"]()
ctx.touch = glb.get("TOUCH_INPUT")
ctx.app_name = "Vision"
lv.scr_load(scr)
lv.task_handler()

import apps.app_vision as vision_mod
app = vision_mod.create_app(content, ctx)

t0 = time.ticks_ms()
frames = 0
err = 0
while time.ticks_diff(time.ticks_ms(), t0) < 6000:
    try:
        app.step()
        frames += 1
    except BaseException as e:
        err += 1
        print("E step error:", e)
        if err > 3:
            break
    lv.task_handler()
elapsed = time.ticks_diff(time.ticks_ms(), t0)

print("R frames={} elapsed={}ms loop_fps={:.1f} errors={} mem_free={}".format(
    frames, elapsed, frames * 1000.0 / elapsed, err, gc.mem_free()))
try:
    print("R stat:", app.stat_label.get_text())
except BaseException as e:
    print("R stat unavailable:", e)
try:
    print("R boxes:", len(app.boxes) if hasattr(app, "boxes") else "-")
except BaseException as e:
    print("R boxes unavailable:", e)

try:
    app.destroy()
    print("R destroy ok")
except BaseException as e:
    print("R destroy error:", e)
print("R done")
