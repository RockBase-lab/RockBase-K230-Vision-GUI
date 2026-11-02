# Why a 12-line poem yields 8 boxes: print the deployed MAX_BOXES, the raw
# ocr_post_process output count, and every box's size plus whether it survives
# the app's filter. Run with --lvgl, camera aimed at the text.

import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
# The serial REPL is persistent: a cached module would shadow the deployed file.
for _name in ("apps.app_ocr", "apps", "apps.error", "ui.beep", "launcher",
              "camera_fov"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv

src = open("/sdcard/factory/gui/launcher.py").read()
G = {"__name__": "ocr_boxes"}
exec(compile(src, "launcher.py", "exec"), G)
G["_ensure_cache_dir"]()

meta = None
for item in G["FOLDER_MEMBERS"]["vision"]:
    if item["id"] == "ocr":
        meta = item
        break

launcher = G["Launcher"]()
launcher.open_app(meta)
app = launcher.active_app
print("B1 MAX_BOXES={} MIN_H={} MIN_W={}".format(
    sys.modules["apps.app_ocr"].MAX_BOXES,
    sys.modules["apps.app_ocr"].MIN_BOX_H,
    sys.modules["apps.app_ocr"].MIN_BOX_W))

t0 = time.ticks_ms()
while time.ticks_diff(time.ticks_ms(), t0) < 1200:
    app.step()
    lv.task_handler()
    time.sleep_ms(20)

# Replicate _detect but keep the unfiltered list.
import aicube
from apps.app_ocr import (_chw2hwc, MASK_THR, BOX_THR, DET_IN,
                          PREVIEW_W, PREVIEW_H, MAX_BOXES,
                          MIN_BOX_H, MIN_BOX_W)

img = app.sensor.snapshot(chn=1)
t0 = time.ticks_ms()
src_np = img.to_numpy_ref()
tin = app.ai2d_det.run(src_np)
app.kpu_det.set_input_tensor(0, tin)
app.kpu_det.run()
ot = app.kpu_det.get_output_tensor(0)
try:
    raw = ot.to_numpy()
finally:
    ot.release()
hwc = _chw2hwc(src_np)
boxes = aicube.ocr_post_process(raw[:, :, :, 0].reshape(-1), hwc.reshape(-1),
                                DET_IN, [PREVIEW_W, PREVIEW_H],
                                MASK_THR, BOX_THR)
print("B2 det {}ms raw_boxes={}".format(
    time.ticks_diff(time.ticks_ms(), t0), len(boxes) if boxes else 0))

passed = 0
if boxes:
    for i, box in enumerate(boxes):
        c = box[1]
        xs = [c[0], c[2], c[4], c[6]]
        ys = [c[1], c[3], c[5], c[7]]
        w = max(xs) - min(xs)
        h = max(ys) - min(ys)
        ok = h >= MIN_BOX_H and w >= MIN_BOX_W
        if ok:
            passed += 1
        print("B3.{} y={} x={} w={} h={} keep={}".format(
            i, int(min(ys)), int(min(xs)), int(w), int(h), ok))
print("B4 passed_filter={} cap={}".format(passed, MAX_BOXES))

del raw, hwc, src_np, tin
launcher.close_app()
print("RESULT: DONE")
