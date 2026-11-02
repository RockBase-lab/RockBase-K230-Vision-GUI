# CV Lab probe: verifies every detector on synthetic frames, then checks that the
# LVGL overlays land on the same geometry after the detection->preview scaling.
# Run with --lvgl (it creates widgets and snapshots the screen).

import gc
import sys
import time

import image
import lvgl as lv

sys.path.insert(0, "/sdcard/factory/gui")
for _name in list(sys.modules):
    if _name == "apps.app_cvlab" or _name.startswith("apps.app_cvlab."):
        del sys.modules[_name]

from apps.app_cvlab import (CVLabApp, MODES, DET_W, DET_H, PREVIEW_W, PREVIEW_H,
                            MODE_BLOBS_DARK, MODE_BLOBS_BRIGHT, MODE_LINES,
                            MODE_RECTS, MODE_MOTION)

WHITE = 0xFFFFFF
BLACK = 0
FAILS = []


def check(name, ok, detail=""):
    print("PROBE {} {} {}".format("OK  " if ok else "FAIL", name, detail))
    if not ok:
        FAILS.append(name)


def gray_bg(value=WHITE):
    """A filled grayscale frame. Fill colours are RGB and converted to luma, so
    white must be 0xFFFFFF; a scalar 255 is luma(blue)=29."""
    img = image.Image(DET_W, DET_H, image.GRAYSCALE)
    img.draw_rectangle(0, 0, DET_W, DET_H, color=value, fill=True)
    return img


def rgb_bg(value=(40, 60, 80)):
    img = image.Image(PREVIEW_W, PREVIEW_H, image.RGB888)
    img.draw_rectangle(0, 0, PREVIEW_W, PREVIEW_H, color=value, fill=True)
    return img


def make_frames():
    frames = {}

    dark = gray_bg(WHITE)
    dark.draw_rectangle(30, 30, 120, 80, color=BLACK, fill=True)
    frames[MODE_BLOBS_DARK] = dark

    bright = gray_bg(BLACK)
    bright.draw_rectangle(200, 100, 100, 60, color=WHITE, fill=True)
    frames[MODE_BLOBS_BRIGHT] = bright

    segments = gray_bg(WHITE)
    segments.draw_line(20, 120, 380, 120, color=BLACK, thickness=4)
    frames[MODE_LINES] = segments

    rects = gray_bg(WHITE)
    rects.draw_rectangle(80, 40, 240, 160, color=BLACK, fill=True)
    frames[MODE_RECTS] = rects

    return frames


def motion_frames():
    a = gray_bg(WHITE)
    a.draw_rectangle(20, 20, 100, 60, color=BLACK, fill=True)
    b = gray_bg(WHITE)
    b.draw_rectangle(250, 140, 100, 60, color=BLACK, fill=True)
    return a, b


class FakeSensor:
    def __init__(self, det, disp):
        self.det = det
        self.disp = disp

    def snapshot(self, chn=None):
        if chn == 1:
            return self.det
        return self.disp


