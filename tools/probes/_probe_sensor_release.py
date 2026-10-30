# Lists the sensor device registry, tries stop/deinit on each entry, and
# then constructs a new sensor to see whether the singleton is freed.

import gc
import sys
import time

print("P1 importing")
from media.sensor import Sensor

devs = getattr(Sensor, "_devs", None)
print("P2 _devs=", devs)
if devs:
    for i in range(len(devs)):
        s = devs[i]
        print("   dev[{}]={}".format(i, s))
        if s is None:
            continue
        for name in ("stop", "deinit"):
            try:
                getattr(s, name)()
                print("      {} ok".format(name))
            except BaseException as e:
                print("      {} err {}".format(name, e))
gc.collect()
time.sleep_ms(200)

try:
    s2 = Sensor(fps=30)
    print("P3 construct ok", s2)
    for name in ("reset",):
        getattr(s2, name)()
    print("P4 reset ok")
    s2.deinit()
    print("P5 deinit ok")
except BaseException as e:
    print("P3 construct fail:", e)
