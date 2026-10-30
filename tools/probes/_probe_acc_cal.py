# Six-face accelerometer calibration logic, no hardware.
# Builds ideal face means, runs compute/verify, and round-trips the
# calibration file to check the stored form.
# Run: python tools/_run_remote.py --lvgl tools/probes/_probe_acc_cal.py

import sys

sys.path.insert(0, "/sdcard/factory/gui")

for _k in list(sys.modules):
    if "app_imu" in _k or _k == "apps":
        del sys.modules[_k]

import apps.app_imu as m

print("== load existing acc_cal ==")
print("load_acc_cal ->", m.load_acc_cal())

bias = [0.02, -0.015, 0.008]
scale = [1.005, 0.997, 1.011]

def face(axis, sign):

    v = [bias[0], bias[1], bias[2]]
    v[axis] = sign * scale[axis] + bias[axis]
    return tuple(v)

means = [None] * 7

for ip, im, axis in m.ACC_PAIRS:
    means[ip] = face(axis, +1)
    means[im] = face(axis, -1)
means[6] = face(2, -1)

holder = type("H", (), {})()
holder._acc_means = means
holder._acc_temp = 48.0 * 20
holder._acc_n = 20
got = m.ImuApp._acc_compute(holder)
print("== compute ==")
print("bias  got =", [round(x, 4) for x in got[0]], " want", bias)
print("scale got =", [round(x, 4) for x in got[1]], " want", scale)

holder._pending = got
mag_err, h_err = m.ImuApp._acc_verify(holder, means[6])
print("== verify residual ==")
print("mag_err=%.4f g  h_err=%.4f g" % (mag_err, h_err))

ok = m.save_acc_cal(got[0], got[1], got[2])
print("save ok:", ok)
back = m.load_acc_cal()
print("roundtrip:", back)
if back:
    print("bias rt =", [round(x, 4) for x in back[0]])
    print("scale rt=", [round(x, 4) for x in back[1]])

try:
    import os
    os.remove(m.ACC_CAL_PATH)
    print("cleanup removed", m.ACC_CAL_PATH)
except BaseException as e:
    print("cleanup:", e)
print("DONE")
