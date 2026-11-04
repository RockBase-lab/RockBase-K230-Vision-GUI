# Noise, offset and magnitude statistics for a static board.

import sys
import time
import math

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("core", "apps.app_motion", "apps", "core.errors"):
    if _name in sys.modules:
        del sys.modules[_name]

from apps import app_motion as M

REGS = (0x00, 0x01, 0x02, 0x03, 0x04, 0x05, 0x06, 0x07, 0x08, 0x09, 0x4D)
NAMES = {0x00: "WHO_AM_I", 0x01: "REVISION", 0x02: "CTRL1", 0x03: "CTRL2",
         0x04: "CTRL3", 0x05: "CTRL4", 0x06: "CTRL5", 0x07: "CTRL6",
         0x08: "CTRL7", 0x09: "CTRL8", 0x4D: "RST_RESULT"}

imu = M._open_imu()
i2c = imu.i2c
addr = imu.addr
print("addr=0x%02X" % addr)
for reg in REGS:
    try:
        print("  %-11s [0x%02X] = 0x%02X" % (NAMES[reg], reg, i2c.readfrom_mem(addr, reg, 1)[0]))
    except BaseException as e:
        print("  %-11s [0x%02X] read err %s" % (NAMES[reg], reg, e))

AX = 16384.0
GX = 64.0

def stats(vals):
    n = len(vals)
    mean = sum(vals) / n
    var = 0.0
    for v in vals:
        d = v - mean
        var += d * d
    sd = math.sqrt(var / n)
    return mean, sd, max(vals) - min(vals)

def collect(n):
    ax = []
    ay = []
    az = []
    gx = []
    gy = []
    gz = []
    tp = []
    t0 = time.ticks_us()
    for _ in range(n):
        a = imu.read_sample()
        ax.append(a[0])
        ay.append(a[1])
        az.append(a[2])
        gx.append(a[3])
        gy.append(a[4])
        gz.append(a[5])
        tp.append(a[6])
    dt = time.ticks_diff(time.ticks_us(), t0) / 1000.0
    return (ax, ay, az, gx, gy, gz, tp), dt

def report(tag, data, dt):
    ax, ay, az, gx, gy, gz, tp = data
    n = len(ax)
    print("--- %s (%d samples in %.0fms, %.0f Hz eff) ---" % (tag, n, dt, n * 1000.0 / dt))
    for name, arr, unit in (("accX", ax, "g"), ("accY", ay, "g"), ("accZ", az, "g"),
                            ("gyrX", gx, "dps"), ("gyrY", gy, "dps"), ("gyrZ", gz, "dps"),
                            ("temp", tp, "C")):
        m, sd, pp = stats(arr)
        print("  %-5s mean=%+10.5f  sd=%9.5f  pp=%9.5f %s" % (name, m, sd, pp, unit))
    mags = [math.sqrt(ax[i] * ax[i] + ay[i] * ay[i] + az[i] * az[i]) for i in range(n)]
    m, sd, pp = stats(mags)
    print("  |a|   mean=%+10.5f  sd=%9.5f  pp=%9.5f g" % (m, sd, pp))
    tilt = math.degrees(math.acos(max(-1.0, min(1.0, -stats(az)[0] / m))))
    print("  tilt from sensor -Z: %.1f deg" % tilt)

data, dt = collect(800)
report("LPF OFF (current)", data, dt)

i2c.writeto_mem(addr, 0x06, bytes([0x77]))
time.sleep_ms(100)
print("CTRL5 now = 0x%02X" % i2c.readfrom_mem(addr, 0x06, 1)[0])
data, dt = collect(800)
report("LPF ON  (mode 11, BW=13.37% ODR)", data, dt)

i2c.writeto_mem(addr, 0x06, bytes([0x00]))
time.sleep_ms(50)
print("CTRL5 restored = 0x%02X" % i2c.readfrom_mem(addr, 0x06, 1)[0])

M._close_i2c(i2c)
print("PROBE DONE")
