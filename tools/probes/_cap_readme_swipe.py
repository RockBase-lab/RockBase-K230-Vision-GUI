# Captures the desktop page swipe as a sequence of stills for the README GIF.
#
# Why stills instead of a recording: one snapshot costs 1-2 s on this board (snapshot
# to buffer, channel swap, JPEG encode, serial transfer), so a 180 ms animation can
# never be sampled live. Each frame here is a real render at a real scroll offset from
# the production pager, and the PC stitches them back at the animation's own cadence.
#
# The status bar and the page dots do not scroll with the grid in the real UI, so the
# probe leaves them alone: only launcher.pager.wrap is scrolled. The dots are updated
# through the same on_page_changed path the pager uses.
#
# Output: one JPEG per step in /data/capture/swipe_<n>.jpg plus a MANIFEST line with
# the offsets, so the PC side does not have to guess the order or the timing.

import os
import sys
import time
import uctypes

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("core", "apps", "core.errors", "ui.beep", "ui.paging", "ui.splash",
              "hardware.backlight", "launcher", "core.config"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv
import image
import ulab.numpy as np

_f = open("/sdcard/factory/gui/launcher.py")
_src = _f.read()
_f.close()
del _f

G = {"__name__": "gui_probe"}
exec(compile(_src, "launcher.py", "exec"), G)
G["_ensure_cache_dir"]()

OUT = "/data/capture"

# Offsets in px from page 0. page_w is 800, so these sweep a full page.
# The tail values ease into the new page the way SNAP_ANIM_MS=180 does; the list
# itself is the frame order, so the PC side plays it as-is.
STEPS = [0, 40, 96, 168, 256, 360, 472, 592, 688, 752, 780, 794, 799, 800]


def ensure_dir(path):
    cur = ""
    for part in path.strip("/").split("/"):
        cur += "/" + part
        try:
            os.mkdir(cur)
        except OSError:
            pass


ensure_dir(OUT)


def save_jpeg(tag):
    for _ in range(2):
        lv.task_handler()
        time.sleep_ms(40)
    dsc = lv.snapshot_take(lv.scr_act(), lv.COLOR_FORMAT.ARGB8888)
    w, h = dsc.header.w, dsc.header.h
    ptr = int.from_bytes(bytes(memoryview(dsc.data)), "little")
    raw = uctypes.bytearray_at(ptr, w * h * 4)
    try:
        img = image.Image(w, h, image.RGB888)
        dst = img.to_numpy_ref()
        src = np.frombuffer(raw, dtype=np.uint8).reshape((h, w, 4))
        dst[:, :, 0] = src[:, :, 2]
        dst[:, :, 1] = src[:, :, 1]
        dst[:, :, 2] = src[:, :, 0]
        del src
        data = img.compressed(80).bytearray()
        path = OUT + "/" + tag + ".jpg"
        with open(path, "wb") as f:
            f.write(data)
        print("[CAP] {} bytes={}".format(tag, len(data)))
        del data
        del img
    finally:
        dsc.buf_free()


launcher = G["Launcher"]()
launcher.build_home()

pager = launcher.pager
if pager is None:
    print("RESULT NO-PAGER (only one page; nothing to swipe)")
else:
    wrap = pager.wrap
    print("[SWIPE] page_w={} pages={}".format(pager.page_w, pager.page_count))

    # Page dots switch when the target page is decided, which happens at the start of
    # the snap animation, not at the end. Mirror that: dots flip at the halfway point
    # of the sweep, the same moment the real pager commits.
    halfway = pager.page_w // 2
    dots_done = False

    for index, offset in enumerate(STEPS):
        wrap.scroll_to_x(offset, lv.ANIM.OFF)
        if not dots_done and offset >= halfway:
            pager.go(1, animate=False)
            wrap.scroll_to_x(offset, lv.ANIM.OFF)
            dots_done = True
        lv.task_handler()
        time.sleep_ms(30)
        save_jpeg("swipe_{:02d}".format(index))
        print("[SWIPE] step={} offset={}".format(index, offset))

    print("MANIFEST {}".format(",".join(str(v) for v in STEPS)))

print("DONE")
