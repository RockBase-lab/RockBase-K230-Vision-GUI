# Read tearing detection: histogram clustering and sample-to-sample
# differences at exactly 256 LSB.

import sys
import time
import math
import ustruct

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("core", "apps.app_motion", "apps", "core.errors"):
    if _name in sys.modules:
        del sys.modules[_name]

from apps import app_motion as M

imu = M._open_imu()
i2c = imu.i2c
addr = imu.addr
print("addr=0x%02X CTRL1=0x%02X (0x40=ADDR_AI)" %
      (addr, i2c.readfrom_mem(addr, 0x02, 1)[0]))

time.sleep_ms(300)
print("warm-up 300ms done")

def stats(vals):
    n = len(vals)
    mean = sum(vals) / n
    var = 0.0
    for v in vals:
        d = v - mean
        var += d * d
    return mean, math.sqrt(var / n), max(vals) - min(vals)

def collect_bytewise(n):
    ax = []
    ay = []
    az = []
    gx = []
    gy = []
    gz = []
    for _ in range(n):
        d = bytes([i2c.readfrom_mem(addr, 0x35 + i, 1)[0] for i in range(6)])
        x, y, z = ustruct.unpack("<hhh", d)
        ax.append(x / 16384.0)
        ay.append(y / 16384.0)
        az.append(z / 16384.0)
        d = bytes([i2c.readfrom_mem(addr, 0x3B + i, 1)[0] for i in range(6)])
        x, y, z = ustruct.unpack("<hhh", d)
        gx.append(x / 64.0)
        gy.append(y / 64.0)
        gz.append(z / 64.0)
    return ax, ay, az, gx, gy, gz

def collect_burst(n):
    ax = []
    ay = []
    az = []
    gx = []
    gy = []
    gz = []
    for _ in range(n):
        x, y, z = ustruct.unpack("<hhh", i2c.readfrom_mem(addr, 0x35, 6))
        ax.append(x / 16384.0)
        ay.append(y / 16384.0)
        az.append(z / 16384.0)
        x, y, z = ustruct.unpack("<hhh", i2c.readfrom_mem(addr, 0x3B, 6))
        gx.append(x / 64.0)
        gy.append(y / 64.0)
        gz.append(z / 64.0)
    return ax, ay, az, gx, gy, gz

def report(tag, data, dt, n):
    ax, ay, az, gx, gy, gz = data
    print("--- %s (%d in %.0fms, %.0f Hz) ---" % (tag, n, dt, n * 1000.0 / dt))
    for name, arr, unit in (("accX", ax, "g"), ("accY", ay, "g"), ("accZ", az, "g"),
                            ("gyrX", gx, "dps"), ("gyrY", gy, "dps"), ("gyrZ", gz, "dps")):
        m, sd, pp = stats(arr)
        print("  %-5s mean=%+9.4f sd=%8.4f pp=%8.4f %s" % (name, m, sd, pp, unit))
    mags = [math.sqrt(ax[i] ** 2 + ay[i] ** 2 + az[i] ** 2) for i in range(n)]
    m, sd, pp = stats(mags)
    print("  |a|   mean=%+9.4f sd=%8.4f pp=%8.4f g" % (m, sd, pp))

N = 600
t0 = time.ticks_us()
d = collect_bytewise(N)
report("bytewise (current)", d, time.ticks_diff(time.ticks_us(), t0) / 1000.0, N)

t0 = time.ticks_us()
d = collect_burst(N)
report("burst 6B (ADDR_AI)", d, time.ticks_diff(time.ticks_us(), t0) / 1000.0, N)

i2c.writeto_mem(addr, 0x06, bytes([0x77]))
time.sleep_ms(200)
t0 = time.ticks_us()
d = collect_burst(N)
report("burst + LPF mode11", d, time.ticks_diff(time.ticks_us(), t0) / 1000.0, N)
i2c.writeto_mem(addr, 0x06, bytes([0x00]))
print("CTRL5 restored = 0x%02X" % i2c.readfrom_mem(addr, 0x06, 1)[0])

M._close_i2c(i2c)
print("PROBE DONE")
