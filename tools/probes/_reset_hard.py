# Hard reset (L2): clears the Sensor singleton, which neither stop()/deinit() nor a
# Ctrl-D soft reset can release. Use before a probe when the board reports
# "sensor(N) is already inited" while no probe of yours is running: the running GUI
# holds the sensor, and only machine.reset() returns it.
#
# The serial link drops as the board reboots. That is the reset taking effect, not a
# failure; the GUI autostarts afterwards and sits on the desktop with no sensor held.

import machine
import time

print("[RESET] machine.reset() in 1s; the serial link will drop")
time.sleep_ms(1000)
machine.reset()
