# Recognize app with the greeting feature: boots, runs frames, no crash.
# Reports the greeter wiring and whether an enrolled identity has a voice file.
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
# The tables and classes live in shell/ after the layer split; importing them
# here also keeps sys.path bootstrapped by the exec above.
from shell.registry import FOLDER_MEMBERS
from shell.home import Launcher


meta = None
for item in FOLDER_MEMBERS["face"]:
    if item["id"] == "recognize":
        meta = item
        break

launcher = Launcher()
launcher.open_app(meta)
app = launcher.active_app
print("R0 app={} voice_on={} greeter={}".format(
    app is not None, app.voice_on, app.greeter is not None))

for i in range(12):
    lv.task_handler()
    app.step()
    time.sleep_ms(60)
lv.task_handler()
print("R1 frames ok fps={} face={} name={}".format(
    app.fps_text, app.has_face, app._name))
print("R2 greeter busy={}".format(app.greeter.busy()))

# Which enrolled identities have a greeting on disk?
import os
VOICE = "/data/face/recognize/voice"
try:
    files = sorted(os.listdir(VOICE))
except BaseException as e:
    files = []
    print("R3 voice dir unreadable:", e)
print("R3 voice files={}".format(files))
for name in app._db_names_unique()[:6]:
    p = VOICE + "/" + name + ".wav"
    try:
        sz = os.stat(p)[6]
    except BaseException:
        sz = -1
    print("R4 {} wav={}".format(name, sz))

launcher.close_app()
print("R5 closed")
