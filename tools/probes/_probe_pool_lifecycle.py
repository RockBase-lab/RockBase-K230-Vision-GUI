# Does the resident pool survive an open/close cycle of every app?
#
# The design assumes the pool is never emptied: apps take a kpu and never release it.
# A pool that silently empties would show up as repeated [POOL] load lines in a run,
# and as each app re-paying the load cost it was supposed to save.
#
# Run with --lvgl. Boot, then open and close each vision/face app, printing the pool
# size after every step.

import gc
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("core", "apps", "core.errors", "ui", "ui.beep", "ui.splash", "launcher",
              "hardware.camera_fov", "core.model_pool", "core.font_pool", "core.ocr_dict",
              "net", "net.service"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv

_src = open("/sdcard/factory/gui/launcher.py").read()
G = {"__name__": "gui_probe"}
exec(compile(_src, "launcher.py", "exec"), G)
G["_ensure_cache_dir"]()

splash = G["splash_ui"].Splash()
G["boot_load"](splash)
mp = G["model_pool"]
print("K0 after boot: pool={}".format(mp.loaded_count()))

launcher = G["Launcher"]()
launcher.build_home()
if launcher.splash is not None:
    splash.delete()

ORDER = ["ocr", "plate", "face_det", "landmark"]
for app_id in ORDER:
    meta = None
    for group in G["FOLDER_MEMBERS"].values():
        for item in group:
            if item["id"] == app_id:
                meta = item
                break
        if meta is not None:
            break
    if meta is None:
        print("K1 {} not found".format(app_id))
        continue

    before = mp.loaded_count()
    t0 = time.ticks_ms()
    launcher.open_app(meta)
    open_ms = time.ticks_diff(time.ticks_ms(), t0)
    after_open = mp.loaded_count()
    app = launcher.active_app
    if app is not None:
        for _ in range(3):
            app.step()
            lv.task_handler()
    t0 = time.ticks_ms()
    launcher.close_app()
    close_ms = time.ticks_diff(time.ticks_ms(), t0)
    after_close = mp.loaded_count()
    print("K1 {:10s} open={:>4d}ms close={:>4d}ms pool {}->{}->{}".format(
        app_id, open_ms, close_ms, before, after_open, after_close))

print("K2 final pool={} models={}".format(
    mp.loaded_count(),
    [p.rsplit("/", 1)[-1] for p in G["BOOT_MODELS"]
     if mp.is_loaded(G["KMODEL_DIR"] + "/" + p[0])]))
print("RESULT: DONE")