class Ctx:
    width = PREVIEW_W
    height = PREVIEW_H
    want_exit = False
    ok_pressed = False
    back_handler = None

    def request_exit(self):
        self.want_exit = True

    def report_unavailable(self, message):
        print("PROBE unavailable:", message)

    def set_back_handler(self, handler):
        self.back_handler = handler

    def take_ok(self):
        value = self.ok_pressed
        self.ok_pressed = False
        return value


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
    content = lv.obj(lv.scr_act())
    content.set_size(PREVIEW_W, PREVIEW_H)
    content.align(lv.ALIGN.TOP_LEFT, 0, 0)
    # The launcher's content container zeroes padding and border; without this the
    # default 22 px padding offsets every overlay in the snapshot only.
    content.set_style_pad_all(0, 0)
    content.set_style_border_width(0, 0)

    ctx = Ctx()
    app = CVLabApp(content, ctx)
    real_sensor = app.sensor
    print("PROBE app ok det={}x{} sx={} sy={}".format(
        app.det_w, app.det_h,
        float(app.img_w) / app.det_w, float(app.img_h) / app.det_h))

    try:
        frames = make_frames()
        for mode in (MODE_BLOBS_DARK, MODE_BLOBS_BRIGHT, MODE_LINES,
                     MODE_RECTS):
            app._switch_mode(mode)
            app.sensor = FakeSensor(frames[mode], rgb_bg())
            t0 = time.ticks_us()
            app._detect()
            cost = time.ticks_diff(time.ticks_us(), t0)
            print("PROBE mode={} hits={} detect_us={}".format(
                MODES[mode]["id"], app.last_hits, cost))
            check("detect/" + MODES[mode]["id"], app.last_hits > 0,
                  "hits={}".format(app.last_hits))

        # Motion compares against the previous binarised detection frame.
        app._switch_mode(MODE_MOTION)
        first, second = motion_frames()
        app.sensor = FakeSensor(first, rgb_bg())
        app._detect()
        check("detect/motion prime", app.last_hits == 0,
              "hits={}".format(app.last_hits))
        app.sensor = FakeSensor(second, rgb_bg())
        t0 = time.ticks_us()
        app._detect()
        cost = time.ticks_diff(time.ticks_us(), t0)
        print("PROBE mode=motion hits={} detect_us={}".format(
            app.last_hits, cost))
        check("detect/motion", app.last_hits > 0,
              "hits={}".format(app.last_hits))

        # Overlay geometry: the dark blob is at (30,30,120,80) in detection space,
        # so the preview box must be (60,60,240,160).
        app._switch_mode(MODE_BLOBS_DARK)
        disp = rgb_bg()
        disp.draw_rectangle(60, 60, 240, 160, color=(0, 0, 0), fill=True)
        app.sensor = FakeSensor(frames[MODE_BLOBS_DARK], disp)
        app.step()
        lv.task_handler()
        lv.task_handler()
        box = app.boxes[0]
        geom = (box.get_x(), box.get_y(), box.get_width(), box.get_height())
        check("overlay geometry", geom == (60, 60, 240, 160), "box={}".format(geom))
        check("overlay visible", not box.has_flag(lv.obj.FLAG.HIDDEN))
        check("count segment", app.stat_count.get_text() == "1",
              "text={}".format(app.stat_count.get_text()))

        # Rect overlay geometry is checked on the quad's bounding box.
        app._switch_mode(MODE_RECTS)
        app.sensor = FakeSensor(frames[MODE_RECTS], rgb_bg())
        app._detect()
        lv.task_handler()
        rbox = app.boxes[0]
        rgeom = (rbox.get_x(), rbox.get_y(), rbox.get_width(),
                 rbox.get_height())
        check("rect geometry", rgeom == (160, 80, 480, 320),
              "box={}".format(rgeom))

        # Bar and picker paths used by touch and the OK key.
        app._switch_mode(MODE_BLOBS_DARK)
        app._open_picker()
        check("picker opens", app.picker is not None and app.frozen)
        app._pick(MODE_LINES)
        check("picker switch", app.mode == MODE_LINES and app.picker is None)
        app._switch_mode(MODE_BLOBS_DARK)

        app.sensor = FakeSensor(frames[MODE_BLOBS_DARK], disp)
        app._detect()
        app._update_stat()
        lv.task_handler()
        snapshot_b64("cvlab_blobs")

        app.sensor = real_sensor
        for _ in range(3):
            app.step()
            lv.task_handler()
            time.sleep_ms(20)
        print("PROBE live step ok hits={} mem_free={}".format(
            app.last_hits, gc.mem_free()))
    finally:
        app.sensor = real_sensor
        try:
            app.destroy()
            print("PROBE destroy ok")
        except BaseException as error:
            print("PROBE destroy error:", error)

    if FAILS:
        print("PROBE RESULT FAIL {}".format(FAILS))
    else:
        print("PROBE RESULT ALL OK")


main()
