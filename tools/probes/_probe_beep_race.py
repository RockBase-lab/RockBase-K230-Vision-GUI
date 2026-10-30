# Click-sound off-time race under a blocking C call.
# Samples the off timestamp at callback entry and exit while a scan
# blocks for about 1.5 s.

import time
import sys
import lvgl as lv

sys.path.insert(0, "/sdcard/factory/gui")
from ui import beep

beep.init()
beep.set_busy_check(lambda: False)

btn = lv.btn(lv.scr_act())
btn.set_size(100, 60)
btn.center()

state = {"calls": 0, "off_at_cb_entry": None, "t_cb_entry": 0}

def blocking_cb(event):
    state["calls"] += 1
    state["off_at_cb_entry"] = beep._off_ms
    state["t_cb_entry"] = time.ticks_ms()
    import network
    w = network.WLAN(network.STA_IF)
    t0 = time.ticks_ms()
    try:
        w.scan()
    except Exception as e:
        print("scan err:", e)
    print("scan took {}ms".format(time.ticks_diff(time.ticks_ms(), t0)))

beep.on_click(btn, blocking_cb)

t0 = time.ticks_ms()
btn.send_event(lv.EVENT.CLICKED, None)
print("calls={}".format(state["calls"]))
print("off_at_cb_entry={} (0=polled clean before callback)".format(
    state["off_at_cb_entry"]))
print("off_after_callback={} now-t_cb={}".format(
    beep._off_ms, time.ticks_diff(time.ticks_ms(), state["t_cb_entry"])))

btn.delete()
for i in range(2):
    lv.task_handler()
print("done")
