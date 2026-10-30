# QR scan app with a synthetic frame injected through a monkeypatched
# snapshot, so detection works without a camera.

import gc
import sys
import time

import lvgl as lv
import image

sys.path.insert(0, "/sdcard/factory/gui")

QR_DATA = "HELLO-ROCKBASE-SCAN-123"

def load_matrix():
    """Read the QR matrix text pushed to the board for this probe."""
    f = open("/sdcard/_qr_matrix.txt")
    lines = f.read().split("\n")
    f.close()
    n = int(lines[0])
    rows = []
    for line in lines[1:]:
        if not line.strip():
            continue
        row = []
        toks = line.split()
        i = 0
        while i < len(toks):
            r = int(toks[i])

            v = 0 if toks[i + 1] == "1" else 255
            row.extend([v] * r)
            i += 2
        rows.append(row)
    return n, rows

def make_qr_gray(n, rows, scale, margin):
    """Draw the QR matrix into a 640x480 GRAYSCALE frame, code centred."""
    img = image.Image(640, 480, image.GRAYSCALE)

    img.draw_rectangle(0, 0, 640, 480, color=0xFFFFFF, fill=True)
    size = n * scale
    ox = (640 - size) // 2
    oy = (480 - size) // 2
    for y in range(n):
        row = rows[y]
        x = 0
        while x < n:
            v = row[x]
            r = 1
            while x + r < n and row[x + r] == v:
                r += 1
            if v == 0:
                img.draw_rectangle(ox + x * scale, oy + y * scale,
                                   r * scale, scale, color=0, fill=True)
            x += r

    return img

def make_plain_rgb(w, h):
    img = image.Image(w, h, image.RGB888)
    img.draw_rectangle(0, 0, w, h, color=(40, 60, 80), fill=True)
    return img

def snapshot_b64(tag):
    lv.task_handler()
    lv.task_handler()
    dsc = lv.snapshot_take(lv.scr_act(), lv.COLOR_FORMAT.ARGB8888)
    w, h = dsc.header.w, dsc.header.h
    ptr = int.from_bytes(bytes(memoryview(dsc.data)), "little")
    import uctypes
    raw = uctypes.bytearray_at(ptr, w * h * 4)
    dsc.buf_free()
    out = bytearray()
    for y in range(h):
        base = y * w * 4
        x = 0
        while x < w:
            off = base + x * 4
            b, g, r = raw[off], raw[off + 1], raw[off + 2]
            nrun = 1
            while nrun < 255 and x + nrun < w:
                o2 = base + (x + nrun) * 4
                if raw[o2] != b or raw[o2 + 1] != g or raw[o2 + 2] != r:
                    break
                nrun += 1
            out.append(nrun - 1)
            out.append(b)
            out.append(g)
            out.append(r)
            x += nrun
    import ubinascii
    print("SHOT {} {} {} {} {}".format(tag, w, h, len(raw), len(out)))
    b64 = ubinascii.b2a_base64(bytes(out)).decode().strip()
    for i in range(0, len(b64), 800):
        print("B64 " + b64[i:i + 800])
    print("SHOTEND {}".format(tag))

def main():
    from apps.app_scan import ScanApp
    print("PROBE import ok")

    n, rows = load_matrix()
    print("PROBE qr matrix {} modules".format(n))

    content = lv.obj(lv.scr_act())
    content.set_size(800, 480)
    content.align(lv.ALIGN.TOP_LEFT, 0, 0)

    class Ctx:
        width = 800
        height = 480
        want_exit = False
        ok_pressed = False
        back_handler = None

        def request_exit(self):
            self.want_exit = True

        def set_back_handler(self, h):
            self.back_handler = h

        def take_ok(self):
            v = self.ok_pressed
            self.ok_pressed = False
            return v

    ctx = Ctx()
    app = ScanApp(content, ctx)
    try:

        qr_frame = make_qr_gray(n, rows, scale=12, margin=8)
        rgb_frame = make_plain_rgb(800, 480)
        real_snapshot = app.sensor.snapshot

        mode = {"qr": True}

        def fake_snapshot(chn=None):
            if chn == 1 and mode["qr"]:
                return qr_frame
            if chn == 2:
                return rgb_frame
            return real_snapshot(chn=chn)

        app.sensor.snapshot = fake_snapshot

        for _ in range(10):
            app.step()
            lv.task_handler()
            time.sleep_ms(30)
            if app.popup is not None:
                break

        assert app.popup is not None, "FAIL: popup not shown"
        assert app.frozen, "FAIL: not frozen after hit"
        print("PROBE popup OK, frozen=True")

        snapshot_b64("scan_popup")

        app._close_result()
        assert app.popup is None and not app.frozen, "FAIL: close failed"
        print("PROBE close OK, frozen=False")

        for _ in range(4):
            app.step()
            lv.task_handler()
            time.sleep_ms(30)
        assert app.popup is None, "FAIL: re-triggered inside ignore window"
        print("PROBE ignore-window OK")

        app.last_payload = None
        for _ in range(10):
            app.step()
            lv.task_handler()
            time.sleep_ms(30)
            if app.popup is not None:
                break
        assert app.popup is not None, "FAIL: second hit missing"
        print("PROBE second hit OK")

        snapshot_b64("scan_popup2")

        assert ctx.back_handler() is True, "FAIL: back handler did not consume"
        assert app.popup is None, "FAIL: back handler left popup"
        print("PROBE back_handler OK")

        print("PROBE ALL OK mem_free={}".format(gc.mem_free()))
    finally:
        try:
            app.destroy()
            print("PROBE destroy ok")
        except BaseException as e:
            print("PROBE destroy error:", e)

main()
