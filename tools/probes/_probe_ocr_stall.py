# Where the stutter after opening the OCR app comes from: drive the real main
# loop (task_handler then app.step) for 150 frames and print every frame that
# costs more than a threshold, plus the first frames in full.
#
# Run with --lvgl. Camera aimed at text. The point is to separate the open cost
# (font + models, paid before the preview) from per-frame stalls in LIVE.

import gc
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("core", "apps.app_ocr", "apps", "core.errors", "ui.beep", "launcher",
              "hardware.camera_fov"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv

_src = open("/sdcard/factory/gui/launcher.py").read()
G = {"__name__": "gui_probe"}
exec(compile(_src, "launcher.py", "exec"), G)
G["_ensure_cache_dir"]()

FRAMES = 150
SLOW_MS = 40

launcher = G["Launcher"]()
# Real entry path: the home screen exists with its tiles, and open_app destroys
# it (release_home) before the app starts. Skipping this hides the garbage that
# the first gc.collect() inside the app then has to sweep.
t_home = time.ticks_ms()
launcher.build_home()
print("S0 build_home {}ms mem={}".format(
    time.ticks_diff(time.ticks_ms(), t_home), gc.mem_free()))

meta = None
for item in G["FOLDER_MEMBERS"]["vision"]:
    if item["id"] == "ocr":
        meta = item
        break

print("S0b mem before open={}".format(gc.mem_free()))
t_open = time.ticks_ms()
launcher.open_app(meta)
open_ms = time.ticks_diff(time.ticks_ms(), t_open)
app = launcher.active_app
print("S1 open_app {}ms app={} sensor={}".format(
    open_ms, type(app).__name__ if app else None,
    app.sensor is not None if app else None))

if app is None:
    print("RESULT: FAIL - app did not open")
else:
    slow = []
    tasks = []
    steps = []
    # First frames in full: this is where a blocking snapshot or a first full
    # repaint would show up.
    for i in range(FRAMES):
        try:
            m0 = gc.mem_free()
            t0 = time.ticks_ms()
            nw = lv.task_handler()
            dt_task = time.ticks_diff(time.ticks_ms(), t0)
            t1 = time.ticks_ms()
            app.step()
            dt_step = time.ticks_diff(time.ticks_ms(), t1)
            m1 = gc.mem_free()
        except BaseException as error:
            gc.collect()
            print("S2.{} EXC {} mem={}".format(i, error, gc.mem_free()))
            try:
                sys.print_exception(error)
            except BaseException:
                pass
            break
        tasks.append(dt_task)
        steps.append(dt_step)

        if i < 24:
            print("S2.{} task={}ms step={}ms mem={}->{}".format(
                i, dt_task, dt_step, m0, m1))
        if dt_task > SLOW_MS or dt_step > SLOW_MS:
            slow.append((i, dt_task, dt_step))

        # The real loop sleeps until LVGL's next wake point; keep it comparable.
        if nw is None:
            time.sleep_ms(5)
        elif nw >= 0xFFFFFFFF or nw < 0:
            time.sleep_ms(5)
        else:
            time.sleep_ms(max(1, min(nw, 30)))

    tasks.sort()
    steps.sort()
    print("S3 frames={} task max={}ms p90={}ms | step max={}ms p90={}ms".format(
        FRAMES, tasks[-1], tasks[int(FRAMES * 0.9)],
        steps[-1], steps[int(FRAMES * 0.9)]))
    print("S4 slow frames (task or step > {}ms): {}".format(
        SLOW_MS, len(slow)))
    for i, dt_task, dt_step in slow:
        print("S4.{} task={}ms step={}ms".format(i, dt_task, dt_step))
    print("S5 fps_text={} frames={}".format(app.fps_text, app.frame_count))

    launcher.close_app()
    print("RESULT: DONE")
