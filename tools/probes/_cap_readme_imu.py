# Captures the IMU app's three pages for the README. The board must already be
# held at the attitude worth showing: the dashboard draws the live AHRS estimate,
# so the picture is whatever the board is doing at capture time.
#
# Runs the real launcher so the pages are the production layout. The attitude is
# printed before each capture so the numbers in the log can be checked against the
# picture. RLE is used (the UI is flat), and every page is captured in one run so
# the whole set stays consistent.

import sys
import time
import ubinascii
import uctypes

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("core", "apps", "core.errors", "apps.app_imu", "ui.beep", "ui.paging",
              "launcher", "core.config"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv

_f = open("/sdcard/factory/gui/launcher.py")
_src = _f.read()
_f.close()
del _f

G = {"__name__": "gui_probe"}
exec(compile(_src, "launcher.py", "exec"), G)
G["_ensure_cache_dir"]()


def shot(tag):
    for _ in range(2):
        lv.task_handler()
        time.sleep_ms(40)
    dsc = lv.snapshot_take(lv.scr_act(), lv.COLOR_FORMAT.ARGB8888)
    w, h = dsc.header.w, dsc.header.h
    ptr = int.from_bytes(bytes(memoryview(dsc.data)), "little")
    raw = uctypes.bytearray_at(ptr, w * h * 4)
    out = bytearray()
    y = 0
    while y < h:
        base = y * w * 4
        run = 1
        b0 = raw[base]
        g0 = raw[base + 1]
        r0 = raw[base + 2]
        for x in range(1, w):
            o = base + x * 4
            b = raw[o]
            g = raw[o + 1]
            r = raw[o + 2]
            if b == b0 and g == g0 and r == r0 and run < 256:
                run += 1
            else:
                out.append(run - 1)
                out.append(b0)
                out.append(g0)
                out.append(r0)
                b0, g0, r0 = b, g, r
                run = 1
        out.append(run - 1)
        out.append(b0)
        out.append(g0)
        out.append(r0)
        y += 1
    dsc.buf_free()
    print("SHOT {} {} {} {} {}".format(tag, w, h, w * h * 4, len(out)))
    b64 = ubinascii.b2a_base64(bytes(out)).decode().strip()
    for i in range(0, len(b64), 800):
        print("B64 " + b64[i:i + 800])
    print("SHOTEND " + tag)
    print("[CAP] {} ok".format(tag))


def top_of(app_id):
    for item in G["APPS"]:
        if item["id"] == app_id:
            return item
    return None


launcher = G["Launcher"]()
launcher.build_home()

meta = top_of("imu")
try:
    launcher.open_app(meta)
    app = launcher.active_app
    if app is None:
        print("[CAP] IMU showed a panel")
    else:
        # The AHRS needs a couple of seconds of samples before the horizon settles;
        # the first frames show the accelerometer-only estimate and read as tilted.
        deadline = time.ticks_add(time.ticks_ms(), 4000)
        while time.ticks_diff(deadline, time.ticks_ms()) > 0:
            app.step()
            lv.task_handler()
            time.sleep_ms(40)

        pages = ((0, "app_imu"), (1, "app_imu_raw"), (2, "app_imu_att"))
        views = {1: app._raw_view, 2: app._att_view}
        for page, tag in pages:
            app._set_page(page)
            # The curve window keeps CHART_POINTS samples (150 at 20 ms = 3 s). The
            # gyro-zero calibration and the first AHRS samples sit at the left edge
            # of that window and read as a wild ramp, so drop the recorded history
            # once the estimator has settled, then dwell longer than a full window
            # so the buffer refills with steady-state samples only.
            view = views.get(page)
            if view is not None:
                for _ in range(60):
                    app.step()
                    lv.task_handler()
                    time.sleep_ms(50)
                view.hist = []
                for _ in range(160):
                    app.step()
                    lv.task_handler()
                    time.sleep_ms(50)
                view.refill()
                for _ in range(4):
                    app.step()
                    lv.task_handler()
                    time.sleep_ms(50)
            else:
                for _ in range(100):
                    app.step()
                    lv.task_handler()
                    time.sleep_ms(50)
            print("[ATT] page={} roll={:+.1f} pitch={:+.1f} yaw={:+.1f}".format(
                page, app._roll, app._pitch, app._yaw))
            shot(tag)
finally:
    try:
        launcher.close_app()
    except BaseException as error:
        print("[CAP] close failed: {}".format(error))

print("DONE")
