# Runs the Plate app through the real launcher and verifies both halves of it.
#
# Part A feeds the known photo (/data/plate/real.jpg, plate 渝A999U9) through the
# app's own _detect/_recognize, so the inference chain is checked against a frame
# with a known answer instead of depending on what the camera happens to see.
# Part B runs the real step() loop against the camera and reports what the bar
# says, the frame rate and the heap. Part C closes the app and checks that both
# kpu handles and the sensor were released.
#
# Run with --lvgl: the app creates lv objects.
#
# Note on part A: _detect() expects an object with to_numpy_ref() returning CHW,
# which is what the sensor's RGBP888 channel gives. A JPEG on disk gives HWC after
# to_rgb888(), so a tiny adapter presents the converted array in the same shape
# rather than changing the app's contract for the sake of a probe.

import gc
import image
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("core", "apps.app_plate", "apps", "core.errors", "ui.beep", "launcher",
              "hardware.camera_fov"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv
import ulab.numpy as np

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
print("L1 meta name={} module={} immersive={} release_home={}".format(
    meta["name"], meta["module"], meta.get("immersive"),
    meta.get("release_home")))

print("L1b mem_free before open:", gc.mem_free())
t0 = time.ticks_ms()
launcher.open_app(meta)
print("L2 open_app {}ms mem_free={}".format(
    time.ticks_diff(time.ticks_ms(), t0), gc.mem_free()))

app = launcher.active_app
if app is None:
    print("RESULT: FAIL - app is None (see the panel on screen)")

else:
    print("L3 class={} boxes={} captions={} plates={}".format(
        type(app).__name__, len(app.boxes), len(app.captions), len(app._texts)))
    print("L3b kpu_det={} kpu_rec={} ai2d_det={}".format(
        app.kpu_det is not None, app.kpu_rec is not None,
        app.ai2d_det is not None))

    for _ in range(2):
        lv.task_handler()
        time.sleep_ms(60)


    # ---------------- Part A: known photos through the app's own methods ------
    class _Chw:
        """Presents an ndarray as the only thing _detect needs from a frame."""

        def __init__(self, arr):
            self.arr = arr

        def to_numpy_ref(self):
            return self.arr

    PHOTOS = [
        ("real", "/data/plate/real.jpg"),
        ("small", "/data/plate/small.jpg"),
        ("base", "/data/plate/var_base.jpg"),
        ("tiny", "/data/plate/var_tiny.jpg"),
        ("blur", "/data/plate/var_blur.jpg"),
        ("cropped", "/data/plate/var_cropped.jpg"),
        ("shifted", "/data/plate/var_shifted.jpg"),
    ]
    print("A1 running {} photos through _detect/_recognize ...".format(
        len(PHOTOS)))
    hits = 0
    for tag, path in PHOTOS:
        try:
            src = image.Image(path).to_rgb888()
            sw = src.width()
            sh = src.height()
            ref = src.to_numpy_ref()
            chw = np.zeros((3, sh, sw), dtype=np.uint8)
            chw[0] = ref[:, :, 0]
            chw[1] = ref[:, :, 1]
            chw[2] = ref[:, :, 2]
            del ref, src

            t0 = time.ticks_ms()
            quads = app._detect(_Chw(chw))
            det_ms = time.ticks_diff(time.ticks_ms(), t0)
            if not quads:
                print("A2 {:8s} detect {}ms -> no plate".format(tag, det_ms))
                del chw
                continue
            t0 = time.ticks_ms()
            text = app._recognize(chw, quads[0])
            rec_ms = time.ticks_diff(time.ticks_ms(), t0)
            q = quads[0]
            ok = text == "渝A999U9"
            if ok:
                hits += 1
            print("A2 {:8s} det={}ms rec={}ms quad=({},{})-({},{}) -> {!r} {}".format(
                tag, det_ms, rec_ms, int(q[0]), int(q[1]), int(q[4]),
                int(q[5]), text, "MATCH" if ok else ""))
            del chw, quads, text
            gc.collect()
        except BaseException as error:
            print("A {:8s} FAILED: {}".format(tag, error))
            sys.print_exception(error)
    print("A3 score {}/{}   mem_free={}".format(hits, len(PHOTOS), gc.mem_free()))

    # ---------------- Part A2: the vote over repeated readings ----------
    # Feed the same photo several times and alternate a deliberately wrong
    # reading, to confirm a minority cannot take the label.
    mod = sys.modules["apps.app_plate"]
    print("A4 vote check (window={} min={}) ...".format(
        mod.VOTE_WINDOW, mod.VOTE_MIN))
    app._reset_votes()
    for rnd in range(mod.VOTE_WINDOW):
        app._vote(0, "渝A999U9")
    print("A5 after {} good votes  text={!r}".format(
        mod.VOTE_WINDOW, app._texts[0]))
    for rnd in range(2):
        app._vote(0, "川A999U9")
    print("A6 after 2 bad votes   text={!r} (must stay 渝A999U9)".format(
        app._texts[0]))
    app._reset_votes()
    app._vote(0, "x")
    print("A7 single-char reading text={!r} (must stay empty)".format(app._texts[0]))
    app._reset_votes()

    # ---------------- Part B: real camera loop ----------------
    print("B1 running the real loop for 4 s ...")
    t0 = time.ticks_ms()
    frames = 0
    errors = 0
    max_step = 0.0
    while time.ticks_diff(time.ticks_ms(), t0) < 4000:
        t1 = time.ticks_us()
        try:
            app.step()
            frames += 1
        except BaseException as error:
            errors += 1
            print("E step error:", error)
            sys.print_exception(error)
            if errors > 3:
                break
        step_ms = time.ticks_diff(time.ticks_us(), t1) / 1000.0
        if step_ms > max_step:
            max_step = step_ms
        lv.task_handler()
    elapsed = time.ticks_diff(time.ticks_ms(), t0)
    print("B2 frames={} elapsed={}ms loop_fps={:.1f} max_step={:.1f}ms errors={}".format(
        frames, elapsed, frames * 1000.0 / elapsed if elapsed else 0.0,
        max_step, errors))
    print("B3 bar: fps='{}' count='{}' hint='{}'".format(
        app.stat_fps.get_text(), app.stat_count.get_text(),
        app.stat_hint.get_text()))
    print("B4 app: plates={} infer={}ms texts={} mem_free={}".format(
        app.plate_count, app.infer_ms, app._texts, gc.mem_free()))

    # Boxes on screen must agree with the app's own plate count.
    shown = 0
    for box in app.boxes:
        if not box.has_flag(lv.obj.FLAG.HIDDEN):
            shown += 1
    print("B5 boxes shown={} (plates={})".format(shown, app.plate_count))

    # ---------------- Part C: teardown ----------------
    try:
        launcher.close_app()
        print("C1 close ok")
    except BaseException as error:
        print("C1 close error:", error)
        sys.print_exception(error)
    # destroy() must have dropped every native handle it owns.
    print("C2 released: kpu_det={} kpu_rec={} ai2d_det={} ai2d_rec={} sensor={}".format(
        app.kpu_det is None, app.kpu_rec is None, app.ai2d_det is None,
        app.ai2d_rec is None, app.sensor is None))
    print("C3 mem_free after close: {}".format(gc.mem_free()))

print("RESULT: DONE")
