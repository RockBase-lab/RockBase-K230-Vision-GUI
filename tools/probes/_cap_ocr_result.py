# Verify the OCR result view: dump every label's geometry (proves reading order,
# no overlap, full-screen coverage) and export a narrow horizontal band through
# two punctuated lines so the glyphs can be looked at.
#
# The band is small on purpose: a full 800x480 live frame does not compress with
# RLE and raises MemoryError. Run with --lvgl, camera aimed at text.

import gc
import sys
import ubinascii
import uctypes

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("apps.app_ocr", "apps", "apps.error", "ui.beep", "launcher",
              "camera_fov"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv

_src = open("/sdcard/factory/gui/launcher.py").read()
G = {"__name__": "gui_probe"}
exec(compile(_src, "launcher.py", "exec"), G)
G["_ensure_cache_dir"]()

launcher = G["Launcher"]()

try:
    meta = None
    for item in G["FOLDER_MEMBERS"]["vision"]:
        if item["id"] == "ocr":
            meta = item
            break
    launcher.open_app(meta)
    app = launcher.active_app
    if app is None:
        print("RESULT: FAIL - app is None")
    else:
        import time
        t0 = time.ticks_ms()
        while time.ticks_diff(time.ticks_ms(), t0) < 1000:
            app.step()
            lv.task_handler()
            time.sleep_ms(20)

        # Drive the scan through the real state machine so the status bar and the
        # mask reflect what a tap produces.
        app._toggle_scan()
        app.step()
        for _ in range(3):
            lv.task_handler()
        lv.refr_now(lv.disp_get_default())

        print("C1 mask hidden={} size={}x{} state={} hint={!r}".format(
            app.mask.has_flag(lv.obj.FLAG.HIDDEN),
            app.mask.get_width(), app.mask.get_height(),
            app.state, app.stat_hint.get_text()))

        vis = 0
        top = None
        bottom = None
        prev_bottom = -1
        overlap = False
        for i, lab in enumerate(app.panel_labels):
            if lab.has_flag(lv.obj.FLAG.HIDDEN):
                continue
            vis += 1
            y = lab.get_y()
            h = lab.get_height()
            print("C2.{} y={} h={} w={} {!r}".format(
                i, y, h, lab.get_width(), lab.get_text()[:12]))
            if top is None or y < top:
                top = y
            if bottom is None or y + h > bottom:
                bottom = y + h
            if y < prev_bottom:
                overlap = True
            prev_bottom = y + h
        print("C3 visible={} span={}..{} overlap={}".format(
            vis, top, bottom, overlap))

        # Two narrow bands: the top bar (must stay above the mask) and the rows
        # that carry full-width punctuation. A full 800x480 frame does not
        # compress with RLE.
        dsc = lv.snapshot_take(lv.scr_act(), lv.COLOR_FORMAT.ARGB8888)
        w = dsc.header.w
        h = dsc.header.h
        print("C4 snapshot {}x{}".format(w, h))
        ptr = int.from_bytes(bytes(memoryview(dsc.data)), "little")
        raw = uctypes.bytearray_at(ptr, w * h * 4)

        for tag, band_x, band_y, band_w, band_h in (
                ("ocrbar", 0, 0, 800, 80),
                ("ocrband", 140, 60, 520, 90)):
            out = bytearray()
            for row in range(band_h):
                y = band_y + row
                base = y * w * 4
                x = 0
                while x < band_w:
                    p = base + (band_x + x) * 4
                    b = raw[p]
                    g = raw[p + 1]
                    r = raw[p + 2]
                    run = 1
                    while run < 255 and x + run < band_w:
                        q = base + (band_x + x + run) * 4
                        if raw[q] != b or raw[q + 1] != g or raw[q + 2] != r:
                            break
                        run += 1
                    out.append(run - 1)
                    out.append(b)
                    out.append(g)
                    out.append(r)
                    x += run

            print("SHOT %s %d %d %d %d" % (tag, band_w, band_h, len(raw),
                                           len(out)))
            b64 = ubinascii.b2a_base64(bytes(out)).decode().strip()
            for i in range(0, len(b64), 800):
                print("B64 " + b64[i:i + 800])
            print("SHOTEND " + tag)

        del raw, dsc
        gc.collect()
finally:
    launcher.close_app()
    print("RESULT: DONE")
