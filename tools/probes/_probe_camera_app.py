# Camera app in the real launcher after the window change.
#
# The preview channel now receives an explicit crop, so both the viewfinder and
# the untouched full-resolution capture channel have to be exercised: a preview
# frame rate, a snapshot through the app's own step loop, and one photo written
# to disk.

import gc
import os
import sys
import time

import lvgl as lv

for _k in ("apps.app_camera", "app_camera", "apps", "camera_fov", "audio.shutter"):
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

scr, content, home_btn = launcher._build_shell("Camera", show_clock=False,
                                              immersive=True)
ctx = glb["AppContext"]()
ctx.touch = glb.get("TOUCH_INPUT")
ctx.app_name = "Camera"
# The real launcher hands an immersive app the whole panel, not the content area
ctx.width = glb["DISPLAY_WIDTH"]
ctx.height = glb["DISPLAY_HEIGHT"]
ctx.content_x = 0
ctx.content_y = 0
lv.scr_load(scr)
lv.task_handler()

import apps.app_camera as camera_mod
app = camera_mod.create_app(content, ctx)

t0 = time.ticks_ms()
frames = 0
err = 0
while time.ticks_diff(time.ticks_ms(), t0) < 4000:
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
print("R preview frames={} elapsed={}ms fps={:.1f} errors={}".format(
    frames, elapsed, frames * 1000.0 / elapsed, err))
print("R stat:", app.stat_label.get_text())

attr = app.sensor._chn_attr[2]
print("R preview crop=({},{},{},{}) out={}x{}".format(
    attr.crop_win.h_start, attr.crop_win.v_start,
    attr.crop_win.width, attr.crop_win.height,
    attr.out_win.width, attr.out_win.height))
attr0 = app.sensor._chn_attr[0]
print("R capture crop_en={} out={}x{}".format(
    attr0.crop_enable, attr0.out_win.width, attr0.out_win.height))

before = app.save_num
t1 = time.ticks_ms()
app._capture()
print("R capture {}ms index {}->{}".format(
    time.ticks_diff(time.ticks_ms(), t1), before, app.save_num))
path = "{}/IMG_{:04d}.jpg".format(camera_mod.SAVE_DIR, before)
try:
    print("R file {} bytes".format(os.stat(path)[6]))
except BaseException as e:
    print("E missing photo:", e)
print("R mem_free={}".format(gc.mem_free()))

try:
    app.destroy()
    print("R destroy ok")
except BaseException as e:
    print("R destroy error:", e)
print("R done")
