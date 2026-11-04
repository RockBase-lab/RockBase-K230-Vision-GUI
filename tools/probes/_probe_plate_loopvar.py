# Compares three loop structures for the Plate app, so a frame-rate decision is
# made on measurement.
#
# The measured per-frame cost is detection 68 ms, LVGL task 22 ms, recognition
# 6 ms, everything else ~2.5 ms, i.e. 101 ms. Detection dominates, and it does not
# depend on what the loop does between runs. The question this answers is whether
# decoupling the preview from detection raises the preview rate usefully.
#
# Variants, each run for the same number of iterations:
#   V1  detect every iteration (what the app does now)
#   V2  detect every second iteration
#   V3  preview only, no detection (the ceiling for this app)
#
# V2 keeps the plate reading update at half the iteration rate, which is still
# several readings per second, and doubles the number of preview refreshes.
#
# Run with --lvgl and a plate in front of the camera.

import gc
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("core", "apps.app_plate", "apps", "core.errors", "ui.beep", "launcher",
              "hardware.camera_fov"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv
from media.sensor import CAM_CHN_ID_1, CAM_CHN_ID_2

_f = open("/sdcard/factory/gui/launcher.py")
gui_src = _f.read()
_f.close()
del _f

G = {"__name__": "gui_probe"}
exec(compile(gui_src, "launcher.py", "exec"), G)
# The tables and classes live in shell/ after the layer split; importing them
# here also keeps sys.path bootstrapped by the exec above.
from shell.registry import FOLDER_MEMBERS
from shell.home import Launcher
from shell import tile_cache

tile_cache.ensure_cache_dir()

launcher = Launcher()
meta = None
for item in FOLDER_MEMBERS["vision"]:
    if item["id"] == "plate":
        meta = item
        break

launcher.open_app(meta)
app = launcher.active_app
if app is None:
    print("RESULT: FAIL - app is None")
else:
    N = 60
    results = []

    def run_variant(label, detect_every):
        frames = 0
        detect_runs = 0
        error = None
        t0 = time.ticks_us()
        while frames < N:
            try:
                img = app.sensor.snapshot(chn=CAM_CHN_ID_2)
                if detect_every and (frames % detect_every == 0):
                    ai_img = app.sensor.snapshot(chn=CAM_CHN_ID_1)
                    quads = app._detect(ai_img)
                    detect_runs += 1
                    if quads:
                        chw = ai_img.to_numpy_ref()
                        text = app._recognize(chw, quads[0])
                        del chw
                        if text is not None:
                            app._vote(0, text)
                    app.plate_count = len(quads)
                    app._show_plates(quads)
                    del ai_img, quads
                img.as_lvgl_img_src(app.img_widget)
                app.img_widget.invalidate()
                del img
                lv.task_handler()
                frames += 1
            except BaseException as exc:
                error = exc
                break
        elapsed = time.ticks_diff(time.ticks_us(), t0) / 1000.0
        ms_per_frame = elapsed / frames if frames else 0.0
        results.append((label, frames, elapsed, ms_per_frame,
                        detect_runs, error))
        print("{}: frames={} elapsed={:.0f}ms per_frame={:.1f}ms fps={:.1f} detect_runs={}".format(
            label, frames, elapsed, ms_per_frame,
            frames * 1000.0 / elapsed if elapsed else 0.0, detect_runs))

    print("V1 detect every frame, N={} ...".format(N))
    run_variant("V1", 1)
    print("V2 detect every 2nd frame ...")
    run_variant("V2", 2)
    print("V3 preview only ...")
    run_variant("V3", 0)

    print("")
    print("===== result")
    base = None
    for label, frames, elapsed, ms, runs, error in results:
        if error is not None:
            print("  {} stopped early: {}".format(label, error))
        if base is None and ms:
            base = ms
        gain = (base / ms) if (base and ms) else 0.0
        print("  {:3s} per_frame={:6.1f} ms  fps={:5.1f}  speedup_x={:.2f}".format(
            label, ms, (frames * 1000.0 / elapsed) if elapsed else 0.0, gain))
    print("  texts={} plates={}".format(app._texts, app.plate_count))
    print("  mem_free={}".format(gc.mem_free()))

    try:
        launcher.close_app()
        print("closed")
    except BaseException as error:
        print("close error:", error)

print("RESULT: DONE")
