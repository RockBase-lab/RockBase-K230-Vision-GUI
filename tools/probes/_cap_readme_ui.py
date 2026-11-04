# Captures the flat screens used by the README: desktop (both pages), the two
# folder flyouts, and the non-camera apps. RLE is the right encoding here: a flat
# 800x480 frame compresses to about 46 KB, while a photo frame would expand past
# 1 MB and raise MemoryError.
#
# Output is SHOT/B64 text; decode it with
#   python tools/decode_lv_snapshot.py tmp/readme_ui.txt "tmp/readme_%s.png"
#
# Every close path runs in try/finally: an app left open holds the sensor or the
# audio handle, which only a power cycle clears.

import sys
import ubinascii
import uctypes

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("apps", "apps.error", "apps.app_settings", "apps.app_audio",
              "apps.app_imu", "apps.app_touch", "apps.app_network",
              "apps.app_gallery", "ui.beep", "ui.paging", "ui.splash",
              "ui.backlight", "launcher", "camera_fov", "config"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv
import time

_f = open("/sdcard/factory/gui/launcher.py")
_gui_src = _f.read()
_f.close()
del _f

G = {"__name__": "gui_probe"}
exec(compile(_gui_src, "launcher.py", "exec"), G)
G["_ensure_cache_dir"]()


def shot(tag):
    """Take one ARGB8888 snapshot of the active screen and print it as RLE."""
    for _ in range(3):
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
    print("[CAP] {} ok {} bytes".format(tag, len(out)))


def meta_of(group, app_id):
    for item in G["FOLDER_MEMBERS"][group]:
        if item["id"] == app_id:
            return item
    return None


def top_of(app_id):
    for item in G["APPS"]:
        if item["id"] == app_id:
            return item
    return None


launcher = G["Launcher"]()
launcher.build_home()
shot("home1")
print("[CAP] pages={}".format(launcher.pager.page_count if launcher.pager else 1))

if launcher.pager is not None and launcher.pager.page_count > 1:
    launcher.pager.go(1, animate=False)
    shot("home2")
    launcher.pager.go(0, animate=False)

try:
    launcher._open_folder(top_of("vision"))
    shot("folder_vision")
    launcher.back_out()
    launcher._open_folder(top_of("face"))
    shot("folder_face")
    launcher.back_out()
except BaseException as error:
    print("[CAP] folder failed: {}".format(error))
    try:
        launcher.back_out()
    except BaseException:
        pass

for app_id in ("settings", "audio", "imu", "gallery", "network"):
    meta = top_of(app_id)
    if meta is None:
        print("[CAP] {} not in APPS".format(app_id))
        continue
    try:
        launcher.open_app(meta)
        if launcher.active_app is None:
            print("[CAP] {} panel instead of app".format(app_id))
            launcher.close_app()
            continue
        for _ in range(8):
            launcher.active_app.step()
            lv.task_handler()
            time.sleep_ms(50)
        shot("app_" + app_id)
    except BaseException as error:
        print("[CAP] {} failed: {}".format(app_id, error))
    finally:
        try:
            launcher.close_app()
        except BaseException as close_error:
            print("[CAP] {} close failed: {}".format(app_id, close_error))

print("DONE")
