# Display bring-up: LVGL initialisation, the two full-screen draw buffers, the
# flush callback, and the panel backlight.
#
# The backlight is a separate GPIO (25). Display.init() brings up the panel and the
# frame pipeline but leaves this pin alone, so a boot without this code renders
# into a dark screen: LVGL runs and the logs look healthy, and a snapshot still
# succeeds because it reads the LVGL buffer rather than the panel. GPIO output
# state also survives a soft reset, so a probe that dimmed the pin leaves the next
# boot dark.

import time

import image
import lvgl as lv
import uctypes
from machine import FPIOA, Pin, PWM
from media.display import Display

from hardware import backlight
from hardware import panel
from shell.perf import PERF

BACKLIGHT_PIN = 25
BACKLIGHT_PWM_HZ = 1000
BACKLIGHT_FADE_STEPS = 10   # 10 steps x 15ms = 150ms fade-in
BACKLIGHT_FADE_MS = 15
_BACKLIGHT = None           # keep the PWM/Pin object alive; dropped objects stop driving

disp_imgs = None
disp_drv = None
REFR_NOW = None             # cached lv.refr_now (None = not supported)


def backlight_on(target_pct=backlight.MIN_PERCENT):
    """Drive the panel backlight to the stored brightness.

    PWM first for a short fade, then a plain GPIO high, then give up loudly: a
    silent failure here is indistinguishable from a dead board from the user's
    side. The PWM object is handed to hardware.backlight so the Settings app can
    dim it later; the GPIO fallback cannot dim and hardware.backlight stays
    detached.
    """
    global _BACKLIGHT
    target = int(target_pct)
    if target < backlight.MIN_PERCENT:
        target = backlight.MIN_PERCENT
    elif target > backlight.MAX_PERCENT:
        target = backlight.MAX_PERCENT
    try:
        pwm = PWM(BACKLIGHT_PIN, freq=BACKLIGHT_PWM_HZ, duty=0)
        for step in range(1, BACKLIGHT_FADE_STEPS + 1):
            pwm.duty(step * target // BACKLIGHT_FADE_STEPS)
            time.sleep_ms(BACKLIGHT_FADE_MS)
        _BACKLIGHT = pwm
        backlight.attach(pwm, target)
        print("[GUI] backlight on: PWM {}Hz {}%, fade {}ms".format(
            BACKLIGHT_PWM_HZ, target, BACKLIGHT_FADE_STEPS * BACKLIGHT_FADE_MS))
        return
    except BaseException as error:
        print("[GUI] backlight PWM unavailable: {}".format(error))
    try:
        fpioa = FPIOA()
        fn = getattr(FPIOA, "GPIO25", None)
        if fn is None:
            fn = FPIOA.GPIO0 + BACKLIGHT_PIN
        fpioa.set_function(BACKLIGHT_PIN, fn)
        pin = Pin(BACKLIGHT_PIN, Pin.OUT, pull=Pin.PULL_NONE, drive=7)
        pin.value(1)
        _BACKLIGHT = pin
        print("[GUI] backlight on: GPIO{} high".format(BACKLIGHT_PIN))
    except BaseException as error:
        print("[GUI] backlight GPIO failed: {} (the panel will stay dark)".format(error))


def lvgl_setup():
    """lv.init() plus the display driver: ARGB8888, two buffers, FULL render mode."""
    global disp_imgs, disp_drv, REFR_NOW
    lv.init()    # LV_COLOR_DEPTH is 32: ARGB8888 <-> image.BGRA8888
    disp_imgs = [image.Image(panel.WIDTH, panel.HEIGHT, image.BGRA8888) for _ in range(2)]
    disp_drv = lv.disp_create(panel.WIDTH, panel.HEIGHT)
    disp_drv.set_color_format(lv.COLOR_FORMAT.ARGB8888)
    disp_drv.set_draw_buffers(disp_imgs[0].bytearray(), disp_imgs[1].bytearray(),
                              disp_imgs[0].size(), lv.DISP_RENDER_MODE.FULL)
    disp_drv.set_flush_cb(_flush_cb)
    # Probe the forced-refresh API (presence varies between bindings). If the probe
    # fails, fall back to the normal cadence.
    refr = getattr(lv, "refr_now", None)
    if refr is None:
        print("[GUI] frame boost: lv.refr_now NOT available")
        return
    try:
        refr(disp_drv)
        REFR_NOW = refr
        print("[GUI] frame boost: lv.refr_now ok")
    except BaseException as error:
        print("[GUI] frame boost: lv.refr_now failed: {}".format(error))


def _flush_cb(disp_drv, area, color):
    t0 = time.ticks_ms()
    if disp_drv.flush_is_last():
        ptr = uctypes.addressof(color.__dereference__())
        img_to_show = disp_imgs[0] if disp_imgs[0].virtaddr() == ptr else disp_imgs[1]
        Display.show_image(img_to_show, layer=Display.LAYER_OSD0)
    disp_drv.flush_ready()
    t = time.ticks_diff(time.ticks_ms(), t0)
    PERF["flush_sum"] += t
    PERF["flush_n"] += 1
    if t > PERF["flush_max"]:
        PERF["flush_max"] = t
