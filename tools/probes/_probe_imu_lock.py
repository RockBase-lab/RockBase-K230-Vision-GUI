# Locking (shadow register) sequence validation.
# Compares peak-to-peak and outlier counts against unlocked reads.

import sys
import time
import math
import ustruct

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("apps.app_motion", "apps", "apps.error"):
    if _name in sys.modules:
        del sys.modules[_name]

from apps import app_motion as M

CTRL9 = 0x0A
CAL1_L = 0x0B
STATUSINT = 0x2D
CTRL7 = 0x08
CTRL5 = 0x06
CMD_AHB_CLOCK_GATING = 0x12

imu = M._open_imu()
i2c = imu.i2c
addr = imu.addr
time.sleep_ms(300)

def cmd9(code):
    i2c.writeto_mem(addr, CTRL9, bytes([code]))
    for _ in range(50):
        st = i2c.readfrom_mem(addr, STATUSINT, 1)[0]
        if st & 0x80:
            break
        time.sleep_ms(1)
    i2c.writeto_mem(addr, CTRL9, bytes([0x00]))

def stats(vals):
    n = len(vals)
    mean = sum(vals) / n
    var = 0.0
    for v in vals:
        d = v - mean
        var += d * d
    return mean, math.sqrt(var / n), max(vals) - min(vals)

def hist_report(tag, arr):
    s = sorted(arr)
    med = s[len(s) // 2]
    big = 0
    worst = 0
    for i in range(1, len(arr)):
        d = arr[i] - arr[i - 1]
        ad = d if d >= 0 else -d
        if ad > worst:
            worst = ad
        if ad >= 200:
            big += 1
    hist = {}
    for v in arr:
        b = (v - med) // 32
        hist[b] = hist.get(b, 0) + 1
    print("  %s median=%d max|delta|=%d count>=200:%d/%d" %
          (tag, med, worst, big, len(arr) - 1))
    print("     hist32: " + " ".join("%+d:%d" % (b * 32, hist[b])
                                     for b in sorted(hist)))

N = 400
ax = []
ay = []
az = []
gx = []
gy = []
gz = []
for _ in range(N):
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

print("=== BASELINE bytewise, no lock ===")
for name, arr, unit in (("accX", ax, "g"), ("accZ", az, "g"),
                        ("gyrX", gx, "dps"), ("gyrY", gy, "dps"), ("gyrZ", gz, "dps")):
    m, sd, pp = stats(arr)
    print("  %-5s mean=%+9.4f sd=%8.4f pp=%8.4f %s" % (name, m, sd, pp, unit))

i2c.writeto_mem(addr, CAL1_L, bytes([0x01]))
cmd9(CMD_AHB_CLOCK_GATING)
print("AHB gating disabled, CAL1_L=0x%02X" % i2c.readfrom_mem(addr, CAL1_L, 1)[0])
i2c.writeto_mem(addr, CTRL7, bytes([0x83]))
print("CTRL7=0x%02X (syncSmpl=1, aEN=1, gEN=1)" % i2c.readfrom_mem(addr, CTRL7, 1)[0])
time.sleep_ms(50)

t0 = time.ticks_us()
ax = []
ay = []
az = []
gx = []
gy = []
gz = []
tp = []
stall = 0
for _ in range(N):

    ok = False
    for _try in range(200):
        st = i2c.readfrom_mem(addr, STATUSINT, 1)[0]
        if st & 0x01:
            ok = True
            break
    if not ok:
        stall += 1
        continue

    st = i2c.readfrom_mem(addr, STATUSINT, 1)[0]
    if not (st & 0x02):
        stall += 1
        continue
    t = ustruct.unpack("<h", i2c.readfrom_mem(addr, 0x33, 2))[0]
    x, y, z = ustruct.unpack("<hhh", i2c.readfrom_mem(addr, 0x35, 6))
    ax.append(x / 16384.0)
    ay.append(y / 16384.0)
    az.append(z / 16384.0)
    x, y, z = ustruct.unpack("<hhh", i2c.readfrom_mem(addr, 0x3B, 6))
    gx.append(x / 64.0)
    gy.append(y / 64.0)
    gz.append(z / 64.0)
    tp.append(t / 256.0)
dt = time.ticks_diff(time.ticks_us(), t0) / 1000.0
print("=== LOCKING MECHANISM (%d good / %d, %.1f Hz, stalls=%d) ===" %
      (len(ax), N, len(ax) * 1000.0 / dt, stall))
for name, arr, unit in (("accX", ax, "g"), ("accY", ay, "g"), ("accZ", az, "g"),
                        ("gyrX", gx, "dps"), ("gyrY", gy, "dps"), ("gyrZ", gz, "dps"),
                        ("temp", tp, "C")):
    m, sd, pp = stats(arr)
    print("  %-5s mean=%+9.4f sd=%8.4f pp=%8.4f %s" % (name, m, sd, pp, unit))
mags = [math.sqrt(ax[i] ** 2 + ay[i] ** 2 + az[i] ** 2) for i in range(len(ax))]
m, sd, pp = stats(mags)
print("  |a|   mean=%+9.4f sd=%8.4f pp=%8.4f g" % (m, sd, pp))

print("=== LOCKING: LSB histogram ===")
hist_report("gyrY", [int(round(v * 64)) for v in gy])
hist_report("gyrZ", [int(round(v * 64)) for v in gz])
hist_report("accX", [int(round(v * 16384)) for v in ax])

i2c.writeto_mem(addr, CTRL7, bytes([0x03]))
time.sleep_ms(20)
i2c.writeto_mem(addr, CAL1_L, bytes([0x00]))
cmd9(CMD_AHB_CLOCK_GATING)
print("restored CTRL7=0x%02X CAL1_L=0x%02X" %
      (i2c.readfrom_mem(addr, CTRL7, 1)[0], i2c.readfrom_mem(addr, CAL1_L, 1)[0]))
M._close_i2c(i2c)
print("PROBE DONE")
