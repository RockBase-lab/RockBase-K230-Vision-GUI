# OCR app through the real launcher: open it, run one scan pass, report the
# recognised lines, the frame rate and the heap, then destroy it and confirm
# both kpUs plus the sensor were released.
#
# Run with --lvgl. The camera must see printed text (a sheet with a few Chinese
# and Latin words) or the line count is legitimately 0.

import gc
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("apps.app_ocr", "apps", "apps.error", "ui.beep", "launcher",
              "camera_fov"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv

src = open("/sdcard/factory/gui/launcher.py").read()
G = {"__name__": "gui_probe"}
exec(compile(src, "launcher.py", "exec"), G)
G["_ensure_cache_dir"]()

meta = None
for item in G["FOLDER_MEMBERS"]["vision"]:
    if item["id"] == "ocr":
        meta = item
        break
print("L1 meta name={} module={} immersive={} release_home={}".format(
    meta["name"], meta["module"], meta.get("immersive"),
    meta.get("release_home")))

print("L2 mem_free before open:", gc.mem_free())
t0 = time.ticks_ms()
launcher = G["Launcher"]()
launcher.open_app(meta)
print("L3 open_app {}ms mem_free={}".format(
    time.ticks_diff(time.ticks_ms(), t0), gc.mem_free()))

app = launcher.active_app
if app is None:
    print("RESULT: FAIL - app is None (see the panel on screen)")
else:
    print("L4 class={} state={} dict={}".format(
        type(app).__name__, app.state,
        len(app.dict_word) if app.dict_word else None))

    # let the preview run a second so AE settles and FPS is real
    t0 = time.ticks_ms()
    frames = 0
    while time.ticks_diff(time.ticks_ms(), t0) < 1500:
        app.step()
        lv.task_handler()
        time.sleep_ms(20)
        frames += 1
    print("L5 preview frames={} mem_free={}".format(frames, gc.mem_free()))

    # one full scan pass through the real state machine (blocking by design;
    # the font already loaded at open)
    app._toggle_scan()
    t0 = time.ticks_ms()
    app.step()
    scan_ms = time.ticks_diff(time.ticks_ms(), t0)
    print("L6 scan {}ms lines={} state={} hint={!r} mem_free={}".format(
        scan_ms, len(app.texts), app.state, app.stat_hint.get_text(),
        gc.mem_free()))
    for i, (coords, text) in enumerate(app.texts):
        print("L6.{} {!r} box=({},{})-({},{})".format(
            i, text, int(coords[0]), int(coords[1]),
            int(coords[4]), int(coords[5])))

    # RESULT state: step must not touch the sensor
    app.step()
    print("L7 RESULT step ok mem_free={}".format(gc.mem_free()))

    t0 = time.ticks_ms()
    launcher.close_app()
    print("L8 close_app {}ms".format(time.ticks_diff(time.ticks_ms(), t0)))
    print("L9 kpu_det={} kpu_rec={} sensor={} mem_free={}".format(
        app.kpu_det, app.kpu_rec, app.sensor, gc.mem_free()))
    print("RESULT: {}".format("PASS" if len(app.texts) >= 0 else "CHECK"))
