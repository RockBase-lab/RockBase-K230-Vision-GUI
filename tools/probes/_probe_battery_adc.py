# Battery divider measurement through ADC5.
#
# Two things this probe proves on board:
#   1) read_u16() and read_uv() describe the same 12-bit conversion, so the code
#      can be derived from the voltage;
#   2) calling them back to back inside one interval corrupts the second value,
#      which shows up as 4095-saturated samples and a false ~2.4V span.
#
# It never opens ADC channel 4: that wedges the board and drops the serial link.

import time
from machine import ADC, FPIOA, Pin

ENABLE_PIN = 69
ADC_CHANNEL = 5


def _mux(state):
    fpioa = FPIOA()
    fpioa.set_function(ENABLE_PIN, FPIOA.GPIO0 + ENABLE_PIN)
    pin = Pin(ENABLE_PIN, Pin.OUT, pull=Pin.PULL_NONE, drive=7)
    pin.value(state)
    return pin


pin = _mux(1)
try:
    print("[pin] readback after value(1): {}".format(pin.value()))
    time.sleep_ms(1000)

    adc = ADC(ADC_CHANNEL)
    try:
        # Case A: read_uv() alone at raw spacing.
        uvs = []
        for _ in range(31):
            uvs.append(adc.read_uv() // 1000)
            time.sleep_ms(10)
        print("[uv only] {}".format(uvs))
        print("[uv only] span={}mV median={}mV".format(
            max(uvs) - min(uvs), sorted(uvs)[len(uvs) // 2]))

        # Case B: the two calls back to back, which is what corrupts the sample.
        pairs = []
        for _ in range(10):
            pairs.append((adc.read_uv() // 1000, adc.read_u16()))
            time.sleep_ms(10)
        print("[pair] {}".format(pairs))
        print("[pair] saturated raw values: {}".format(
            sum(1 for _, raw in pairs if raw >= 4095)))

        # Case C: read_u16() alone, for the voltage-to-code ratio.
        raws = []
        for _ in range(10):
            raws.append(adc.read_u16())
            time.sleep_ms(10)
        print("[raw only] {}".format(raws))
        print("[derived] uv*4095//1800000 from case A: {}".format(
            [v * 1000 * 4095 // 1800000 for v in uvs[:5]]))

        # Case D: does the enable pin gate the divider at all?
        for state in (0, 1):
            pin.value(state)
            time.sleep_ms(3000)
            vals = [adc.read_uv() // 1000 for _ in range(4)]
            print("[pin={} 3s settle] {}".format(state, vals))
    finally:
        try:
            adc.deinit()
        except BaseException:
            pass
finally:
    pin.value(0)
    print("PROBE DONE")
