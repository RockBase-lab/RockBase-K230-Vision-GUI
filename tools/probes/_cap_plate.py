# Captures the Plate caption so the 30px size and the green ink can be checked by
# eye instead of trusted.
#
# The app runs through the real launcher, so the caption uses the production
# style. Only the caption region is exported: a live camera frame does not
# compress with RLE (measured: a full 800x480 frame needed ~1 MB of output and
# raised MemoryError), while the flat caption area does and is all this probe
# needs to show.
#
# Output: SHOT/B64 lines that tools/decode_lv_snapshot.py turns into a PNG.
# Run with --lvgl.
#
# The whole body is wrapped in try/finally: a probe that dies before close_app
# leaves the Sensor singleton claimed, and only a machine.reset() clears it.

import gc
import sys
import ubinascii
import uctypes

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("apps.app_plate", "apps", "apps.error", "ui.beep", "launcher",
              "camera_fov"):
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
G["_ensure_cache_dir"]()

launcher = G["Launcher"]()

try:
    meta = None
    for item in G["FOLDER_MEMBERS"]["vision"]:
        if item["id"] == "plate":
            meta = item
            break
    launcher.open_app(meta)
    app = launcher.active_app
    if app is None:
        print("RESULT: FAIL - app is None (see the panel on screen)")
    else:
        print("C1 app up")
        for _ in range(10):
            app.step()
            lv.task_handler()
        print("C2 plates={} texts={}".format(app.plate_count, app._texts))

        if not any(app._texts):
            # No plate in view: push a string through the vote so the caption is
            # exercised anyway. Three votes is what the app requires.
            for _ in range(3):
                app._vote(0, "渝A999U9")
            print("C3 injected caption for layout check")

        # A synthetic quad places the caption where it is easy to crop, so this is
        # a layout check rather than a detection check.
        quad = np.array([120.0, 180.0, 480.0, 180.0, 480.0, 300.0, 120.0, 300.0])
        app._show_plates([quad])
        print("C4 caption drawn at (120,180)-(480,300)")

        for _ in range(3):
            lv.task_handler()
        lv.refr_now(lv.disp_get_default())

        cap = app.captions[0]
        print("C5 caption text={!r} pos=({},{}) size={}x{} hidden={}".format(
            cap.get_text(), cap.get_x(), cap.get_y(),
            cap.get_width(), cap.get_height(),
            cap.has_flag(lv.obj.FLAG.HIDDEN)))

        cx = cap.get_x()
        cy = cap.get_y()
        box_w = cap.get_width() + 8
        box_h = cap.get_height() + 6
        if cx + box_w > 800:
            cx = 800 - box_w
        if cy + box_h > 480:
            cy = 480 - box_h
        print("C6 crop region ({},{}) {}x{}".format(cx, cy, box_w, box_h))

        dsc = lv.snapshot_take(lv.scr_act(), lv.COLOR_FORMAT.ARGB8888)
        w = dsc.header.w
        h = dsc.header.h
        print("C7 snapshot {}x{} full frame".format(w, h))
        ptr = int.from_bytes(bytes(memoryview(dsc.data)), "little")
        raw = uctypes.bytearray_at(ptr, w * h * 4)

        # Per-row RLE over the crop only, run length 1..255, memory order B,G,R,A.
        out = bytearray()
        for row in range(box_h):
            y = cy + row
            base = y * w * 4
            x = 0
            while x < box_w:
                p = base + (cx + x) * 4
                b = raw[p]
                g = raw[p + 1]
                r = raw[p + 2]
                run = 1
                while run < 255 and x + run < box_w:
                    q = base + (cx + x + run) * 4
                    if raw[q] != b or raw[q + 1] != g or raw[q + 2] != r:
                        break
                    run += 1
                out.append(run - 1)
                out.append(b)
                out.append(g)
                out.append(r)
                x += run

        print("SHOT plate %d %d %d %d" % (box_w, box_h, len(raw), len(out)))
        b64 = ubinascii.b2a_base64(bytes(out)).decode().strip()
        for i in range(0, len(b64), 800):
            print("B64 " + b64[i:i + 800])
        print("SHOTEND plate")

        del raw, out, dsc
        gc.collect()
finally:
    # Always release the camera, otherwise the next run reports
    # "sensor(2) is already inited" and needs a machine.reset().
    try:
        if launcher.active_app is not None:
            launcher.close_app()
            print("C8 closed")
    except BaseException as error:
        print("C8 close failed:", error)
        sys.print_exception(error)

print("RESULT: DONE")
