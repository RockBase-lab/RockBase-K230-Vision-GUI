# Dumps the 106 landmark screen coordinates of one real face.
#
# The triangulation of the mesh must be computed from real geometry, so this
# prints the point set in a machine-readable form. Run with --lvgl and a single
# face straight at the camera at normal distance.

import gc
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("core", "apps.app_face_points", "apps.app_face_det", "apps",
              "core.errors", "ui.beep", "launcher"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv

_f = open("/sdcard/factory/gui/launcher.py")
gui_src = _f.read()
_f.close()
del _f

G = {"__name__": "gui_probe"}
exec(compile(gui_src, "launcher.py", "exec"), G)
# The tables and classes live in shell/ after the layer split; importing them
# here also keeps sys.path bootstrapped by the exec above.
from shell.registry import FOLDER_MEMBERS
from shell.home import Launcher
from shell import tile_cache

tile_cache.ensure_cache_dir()

launcher = Launcher()
meta = None
for item in FOLDER_MEMBERS["face"]:
    if item["id"] == "landmark":
        meta = item
        break
launcher.open_app(meta)
app = launcher.active_app
print("D1 app up")

print("D2 a single face straight at the camera, normal distance, 6 s ...")
for s in range(6, 0, -1):
    print("   ", s)
    time.sleep_ms(1000)

best = None
for attempt in range(20):
    app.step()
    lv.task_handler()
    if app._have_pts:
        best = (list(app._kx), list(app._ky))
        break

if best is None:
    print("D3 no face")
else:
    xs, ys = best
    print("D3 pts={} x=[{:.0f},{:.0f}] y=[{:.0f},{:.0f}]".format(
        len(xs), min(xs), max(xs), min(ys), max(ys)))
    print("D4 BEGIN")
    for k in range(len(xs)):
        print("PT {} {:.2f} {:.2f}".format(k, xs[k], ys[k]))
    print("D5 END")

launcher.close_app()
print("D6 closed")
print("RESULT: DONE")
