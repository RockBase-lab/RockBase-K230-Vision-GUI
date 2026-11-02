# Breaks down the Plate app's per-frame cost, including the parts its own
# infer_ms does not cover.
#
# infer_ms measures detection + crop + recognition, but the loop runs at ~10 fps,
# i.e. ~100 ms per iteration, so about 20-25 ms is spent outside that window:
# two snapshots, the LVGL task handler, the overlay update and the periodic
# gc.collect. This probe times every segment separately over many frames, so an
# optimisation targets the real cost instead of a guess.
#
# Segments:
#   snap_ai   snapshot of chn1 (the AI frame)
#   snap_disp snapshot of chn2 (the preview frame)
#   detect    ai2d + kpu + post-process inside the app
#   preview   as_lvgl_img_src + invalidate
#   rec       ocr_rec_preprocess + ai2d + kpu + decode
#   overlay   _show_plates (lv.line set_points + caption updates)
#   task      lv.task_handler
#   gc        the every-8-frames collect
#
# Run with --lvgl and a plate in front of the camera.

import gc
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("apps.app_plate", "apps", "apps.error", "ui.beep", "launcher",
              "camera_fov"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv

_f = open("/sdcard/factory/gui/launcher.py")
gui_src = _f.read()
_f.close()
del _f

G = {"__name__": "gui_probe"}
exec(compile(gui_src, "launcher.py", "exec"), G)
G["_ensure_cache_dir"]()

launcher = G["Launcher"]()
meta = None
for item in G["FOLDER_MEMBERS"]["vision"]:
    if item["id"] == "plate":
        meta = item
        break

launcher.open_app(meta)
app = launcher.active_app
if app is None:
    print("RESULT: FAIL - app is None")
else:
    from media.sensor import CAM_CHN_ID_1, CAM_CHN_ID_2

    # Replace the app's own step() with an instrumented equivalent. The segment
    # boundaries match step() exactly, so the numbers add up to the loop time.
    stats = {}


    def add(key, ms):
        entry = stats.get(key)
        if entry is None:
            stats[key] = [ms, 1]
        else:
            entry[0] += ms
            entry[1] += 1


    def timed(key, fn, *args, **kwargs):
        t0 = time.ticks_us()
        result = fn(*args, **kwargs)
        add(key, time.ticks_diff(time.ticks_us(), t0) / 1000.0)
        return result


    N = 120
    print("measuring {} frames, hold a plate in view ...".format(N))
    t_loop0 = time.ticks_us()
    frames = 0
    errors = 0
    while frames < N:
        t0 = time.ticks_us()
        try:
            ai_img = timed("snap_ai", app.sensor.snapshot, **{"chn": CAM_CHN_ID_1})
            img = timed("snap_disp", app.sensor.snapshot, **{"chn": CAM_CHN_ID_2})
        except BaseException as error:
            errors += 1
            print("snapshot error:", error)
            if errors > 3:
                break
            continue

        quads = []
        try:
            quads = timed("detect", app._detect, ai_img)
        except BaseException as error:
            print("detect error:", error)

        def preview():
            img.as_lvgl_img_src(app.img_widget)
            app.img_widget.invalidate()

        timed("preview", preview)

        if quads:
            slot = 0
            try:
                chw = ai_img.to_numpy_ref()
                text = timed("rec", app._recognize, chw, quads[slot])
                del chw
                if text is not None:
                    app._vote(slot, text)
            except BaseException as error:
                print("rec error:", error)

        app.plate_count = len(quads)
        timed("overlay", app._show_plates, quads)
        del quads

        app._gc_tick += 1
        if app._gc_tick >= 8:
            app._gc_tick = 0
            timed("gc", gc.collect)

        timed("task", lv.task_handler)
        frames += 1
        add("loop", time.ticks_diff(time.ticks_us(), t0) / 1000.0)

    elapsed = time.ticks_diff(time.ticks_us(), t_loop0) / 1000.0
    print("")
    print("===== result  frames={} elapsed={:.0f}ms loop_fps={:.2f}".format(
        frames, elapsed, frames * 1000.0 / elapsed if elapsed else 0.0))
    keys = ["snap_ai", "snap_disp", "detect", "preview", "rec", "overlay",
            "task", "gc", "loop"]
    total_loop = 0.0
    loop_entry = stats.get("loop")
    loop_avg = loop_entry[0] / loop_entry[1] if loop_entry else 0.0
    for key in keys:
        entry = stats.get(key)
        if entry is None:
            print("  {:10s} (never ran)".format(key))
            continue
        sum_ms, count = entry
        avg = sum_ms / count
        per_frame = sum_ms / frames if frames else 0.0
        print("  {:10s} n={:4d}  avg={:6.2f} ms  per-frame={:6.2f} ms".format(
            key, count, avg, per_frame))
        if key != "loop":
            total_loop += per_frame
    print("  {:10s} {} ms accounted, {} ms in loop".format(
        "accounted", round(total_loop, 1), round(loop_avg, 1)))
    print("  fps_text={} plates={} texts={}".format(
        app.fps_text, app.plate_count, app._texts))

    try:
        launcher.close_app()
        print("closed, mem_free={}".format(gc.mem_free()))
    except BaseException as error:
        print("close error:", error)

print("RESULT: DONE")
