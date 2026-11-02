# Measures how stable the plate reading is over time, which is what the user
# reported as the problem: digits correct, province character varying between
# frames (渝 / 川 / 浙).
#
# Runs the real app loop for 20 s and records every raw reading plus every label
# the app actually displayed. The two numbers that matter:
#   - raw reading distribution: how often a single frame is wrong
#   - displayed label changes: how often the user sees it change
# A voting window that works shows a long tail of wrong raw readings with a
# displayed label that never moves.
#
# Point the camera at the plate first. Run with --lvgl.

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
    # Wrap _vote so every raw reading and every label change is visible.
    raw_counts = {}
    raw_total = 0
    label_changes = 0
    last_label = [""]

    original_vote = app._vote

    def traced_vote(slot, text):
        global raw_total, label_changes
        raw_total += 1
        raw_counts[text] = raw_counts.get(text, 0) + 1
        before = app._texts[slot]
        original_vote(slot, text)
        after = app._texts[slot]
        if after != before and after:
            label_changes += 1
            print("  label -> {!r} (raw count so far {})".format(after, raw_total))

    app._vote = traced_vote

    print("hold the plate in view for 20 s ...")
    t0 = time.ticks_ms()
    frames = 0
    errors = 0
    while time.ticks_diff(time.ticks_ms(), t0) < 20000:
        try:
            app.step()
            frames += 1
        except BaseException as error:
            errors += 1
            print("E step error:", error)
            if errors > 5:
                break
        lv.task_handler()

    elapsed = time.ticks_diff(time.ticks_ms(), t0)
    print("")
    print("===== result")
    print("frames={} elapsed={}ms fps={:.1f} errors={}".format(
        frames, elapsed, frames * 1000.0 / elapsed if elapsed else 0.0, errors))
    print("raw readings={} distinct={}".format(raw_total, len(raw_counts)))
    for text in sorted(raw_counts, key=lambda k: -raw_counts[k]):
        print("  raw {!r} x{}".format(text, raw_counts[text]))
    print("label changes={} final={!r}".format(label_changes, app._texts))

    try:
        launcher.close_app()
        print("close ok, mem_free={}".format(gc.mem_free()))
    except BaseException as error:
        print("close error:", error)

print("RESULT: DONE")
