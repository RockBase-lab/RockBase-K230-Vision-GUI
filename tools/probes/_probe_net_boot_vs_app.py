# Why does the boot attempt fail while a manual connect from the Wi-Fi app works?
#
# Calls the two paths back to back in one process, in the same order the board sees
# them, and prints the outcome and the timing of each. The only differences between
# them are the parameters _start_connect receives, so whichever one flips the result
# is the cause.
#
#   A) boot path:   scan -> require_ap gate -> w.connect() -> ONE isconnected check
#   B) app path:    scan -> w.connect() -> poll isconnected for up to 5 s
#
# Run without --lvgl: no widget is created, so nothing can wedge the display.

import gc
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("net", "net.service"):
    if _name in sys.modules:
        del sys.modules[_name]

import net.service as n

n.load()
ssid = n.cfg.get("ssid", "")
pwd = n.cfg.get("password", "")
print("W0 ssid={!r} pwd_len={} state={}".format(ssid, len(pwd), n.state))
if not ssid:
    print("RESULT: no saved network, nothing to test")
    raise SystemExit

# Start from a genuinely disconnected radio: on a warm VM the firmware-level link
# survives, boot_start would adopt it, and the failure this probe exists to find
# would never be reproduced.
try:
    n.disconnect()
except BaseException as error:
    print("W0b disconnect error {}".format(error))
limit = time.ticks_add(time.ticks_ms(), 8000)
while n.state == n.CONNECTED and time.ticks_diff(limit, time.ticks_ms()) > 0:
    time.sleep_ms(100)
print("W0c after disconnect state={} busy={}".format(n.state, n.busy()))

# ---- A) boot path ----
t0 = time.ticks_ms()
try:
    waiting = n.boot_start()
    print("W1 boot_start -> {} ({}ms)".format(
        waiting, time.ticks_diff(time.ticks_ms(), t0)))
    if waiting:
        outcome = n.boot_wait(15000)
        boot_ms = time.ticks_diff(time.ticks_ms(), t0)
        print("W2 boot path outcome={} {}ms state={} err={!r}".format(
            outcome, boot_ms, n.state, n.last_error))
    else:
        print("W2 boot path skipped (state={})".format(n.state))
except BaseException as error:
    print("W2 boot path EXC {}".format(error))
    try:
        sys.print_exception(error)
    except BaseException:
        pass

print("W3 after boot path: state={} worker_busy={}".format(
    n.state, n.busy()))

# Let any boot worker finish before touching the radio again, otherwise the second
# attempt races the first one inside the driver.
limit = time.ticks_add(time.ticks_ms(), 20000)
while n.busy() and time.ticks_diff(limit, time.ticks_ms()) > 0:
    time.sleep_ms(100)
print("W4 worker clear={} state={}".format(not n.busy(), n.state))

# ---- B) app path, exactly what the Wi-Fi app button does ----
t0 = time.ticks_ms()
try:
    n.connect(ssid, pwd)
    deadline = time.ticks_add(time.ticks_ms(), 20000)
    while time.ticks_diff(deadline, time.ticks_ms()) > 0:
        n.poll()
        if n.state == n.CONNECTED:
            break
        if n.state == n.GAVE_UP:
            break
        time.sleep_ms(200)
    app_ms = time.ticks_diff(time.ticks_ms(), t0)
    print("W5 app path outcome state={} {}ms ip={!r} err={!r}".format(
        n.state, app_ms, n.ip(), n.last_error))
except BaseException as error:
    print("W5 app path EXC {}".format(error))
    try:
        sys.print_exception(error)
    except BaseException:
        pass

print("W6 final state={} busy={} ip={!r}".format(n.state, n.busy(), n.ip()))

# Leave the radio in a known state for whatever runs next.
try:
    n.disconnect()
except BaseException:
    pass
gc.collect()
print("RESULT: DONE")
