# Exit path for the vision app without a snapshot, because capturing
# after close_app hangs on sensor stop.

import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("apps.app_vision", "apps", "app_error", "gui"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv

_f = open("/sdcard/factory/gui/launcher.py")
_src = _f.read()
_f.close()
del _f

G = {"__name__": "gui_probe"}
exec(compile(_src, "launcher.py", "exec"), G)
G["_ensure_cache_dir"]()

launcher = G["Launcher"]()
meta = None
for item in G["FOLDER_MEMBERS"]["vision"]:
    if item["id"] == "detect":
        meta = item
        break

launcher.open_app(meta)
print("A1 app={} ctx={}x{}".format(launcher.active_app is not None,
                                   launcher.ctx.width, launcher.ctx.height))
for _ in range(8):
    lv.task_handler()
    launcher.active_app.step()
    time.sleep_ms(100)
print("A2 frames ok fps={} objs={}".format(launcher.active_app.fps_text,
                                           launcher.active_app.last_objs))

launcher.ctx.request_exit()
launcher.close_app()
print("A3 closed active_app={} app_scr={} home_scr={}".format(
    launcher.active_app, launcher.app_scr, launcher.home_scr is not None))
print("A4 main-style exit ok")
