# Headless probe: build the Settings app, report geometry, and save one snapshot.
#
# Run on board with --lvgl (the runner injects Display.init + lv.init + disp set up).
import base64
import gc
import sys

sys.path.insert(0, "/sdcard/factory/gui")

for m in ("apps.app_settings", "config"):
    try:
        del sys.modules[m]
    except KeyError:
        pass

import lvgl as lv
import config
from apps import app_settings


class Ctx:
    width = 800
    height = 424
    content_x = 0
    content_y = 56
    app_name = "Settings"
    touch = None

    def request_exit(self):
        pass


scr = lv.obj()
scr.set_style_bg_color(lv.color_hex(0x12141A), 0)
scr.set_style_bg_opa(lv.OPA.COVER, 0)
content = lv.obj(scr)
content.set_size(800, 424)
content.align(lv.ALIGN.TOP_MID, 0, 56)
content.set_style_bg_opa(lv.OPA.TRANSP, 0)
content.set_style_border_width(0, 0)
content.set_style_pad_all(0, 0)
content.clear_flag(lv.obj.FLAG.SCROLLABLE)

lv.scr_load(scr)
app = app_settings.create_app(content, Ctx())
lv.task_handler()
lv.task_handler()

print("TOGGLES", len(app._toggles))
print("STEPPERS", len(app._steppers))
print("LIVE", len(app._live))
print("ACTIONS", len(app._actions))
try:
    cnt = app.scroll.get_child_count()
except Exception:
    try:
        cnt = app.scroll.get_child_cnt()
    except Exception as e:
        cnt = -1
        print("CHILD_CNT_ERR", e)
print("SCROLL_CHILD_CNT", cnt)
try:
    print("SCROLL_H", app.scroll.get_height())
except Exception as e:
    print("SCROLL_H_ERR", e)
# Last child y tells us whether the content is taller than the 408px viewport
# (it must be, otherwise there is nothing to scroll and a row is clipped).
try:
    last = app.scroll.get_child(cnt - 1)
    print("LAST_CHILD_Y", last.get_y(), "H", last.get_height())
except Exception as e:
    print("LAST_CHILD_ERR", e)

# Exercise every preference once, then restore the file the board came with.
key = "ui.click_sound"
before = config.get_bool(key, True)
app._on_toggle(key, app._toggles[0][1])
after = config.get_bool(key, True)
print("TOGGLE_TEST", before, "->", after)
app._on_toggle(key, app._toggles[0][1])
print("TOGGLE_RESTORED", config.get_bool(key, True))

item = app._steppers[0][0]
config.set("display.brightness", 80)
app._on_step(item, -1)
print("STEP_TEST_DOWN", config.get_int("display.brightness", 100))
config.set("display.brightness", 100)
app._on_step(item, 1)
print("STEP_TEST_CLAMP", config.get_int("display.brightness", 100))
config.set("display.brightness", 100)

for _ in range(3):
    lv.task_handler()

print("FREE_KB", gc.mem_free() // 1024)

# One snapshot, RLE + base64 so the tool output stays small.
w = 800
h = 480
dsc = lv.snapshot_take(lv.scr_act(), lv.COLOR_FORMAT.ARGB8888)
ptr = int.from_bytes(bytes(memoryview(dsc.data)), "little")
import uctypes
raw = uctypes.bytearray_at(ptr, w * h * 4)

# Keep only a coarse signature instead of the full image: one base64 blob of
# the full frame overflows the tool output and gets truncated.
hits = 0
lum = 0
step = 4 * 37
i = 0
n = len(raw)
while i < n:
    lum += raw[i] + raw[i + 1] + raw[i + 2]
    hits += 1
    i += step
print("PIX_SAMPLES", hits)
print("PIX_MEAN_LUM", lum // (hits * 3))
try:
    dsc.buf_free()
except Exception:
    pass
print("PROBE_DONE")
