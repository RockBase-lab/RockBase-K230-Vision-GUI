# Prints the firmware identity this project was verified on. Read-only: no sensor,
# no display, no lvgl, so it cannot disturb whatever the GUI is doing.
#
# On a correctly flashed board this reports machine='k230_canmv_rockbase', which is how
# docs/firmware.md tells you whether the right board configuration was built.

import gc
import os

print("uname:", os.uname())

for name in ("version", "machine"):
    try:
        module = __import__(name)
        print("{}: {}".format(name, dir(module)))
    except BaseException as error:
        print("{}: n/a ({})".format(name, error))

try:
    print("mem_free:", gc.mem_free())
except BaseException as error:
    print("mem_free: n/a ({})".format(error))

print("DONE")
