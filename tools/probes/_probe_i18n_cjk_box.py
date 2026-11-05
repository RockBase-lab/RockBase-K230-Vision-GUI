# Measure the dialog CJK face and render the two dialogs the users reported as
# boxed: the IMU calibration prompt and the face-recognition status bar.
# Run with --lvgl. Language is forced in memory only.
import sys

sys.path.insert(0, "/sdcard/factory/gui")
for _name in list(sys.modules):
    if _name.split(".")[0] in ("core", "apps", "ui", "shell", "launcher",
                               "hardware", "audio", "net"):
        del sys.modules[_name]

import gc
import time
import ubinascii
import uctypes

import lvgl as lv

from core import config
from core import i18n
from core import font_pool


def shot(tag):
    for _ in range(3):
        lv.task_handler()
        time.sleep_ms(30)
    dsc = lv.snapshot_take(lv.scr_act(), lv.COLOR_FORMAT.ARGB8888)
    w, h = dsc.header.w, dsc.header.h
    ptr = int.from_bytes(bytes(memoryview(dsc.data)), "little")
    raw = uctypes.bytearray_at(ptr, w * h * 4)
    out = bytearray()
    for y in range(h):
        base = y * w * 4
        run = 1
        b0, g0, r0 = raw[base], raw[base + 1], raw[base + 2]
        for x in range(1, w):
            o = base + x * 4
            b, g, r = raw[o], raw[o + 1], raw[o + 2]
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
    dsc.buf_free()
    print("SHOT {} {} {} {} {}".format(tag, w, h, len(out), w * h * 4))
    b64 = ubinascii.b2a_base64(bytes(out)).decode().strip()
    for i in range(0, len(b64), 800):
        print("B64 " + b64[i:i + 800])
    print("SHOTEND " + tag)


print("[FONT] free before: {}KB".format(gc.mem_free() // 1024))
for name in ("ui_cjk_22.bin",):
    t0 = time.ticks_ms()
    f = font_pool.get(name)
    print("[FONT] {} {}ms ok={} free={}KB".format(
        name, time.ticks_diff(time.ticks_ms(), t0), f is not None,
        gc.mem_free() // 1024))

config.load()
config.set("ui.language", "zh", save=False)
i18n.init()
print("[I18N] lang={}".format(i18n.lang()))

# ---- 1. IMU calibration prompt -------------------------------------------
# Build the overlay directly; the I2C sensor is not touched by _build_overlay.
import apps.app_imu as imu_mod

scr = lv.obj()
lv.scr_load(scr)
content = lv.obj(scr)
content.set_size(800, 480)
content.set_style_pad_all(0, 0)
content.set_style_border_width(0, 0)
content.set_style_bg_opa(lv.OPA.TRANSP, 0)


class Ctx:
    width = 800
    height = 480
    screen_w = 800
    screen_h = 480
    content_x = 0
    content_y = 0
    app_name = "IMU"
    want_exit = False
    touch = None
    ok_pressed = False
    ok_beep = True
    back_handler = None
    unavailable_msg = None

    def request_exit(self):
        self.want_exit = True

    def set_back_handler(self, handler):
        self.back_handler = handler

    def take_ok(self):
        return False


class Shim:
    pass


app = Shim()
app._content = content
app.ctx = Ctx()
app._overlay = None
app._state = 0
app._from_menu = False
app._cal_action = None
try:
    imu_mod.ImuApp._build_overlay(app)
    # _enter_prompt only forwards these three values to _set_overlay, so call it
    # directly: the shim does not need the rest of the app's methods.
    imu_mod.ImuApp._set_overlay(
        app,
        i18n.t("imu.gyro_title"),
        i18n.t("imu.gyro_prompt") % i18n.t("imu.need_gyro"),
        ((i18n.t("imu.btn_start"), 0x16A34A, "start"),
         (i18n.t("imu.btn_skip"), 0x374151, "skip")))
    print("[IMU] prompt built")
    shot("imu_prompt_zh")
except BaseException as error:
    print("[IMU] failed: {}".format(error))
    sys.print_exception(error)

lv.scr_load(lv.obj())
for _ in range(2):
    lv.task_handler()

# ---- 2. Face recognition status bar -------------------------------------
# seg_name starts as "-", so this is the label that used to box Chinese.
import apps.app_face_recognize as fr

scr = lv.obj()
lv.scr_load(scr)
content = lv.obj(scr)
content.set_size(800, 480)
content.set_style_pad_all(0, 0)
content.set_style_border_width(0, 0)
content.set_style_bg_opa(lv.OPA.TRANSP, 0)

try:
    fr_app = fr.create_app(content, Ctx())
    for _ in range(4):
        lv.task_handler()
    shot("recognize_zh")
    fr_app.destroy()
except BaseException as error:
    print("[REC] failed: {}".format(error))
    sys.print_exception(error)

print("PROBE_DONE")
