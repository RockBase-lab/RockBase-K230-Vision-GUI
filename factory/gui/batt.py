# Shared ADC5 battery access for the status bar and the Battery app.
#
# Both consumers must share one ADC handle: opening ADC(5) twice in the same VM is
# untested on this board, and a second handle is the obvious way to break it.
#
# Conversion chain, measured on board:
#   the divider is 1.5k over 1k, so the tap is 2/5 of the pack;
#   read_uv() assumes a 1.80V reference while the rail measures 1.88V, so raw values
#   are 4.4% low and get scaled by 47/45;
#   the code is a plain 12-bit conversion (measured raw=3417 next to 1501mV).
#   The percentage comes from a resting Li-ion OCV curve: it reads high while
#   charging and low under load, so it is an estimate, not a fuel gauge.

import time

from machine import ADC

ADC_CHANNEL = 5             # channel 4 wedges the board; never open it

DIV_NUM = 5
DIV_DEN = 2
VREF_FIX_NUM = 47           # 1880/1800 in lowest terms, keeps the multiply 32-bit
VREF_FIX_DEN = 45
ADC_FULL_CODE = 4095
ADC_FULL_MV = 1800000
MAX_INPUT_UV = 2200000      # above this the sample is not a real tap voltage
ABSENT_MAX_MV = 100         # a disconnected pack leaves the tap near ground

# Resting open-circuit voltage of a single Li-ion cell, in 25mV steps.
OCV_CURVE = (
    (4200, 100), (4150, 97), (4100, 93), (4050, 89), (4000, 84),
    (3950, 79), (3900, 73), (3850, 66), (3800, 58), (3750, 49),
    (3700, 39), (3650, 29), (3600, 20), (3550, 13), (3500, 8),
    (3450, 4), (3400, 2), (3300, 0),
)

# Status-bar refresh period. Charge moves over minutes, and the divider cannot save
# power anyway (GPIO69 does not gate it), so polling faster only costs I2C time.
CACHE_MS = 20000

_ADC = None
_last = None                # (tick_ms, mv, percent, level)
_primed = False


def adc():
    """Open the shared ADC5 handle once and keep it. Raises on failure."""
    global _ADC
    if _ADC is None:
        _ADC = ADC(ADC_CHANNEL)
    return _ADC


def correct(uv):
    """Apply the per-board Vref correction to a raw read_uv() value."""
    return uv * VREF_FIX_NUM // VREF_FIX_DEN


def delta_code(uv):
    """ADC code that read_uv() came from, derived so the channel is read once."""
    return uv * ADC_FULL_CODE // ADC_FULL_MV


def pack_mv(uv):
    """Pack voltage in mV from an uncorrected read_uv() value."""
    return correct(uv) * DIV_NUM // DIV_DEN // 1000


def percent(mv):
    """Estimate charge from the resting OCV curve; 0-100."""
    if mv >= OCV_CURVE[0][0]:
        return 100
    for i in range(1, len(OCV_CURVE)):
        hi_mv, hi_pct = OCV_CURVE[i - 1]
        lo_mv, lo_pct = OCV_CURVE[i]
        if mv >= lo_mv:
            return lo_pct + (mv - lo_mv) * (hi_pct - lo_pct) // (hi_mv - lo_mv)
    return 0


def level(mv):
    """Icon level 0-3, one per quarter of the estimated charge.

    A pack that is not there, or reads below the absent threshold, returns 0 so the
    status bar shows an empty icon rather than an invention.
    """
    if mv is None or mv <= ABSENT_MAX_MV:
        return 0
    pct = percent(mv)
    lv = pct // 25
    if lv > 3:
        lv = 3
    return lv


def sample_uv(count=5, gap_ms=10):
    """Median of `count` read_uv() values, or None when every sample was invalid."""
    handle = adc()
    values = []
    for i in range(count):
        if i:
            time.sleep_ms(gap_ms)
        try:
            uv = handle.read_uv()
        except BaseException as error:
            print("[batt] read failed: {}".format(error))
            continue
        if 0 < uv <= MAX_INPUT_UV:
            values.append(uv)
    if not values:
        return None
    values.sort()
    return values[len(values) // 2]


def status(force=False):
    """(mv, percent, level) for the status bar, cached for CACHE_MS.

    Returns None when the channel cannot be read; callers keep their last icon.
    """
    global _last, _primed
    now = time.ticks_ms()
    if not force and _last is not None:
        if time.ticks_diff(now, _last[0]) < CACHE_MS:
            return _last[1], _last[2], _last[3]
    try:
        if not _primed:
            # The first conversion after ADC() is created returns 0 on this board.
            _primed = True
            try:
                adc().read_uv()
            except BaseException:
                pass
            time.sleep_ms(20)
        uv = sample_uv()
    except BaseException as error:
        print("[batt] status failed: {}".format(error))
        return None
    if uv is None:
        return None
    mv = pack_mv(uv)
    pct = percent(mv) if mv > ABSENT_MAX_MV else 0
    lvl = level(mv)
    _last = (now, mv, pct, lvl)
    return mv, pct, lvl
