# Batch greeting build: queue every identity without a WAV and let the app's
# step() worker build them, verifying memory stays sane across the batch.
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _m in list(sys.modules):
    if _m.startswith("apps.") or _m.startswith("audio."):
        del sys.modules[_m]

import lvgl as lv

_f = open("/sdcard/factory/gui/launcher.py")
_src = _f.read()
_f.close()
del _f
G = {"__name__": "gui_probe"}
exec(compile(_src, "launcher.py", "exec"), G)

meta = None
for item in G["FOLDER_MEMBERS"]["face"]:
    if item["id"] == "recognize":
        meta = item
        break

launcher = G["Launcher"]()
launcher.open_app(meta)
app = launcher.active_app
print("V0 app={} missing={}".format(
    app is not None, app._missing_voices()))

t0 = time.ticks_ms()
app._queue_voices(app._missing_voices())
print("V1 queued={} free={}".format(app._voice_total, G["gc"].mem_free()
                                    if "gc" in G else -1))

frames = 0
while app._voice_queue and time.ticks_diff(time.ticks_ms(), t0) < 180000:
    if app._step_voice():
        frames += 1
        print("V2 built {} free={}".format(
            app._voice_done, __import__("gc").mem_free()))
    else:
        break
print("V3 elapsed={}ms done={}/{} synth={}".format(
    time.ticks_diff(time.ticks_ms(), t0), app._voice_done,
    app._voice_total, app._synth))

import os
VOICE = "/data/face/recognize/voice"
print("V4 missing now={}".format(app._missing_voices()))
try:
    print("V5 files={}".format(sorted(os.listdir(VOICE))))
except BaseException as e:
    print("V5 list failed:", e)

# Play one of them to confirm the greeter path works with real identity names.
app.greeter.reset()
started = False
for i in range(12):
    if app.greeter.update(app._db_names_unique()[0], True):
        started = True
        break
print("V6 greeter started={} busy={}".format(started, app.greeter.busy()))
if started:
    t1 = time.ticks_ms()
    while app.greeter.busy() and time.ticks_diff(time.ticks_ms(), t1) < 12000:
        app.greeter.pump()
        time.sleep_ms(39)
    print("V7 played {} ms, frames={}".format(
        time.ticks_diff(time.ticks_ms(), t1), app.greeter.player.frames))
app.greeter.stop()

launcher.close_app()
print("V8 closed")
