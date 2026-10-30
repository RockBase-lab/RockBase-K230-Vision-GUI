# Accelerometer calibration UI capture.
# Drives the app through menu, prompt, first face and result states
# with a fake context, then snapshots each state.

import sys
sys.path.insert(0, "/sdcard/factory/gui")
for _k in list(sys.modules):
    if "app_imu" in _k or _k == "apps":
        del sys.modules[_k]

import lvgl as lv
import uctypes
import ubinascii
from ui import beep

beep.init()

CONTENT_W, CONTENT_H = 800, 424

class FakeCtx(object):
    def __init__(self):
        self.width = CONTENT_W
        self.height = CONTENT_H
        self.screen_w = 800
        self.screen_h = 480
        self.content_x = 0
        self.content_y = 56
        self.app_name = "IMU"
        self.want_exit = False
        self.touch = None
        self.ok_pressed = False
        self.ok_beep = True
        self.back_handler = None
        self.unavailable_msg = None

    def take_ok(self):
        if self.ok_pressed:
            self.ok_pressed = False
            return True
        return False

    def report_unavailable(self, message):
        self.unavailable_msg = message

root = lv.obj()
root.set_size(CONTENT_W, CONTENT_H)
root.set_style_bg_color(lv.color_hex(0x10141C), 0)
root.set_style_pad_all(0, 0)
lv.scr_load(root)

import apps.app_imu as m

app = m.ImuApp(root, FakeCtx())

def snap(tag):
    lv.task_handler()
    lv.task_handler()
    panel = app._ov_panel
    if panel is None:
        print("NO PANEL for", tag)
        return
    W, H = panel.get_width(), panel.get_height()
    d = lv.snapshot_take(panel, lv.COLOR_FORMAT.ARGB8888)
    ptr = int.from_bytes(bytes(memoryview(d.data)), "little")
    raw = uctypes.bytearray_at(ptr, W * H * 4)
    enc = bytearray()
    for y in range(H):
        row = y * W * 4
        i = 0
        while i < W:
            o = row + i * 4
            b, g, r = raw[o], raw[o + 1], raw[o + 2]
            m1 = 1
            while m1 < 256 and i + m1 < W:
                o2 = row + (i + m1) * 4
                if raw[o2] != b or raw[o2 + 1] != g or raw[o2 + 2] != r:
                    break
                m1 += 1
            enc += bytes([m1 - 1, b, g, r])
            i += m1
    b64 = ubinascii.b2a_base64(bytes(enc)).decode().strip()
    print("SHOT {} {} {} {} {}".format(tag, W, H, W * H * 4, len(enc)))
    k = 0
    while k < len(b64):
        print("B64 " + b64[k:k + 800])
        k += 800
    print("SHOTEND " + tag)

app._enter_menu()
snap("menu")

app._enter_acc_prompt("No accelerometer calibration found.")
snap("acc_prompt")

import time as _t
app._acc_action = "start"
app._acc_tick(_t.ticks_ms())
snap("face1")

app._state = m.ST_ACC_RESULT
app._set_overlay(
    "Residual high",
    "Residual: 12 mg\n\nRetry all or keep anyway?",
    (("Retry", 0x16A34A, "retry"), ("Keep", 0x374151, "keep"),
     ("Back", 0x374151, "back")))
snap("result")

print("PROBE DONE")
