# Physical keys: GPIO0=CANCEL (pull-up, active low), GPIO53=OK (pull-down,
# active high).
#
# The polarity is a measured on-board fact: GPIO0 idles at 1 and reads 0 when
# pressed; GPIO53 idles at 0 and reads 1 when pressed (the other leg of the switch
# goes to 3.3V).
#
# The main loop calls poll() every ~5ms for edge detection plus debounce: a level
# only counts once it has been stable for >= DEBOUNCE_MS.
# CANCEL walks back one layer per press along the path the user came in by; that
# rule lives in Launcher.back_out().
# OK: no function bound on the desktop, only printed (reserved for confirm/select).

import time

from machine import FPIOA, Pin

from ui import beep


class HardwareButtons:
    """CANCEL / OK edge detection.

    A physical key gets the same click-tone feedback as an on-screen button. OK is
    the exception: the running app can mute it with ctx.ok_beep=False, because
    Camera already plays a synthesised shutter on OK and two sounds at once muffle
    each other.
    """

    CANCEL_PIN = 0
    OK_PIN = 53
    DEBOUNCE_MS = 30
    # Active level while pressed (confirmed by measurement on board)
    ACTIVE_LEVEL = {"cancel": 0, "ok": 1}

    def __init__(self):
        self.ok_pin = None
        self.cancel_pin = None
        try:
            # Some K230 pads do not default to the GPIO function, so switch them
            # explicitly to GPIOxx (the same method used for the PA pin in app_audio)
            fpioa = FPIOA()
            for p in (self.CANCEL_PIN, self.OK_PIN):
                try:
                    fpioa.set_function(p, getattr(FPIOA, "GPIO{}".format(p)))
                except BaseException as e:
                    print("[GUI] FPIOA set_function({}) failed: {}".format(p, e))
            self.cancel_pin = Pin(self.CANCEL_PIN, Pin.IN, Pin.PULL_UP)
            self.ok_pin = Pin(self.OK_PIN, Pin.IN, Pin.PULL_DOWN)
            print("[GUI] hardware buttons ready: GPIO{}=CANCEL(up/active-low) "
                  "GPIO{}=OK(down/active-high)".format(self.CANCEL_PIN, self.OK_PIN))
        except BaseException as error:
            print("[GUI] hardware button init failed: {}".format(error))
            self.cancel_pin = None
            self.ok_pin = None
        # Per key: stable state (True=pressed), last raw level, time of the level change
        self._stable = {"cancel": False, "ok": False}
        self._raw = {"cancel": False, "ok": False}   # starts out treated as not pressed
        self._change_ms = {"cancel": 0, "ok": 0}

    def _scan(self, pin, name):
        if pin is None:
            return False
        try:
            level = pin.value()
        except BaseException:
            return False
        now = time.ticks_ms()
        pressed = (level == self.ACTIVE_LEVEL[name])
        if pressed != self._raw[name]:
            self._raw[name] = pressed
            self._change_ms[name] = now
        elif pressed != self._stable[name] and \
                time.ticks_diff(now, self._change_ms[name]) >= self.DEBOUNCE_MS:
            self._stable[name] = pressed
            if pressed:
                return True  # this poll saw one stable press (edge)
        return False

    def poll(self, launcher):
        """Called from the main loop; returns whether a key event was consumed."""
        cancel_press = self._scan(self.cancel_pin, "cancel")
        ok_press = self._scan(self.ok_pin, "ok")
        if ok_press and (launcher.ctx is None or launcher.ctx.ok_beep):
            beep.tap()
        if cancel_press:
            beep.tap()
        if ok_press:
            print("[Key] OK pressed (GPIO{})".format(self.OK_PIN))
            # Forward the OK edge to the current app (consumed with ctx.take_ok()
            # inside step()). On the home screen ctx is None, so the press is only
            # printed and has no side effect.
            if launcher.ctx is not None:
                launcher.ctx.ok_pressed = True
        if cancel_press:
            print("[Key] CANCEL pressed (GPIO{})".format(self.CANCEL_PIN))
            return launcher.back_out()
        return False
