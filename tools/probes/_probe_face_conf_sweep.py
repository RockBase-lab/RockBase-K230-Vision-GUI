# Sweeps the face-detection confidence threshold over ONE captured frame.
#
# Re-runs aidemo.face_det_post_process on the same 9 output tensors with a range
# of conf values, so the box count change is caused by the threshold alone and
# not by frame-to-frame variation. Run with --lvgl and a face in front of the
# camera.

import gc
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("core", "apps.app_face_det", "apps", "core.errors", "ui.beep", "launcher"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv
import ulab.numpy as np
import aidemo
from media.sensor import CAM_CHN_ID_1
from apps.app_face_det import rgb_param, MODEL_IN

_f = open("/sdcard/factory/gui/launcher.py")
gui_src = _f.read()
_f.close()
del _f

G = {"__name__": "gui_probe"}
exec(compile(gui_src, "launcher.py", "exec"), G)
G["_ensure_cache_dir"]()

launcher = G["Launcher"]()
meta = None
for item in G["FOLDER_MEMBERS"]["face"]:
    if item["id"] == "face_det":
        meta = item
        break
launcher.open_app(meta)
app = launcher.active_app
print("L1 app up, th_idx={} rgb_param={} anchors={}".format(
    app.th_idx, rgb_param(), list(app.anchors.shape)))

print("L2 hold still, a face in front of the camera, 6 s ...")
for s in range(6, 0, -1):
    print("   ", s)
    time.sleep_ms(1000)

SCAN = [0.02, 0.05, 0.10, 0.20, 0.25, 0.30, 0.40, 0.50, 0.60,
        0.70, 0.80, 0.85, 0.90, 0.93, 0.95, 0.97, 0.98, 0.99]


def capture():
    """One frame through ai2d + kpu. Returns the 9 numpy outputs."""
    img = app.sensor.snapshot(chn=CAM_CHN_ID_1)
    tin = app.ai2d.run(img.to_numpy_ref())
    app.kpu.set_input_tensor(0, tin)
    app.kpu.run()
    outs = []
    for i in range(app.kpu.outputs_size()):
        ot = app.kpu.get_output_tensor(i)
        try:
            outs.append(ot.to_numpy())
        finally:
            ot.release()
    del tin, img
    return outs


def count_at(outs, conf):
    res = aidemo.face_det_post_process(conf, 0.2, MODEL_IN[0], app.anchors,
                                       rgb_param(), outs)
    if len(res) == 0:
        return 0
    return len(res[0])


print("L3 capturing a frame ...")
outs = capture()
print("L4 tensors={} shapes={}".format(len(outs), [list(o.shape) for o in outs]))

for i, o in enumerate(outs):
    try:
        # ulab ndarrays expose no .max() method; the function form works.
        print("L5 out[{}] dtype={} max={:.4f} min={:.4f}".format(
            i, o.dtype, float(np.max(o)), float(np.min(o))))
    except BaseException as e:
        print("L5 out[{}] stats error {}".format(i, e))

print("L6 conf -> boxes (same frame)")
last = None
for c in SCAN:
    try:
        n = count_at(outs, c)
    except BaseException as e:
        print("   {:.2f} -> error {}".format(c, e))
        continue
    flag = "  <-- change" if (last is not None and n != last) else ""
    print("   {:.2f} -> {}{}".format(c, n, flag))
    last = n

print("L7 the three app presets on the same tensors:")
for name, conf in (("Recall", 0.25), ("Normal", 0.50), ("Strict", 0.70)):
    print("   {} {:.2f} -> {}".format(name, conf, count_at(outs, conf)))

del outs
gc.collect()
launcher.close_app()
print("L8 closed | mem_free={}".format(gc.mem_free()))
print("ALL DONE")
