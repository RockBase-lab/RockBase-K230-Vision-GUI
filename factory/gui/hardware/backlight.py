# Panel backlight handle shared between the launcher and the Settings app.
#
# The launcher owns bring-up (PWM fade, or the GPIO fallback that cannot dim)
# because the panel must light before any UI exists. It then hands the PWM
# object here, so a later brightness change is a duty write rather than a
# second PWM object on the same pin.
#
# When bring-up fell back to a plain GPIO, attach() is never called and
# set_percent() is a no-op; the Settings row still shows a value, but a
# non-dimmable panel is a degraded boot, not an error.

# Lowest duty that still reads comfortably in a lit room. The PWM itself accepts
# down to 1%, but below this the 1 kHz pulses turn the 800x480 panel into something
# the user has to squint at, so it is the product floor rather than a hardware one.
MIN_PERCENT = 20
MAX_PERCENT = 100

_pwm = None
_level = MIN_PERCENT


def _clamp(pct):
    pct = int(pct)
    if pct < MIN_PERCENT:
        return MIN_PERCENT
    if pct > MAX_PERCENT:
        return MAX_PERCENT
    return pct


def attach(pwm, pct=MIN_PERCENT):
    """Take ownership of the PWM already driving the backlight pin."""
    global _pwm, _level
    _pwm = pwm
    _level = _clamp(pct)
    if pwm is None:
        return False
    try:
        pwm.duty(_level)
        return True
    except BaseException:
        # A dead channel drops the handle so later writes do not raise every frame
        _pwm = None
        return False


def set_percent(pct):
    """Set brightness in percent. Returns whether the hardware accepted it."""
    global _pwm, _level
    _level = _clamp(pct)
    pwm = _pwm
    if pwm is None:
        return False
    try:
        pwm.duty(_level)
        return True
    except BaseException:
        _pwm = None
        return False


def level():
    return _level


def available():
    return _pwm is not None
