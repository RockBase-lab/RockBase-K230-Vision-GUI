# Faces app in the real launcher after the window change.
#
# Both channels now receive the wide explicit window, so this checks that the
# RetinaFace chain still detects and that the frame rate holds with the 2.25:1
# ISP scale. Prints the app's own status segments.

import gc
import sys
import time

import lvgl as lv

for _k in ("apps.app_face_det", "app_face_det", "apps", "hardware.camera_fov"):
    if _k in sys.modules:
        del sys.modules[_k]

src = open("/sdcard/factory/gui/launcher.py").read()
glb = {"__name__": "gui_mod"}
try:
    exec(compile(src, "/sdcard/factory/gui/launcher.py", "exec"), glb)
except SystemExit:
    pass

# These imports must follow the exec above: it is what puts the GUI root on
# sys.path. Symbols are no longer re-exported through the launcher's globals.
from hardware import panel
from shell import tile_cache
from shell import touchscreen
from shell.context import AppContext
from shell.home import Launcher

tile_cache.ensure_cache_dir()
launcher = Launcher()

scr, content, home_btn = launcher._build_shell("Faces", show_clock=False,
                                              immersive=True)
ctx = AppContext()
ctx.touch = touchscreen.instance()
ctx.app_name = "Faces"
ctx.width = panel.WIDTH
ctx.height = panel.HEIGHT
ctx.content_x = 0
ctx.content_y = 0
lv.scr_load(scr)
lv.task_handler()

import apps.app_face_det as face_mod
app = face_mod.create_app(content, ctx)

attr = app.sensor._chn_attr[1]
print("R ai crop=({},{},{},{}) out={}x{}".format(
    attr.crop_win.h_start, attr.crop_win.v_start,
    attr.crop_win.width, attr.crop_win.height,
    attr.out_win.width, attr.out_win.height))

t0 = time.ticks_ms()
frames = 0
err = 0
faces = 0
while time.ticks_diff(time.ticks_ms(), t0) < 6000:
    try:
        app.step()
        frames += 1
        faces = max(faces, app.last_faces)
    except BaseException as e:
        err += 1
        print("E step error:", e)
        if err > 3:
            break
    lv.task_handler()
elapsed = time.ticks_diff(time.ticks_ms(), t0)

print("R frames={} elapsed={}ms loop_fps={:.1f} errors={} max_faces={}".format(
    frames, elapsed, frames * 1000.0 / elapsed, err, faces))
print("R seg:", app.seg_count.get_text(), "|", app.seg_fps.get_text())
print("R mem_free={}".format(gc.mem_free()))

try:
    app.destroy()
    print("R destroy ok")
except BaseException as e:
    print("R destroy error:", e)
print("R done")
