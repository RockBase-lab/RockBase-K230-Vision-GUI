# History-row fill under a simulated slow loop.
# Sleeps 45 ms per round and counts blank rows to verify the per-frame
# row budget.

import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("apps.app_motion", "apps", "apps.error", "launcher"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv

src_file = open("/sdcard/factory/gui/launcher.py")
gui_src = src_file.read()
src_file.close()
del src_file

G = {"__name__": "gui_probe"}
exec(compile(gui_src, "launcher.py", "exec"), G)

launcher = G["Launcher"]()
meta = None
for item in G["APPS"]:
    if item["id"] == "motion":
        meta = item
        break
launcher.open_app(meta)
app = launcher.active_app

t0 = time.ticks_ms()
frames = 0
while time.ticks_diff(time.ticks_ms(), t0) < 3000:
    launcher.active_app.step()
    lv.task_handler()
    frames += 1
    time.sleep_ms(45)
elapsed = time.ticks_diff(time.ticks_ms(), t0)
print("slow loop: {} frames / {}ms -> {:.1f} fps".format(
    frames, elapsed, 1000.0 * frames / elapsed))

blank = 0
for i, row in enumerate(app._row_labels):
    text = row[0].get_text()
    if not text.strip():
        blank += 1
    print("  r{} y={} text={!r}".format(i, row[0].get_y(), text))
print("blank history rows = {} -> {}".format(
    blank, "ALL FILLED" if blank == 0 else "MISSING ROWS"))
print("footer = {!r}".format(app._foot.get_text()))
print("PROBE DONE")
