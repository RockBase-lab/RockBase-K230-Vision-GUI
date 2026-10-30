# Keyboard layer snapshot: build, force the symbol page, poll once so it
# renders, then snapshot.

import sys
sys.path.insert(0, "/sdcard/factory/gui")
import lvgl as lv
import uctypes
import ubinascii

for _m in ("ui.kb_layer", "ui.beep"):
    if _m in sys.modules:
        del sys.modules[_m]
from ui import kb_layer

scr = lv.obj()
scr.set_size(800, 480)
lv.scr_load(scr)

kbobj = kb_layer.KbLayer(title="test", on_ok=lambda: None, password=False)
lv.task_handler()
lv.task_handler()
kbobj.kb.set_mode(3)
for _ in range(3):
    lv.task_handler()
kbobj.poll(None)
for _ in range(2):
    lv.task_handler()

b = kbobj._badge
print("badge pos=({},{}) size={}x{} hidden={}".format(
    b.get_x(), b.get_y(), b.get_width(), b.get_height(),
    bool(b.has_flag(lv.obj.FLAG.HIDDEN)) if hasattr(b, "has_flag") else "?"))
src = open("/sdcard/factory/gui/ui/kb_layer.py", "rb").read()
print("board kb_layer has 291x65:", b"291, 65" in src, "| len", len(src))

W, H = 800, 480
dsc = lv.snapshot_take(lv.scr_act(), lv.COLOR_FORMAT.ARGB8888)
ptr = int.from_bytes(bytes(memoryview(dsc.data)), "little")
raw = uctypes.bytearray_at(ptr, W * H * 4)

tag = "kbnum"
enc = bytearray()

for y in range(H):
    row = y * W * 4
    x = 0
    while x < W:
        o = row + x * 4
        b, g, r = raw[o], raw[o + 1], raw[o + 2]
        n = 1
        while n < 256 and x + n < W:
            o2 = row + (x + n) * 4
            if (raw[o2] != b or raw[o2 + 1] != g or raw[o2 + 2] != r):
                break
            n += 1
        enc += bytes([n - 1, b, g, r])
        x += n

b64 = ubinascii.b2a_base64(bytes(enc)).decode().strip()
print("SHOT {} {} {} {} {}".format(tag, W, H, W * H * 4, len(enc)))
i = 0
while i < len(b64):
    print("B64 " + b64[i:i + 800])
    i += 800
print("SHOTEND " + tag)
print("PROBE DONE")
