# CV Lab: classic computer-vision bench on the live camera, no neural network.
#
# Display path matches the other immersive apps: chn2 packed RGB888 at 800x480 is
# composited through as_lvgl_img_src, and every result is drawn as pre-built LVGL
# objects on top, never into the frame buffer.
# Detection path: a separate grayscale channel at half resolution keeps Hough and
# quad detection inside the frame budget. Both channels share the same 5:3 crop
# window, so one scale factor maps results onto the preview exactly.
#
# Modes: dark/bright blobs, line segments, rectangles, frame-difference motion.
# Tap the top bar (or OK) to switch, CANCEL exits, and the bar doubles as the live
# status line.

import gc
import sys
import time

import lvgl as lv
from media.sensor import Sensor, CAM_CHN_ID_1, CAM_CHN_ID_2

from ui import beep
from apps.error import (AppUnavailable, CAMERA_UNAVAILABLE, NoSignalCounter,
                        close_sensor)
from camera_fov import set_preview

PREVIEW_W = 800                # display channel = screen size (source == widget)
PREVIEW_H = 480
DET_W = 400                    # detection channel: half size, same 5:3 crop window
DET_H = 240

# Detection throttle. Display keeps running every frame; the detectors are the
# expensive part (Hough and quad search), so they are only re-run this often.
# Measured on board at 400x240: blobs 11-12 ms, segments 20-45 ms, rects 41-353 ms
# (scene dependent), motion 14 ms.
DETECT_PERIOD_MS = 90
RECT_PERIOD_MS = 250
MOTION_PERIOD_MS = 120

# Overlay pools. Fixed at construction so a frame never allocates LVGL objects.
BOX_POOL = 12
LINE_POOL = 16
DOT_POOL = 24

# Detector parameters. Distances are in detection-channel pixels (half of the
# preview), so a threshold tuned here covers the same scene area as twice the
# value measured on a full-size frame.
BLOB_MIN_PIXELS = 150
BLOB_MARGIN = 10
SEGMENT_MERGE_DISTANCE = 10
SEGMENT_MAX_THETA_DIFF = 15
RECT_THRESHOLD = 10000
# Motion: binarise both frames, XOR them, and blobs on the result are changed
# regions. Measured: copy 0.2 ms + binary 2 ms + xor 0.5 ms + blobs 11 ms.
MOTION_BIN_LO = 0
MOTION_BIN_HI = 100
MOTION_MIN_PIXELS = 80
MOTION_MARGIN = 5

# Top bar (same geometry as the other immersive apps).
TOPBAR_H = 56
TOPBAR_Y = 10
TOPBAR_LEFT = 8
TOPBAR_RIGHT = 8
SEG_PAD = 18

COL_TITLE = 0x00E5A0           # teal: app name
COL_FPS = 0xFFC857             # amber: frame rate
COL_COUNT = 0x64B5F6           # sky blue: hit count when non-zero
COL_COUNT_IDLE = 0x6B7885      # grey: demoted count so "0" is not eye-catching
COL_SEP = 0x3A4650
COL_HINT = 0x7A8794
COL_FROZEN = 0xFF8A65
COL_ERROR = 0xFF6B6B

# Ink colours, one per mode, so the mode is recognisable before reading the bar.
COL_DARK = 0x4CC9F0
COL_BRIGHT = 0xFFC857
COL_LINES = 0x5CE8C0
COL_RECTS = 0xC990A8
COL_MOTION = 0xFF8A65

DOT_SIZE = 8
BOX_WIDTH = 3
LINE_WIDTH = 3

MODE_BLOBS_DARK = 0
MODE_BLOBS_BRIGHT = 1
MODE_LINES = 2
MODE_RECTS = 3
MODE_MOTION = 4

MODES = [
    {"id": "dark", "name": "Blobs Dark", "note": "L 0-80", "color": COL_DARK},
    {"id": "bright", "name": "Blobs Bright", "note": "L 175-255",
     "color": COL_BRIGHT},
    {"id": "segments", "name": "Segments", "note": "Hough", "color": COL_LINES},
    {"id": "rects", "name": "Rects", "note": "quad", "color": COL_RECTS},
    {"id": "motion", "name": "Motion", "note": "frame diff",
     "color": COL_MOTION},
]
DEFAULT_MODE = MODE_BLOBS_DARK

# Serial summary throttle, so a busy scene does not flood the log.
PRINT_MS = 1000


def _font(*names):
    """Return the first built-in font among the candidates that exists."""
    for name in names:
        font = getattr(lv, name, None)
        if font:
            return font
    return None


def _rect_of(obj):
    """(x, y, w, h) from a blob/rect result, tolerating index-style results."""
    try:
        return obj.rect()
    except BaseException:
        return (obj[0], obj[1], obj[2], obj[3])


def _center_of(obj, x, y, w, h):
    """Centroid of a blob result, falling back to the box centre."""
    try:
        return int(obj.cx()), int(obj.cy())
    except BaseException:
        return int(x + w // 2), int(y + h // 2)


def _line_of(obj):
    """(x1, y1, x2, y2) from a line result."""
    try:
        return obj.line()
    except BaseException:
        return (obj[0], obj[1], obj[2], obj[3])


class CVLabApp:
    def __init__(self, content, ctx):
        self.ctx = ctx
        self.content = content
        self.sensor = None
        self.frozen = False
        self.mode = DEFAULT_MODE
        self.picker = None
        self.frame_count = 0
        self.last_fps_ms = time.ticks_ms()
        self.fps_text = "-"
        self.last_hits = 0
        self._gc_tick = 0
        self._detect_ms = 0
        self._print_ms = 0
        self._detect_error_shown = False
        self.prev = None           # previous binarised frame, motion mode only
        self._no_signal = NoSignalCounter()
        self.det_w = DET_W
        self.det_h = DET_H

        self.img_w = ctx.width
        self.img_h = ctx.height

        # ---- preview ----
        self.img_widget = lv.img(content)
        self.img_widget.set_size(self.img_w, self.img_h)
        self.img_widget.align(lv.ALIGN.CENTER, 0, 0)

        # ---- overlay pools ----
        # Colour is assigned when the mode changes, never per frame.
        self.boxes = []
        for _ in range(BOX_POOL):
            box = lv.obj(content)
            box.set_style_bg_opa(lv.OPA.TRANSP, 0)
            box.set_style_border_width(BOX_WIDTH, 0)
            box.set_style_radius(4, 0)
            box.set_style_pad_all(0, 0)
            box.clear_flag(lv.obj.FLAG.SCROLLABLE)
            box.clear_flag(lv.obj.FLAG.CLICKABLE)
            box.add_flag(lv.obj.FLAG.HIDDEN)
            self.boxes.append(box)

        self.lines = []
        for _ in range(LINE_POOL):
            ln = lv.line(content)
            ln.set_points([{"x": 0, "y": 0}] * 2, 2)
            ln.set_style_line_width(LINE_WIDTH, 0)
            ln.set_style_line_opa(lv.OPA.COVER, 0)
            try:
                ln.set_style_line_rounded(True, 0)
            except BaseException:
                pass
            ln.clear_flag(lv.obj.FLAG.CLICKABLE)
            ln.add_flag(lv.obj.FLAG.HIDDEN)
            self.lines.append(ln)

        self.dots = []
        for _ in range(DOT_POOL):
            dot = lv.obj(content)
            dot.set_size(DOT_SIZE, DOT_SIZE)
            dot.set_style_radius(DOT_SIZE // 2, 0)
            dot.set_style_bg_opa(lv.OPA.COVER, 0)
            dot.set_style_border_width(0, 0)
            dot.set_style_pad_all(0, 0)
            dot.clear_flag(lv.obj.FLAG.SCROLLABLE)
            dot.clear_flag(lv.obj.FLAG.CLICKABLE)
            dot.add_flag(lv.obj.FLAG.HIDDEN)
            self.dots.append(dot)

        # ---- top bar: one clickable strip, colour-coded segments ----
        bar_w = self.img_w - TOPBAR_LEFT - TOPBAR_RIGHT
        self.stat_btn = lv.btn(content)
        self.stat_btn.set_size(bar_w, TOPBAR_H)
        self.stat_btn.align(lv.ALIGN.TOP_LEFT, TOPBAR_LEFT, TOPBAR_Y)
        self.stat_btn.set_style_bg_color(lv.color_hex(0x000000), 0)
        self.stat_btn.set_style_bg_opa(lv.OPA._50, 0)
        self.stat_btn.set_style_radius(TOPBAR_H // 2, 0)
        self.stat_btn.set_style_border_width(1, 0)
        self.stat_btn.set_style_border_color(lv.color_hex(COL_SEP), 0)
        self.stat_btn.set_style_pad_all(0, 0)
        self.stat_btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
        self.stat_btn.add_flag(lv.obj.FLAG.CLICKABLE)
        beep.on_click(self.stat_btn, lambda e: self._open_picker())
        self.stat_btn.set_flex_flow(lv.FLEX_FLOW.ROW)
        self.stat_btn.set_flex_align(lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER,
                                     lv.FLEX_ALIGN.CENTER)
        try:
            self.stat_btn.set_style_pad_column(SEG_PAD, 0)
        except BaseException:
            pass

        bar_font = _font("font_montserrat_20", "font_montserrat_16",
                         "font_montserrat_14")

        def _seg(text, color):
            lab = lv.label(self.stat_btn)
            lab.set_text(text)
            lab.set_width(lv.SIZE_CONTENT)
            lab.set_style_text_color(lv.color_hex(color), 0)
            if bar_font is not None:
                lab.set_style_text_font(bar_font, 0)
            # Let clicks reach the strip container, not the small text block.
            lab.clear_flag(lv.obj.FLAG.CLICKABLE)
            return lab

        self.stat_title = _seg("CV Lab", COL_TITLE)
        self.stat_sep1 = _seg("|", COL_SEP)
        self.stat_fps = _seg("FPS -", COL_FPS)
        self.stat_sep2 = _seg("|", COL_SEP)
        self.stat_mode = _seg(MODES[self.mode]["name"], MODES[self.mode]["color"])
        self.stat_sep3 = _seg("|", COL_SEP)
        self.stat_count = _seg("0", COL_COUNT_IDLE)
        self.stat_hint = _seg("TAP to switch", COL_HINT)

        ctx.set_back_handler(self._on_back)

        # ---- sensor ----
        sensor = None
        try:
            sensor = Sensor(fps=30)
            sensor.reset()
            # chn2 = preview, packed RGB888 (as_lvgl_img_src only takes packed).
            set_preview(sensor, CAM_CHN_ID_2, self.img_w, self.img_h)
            sensor.set_pixformat(Sensor.RGB888, chn=CAM_CHN_ID_2)
            # chn1 = detection, grayscale: the format every classic detector wants.
            try:
                set_preview(sensor, CAM_CHN_ID_1, DET_W, DET_H)
                sensor.set_pixformat(Sensor.GRAYSCALE, chn=CAM_CHN_ID_1)
                self.det_w = DET_W
                self.det_h = DET_H
            except BaseException as error:
                # Not every ISP build accepts an arbitrary downscale; full-size
                # detection is slower but keeps the app usable.
                print("[CVLab] {}x{} detect rejected ({}), using {}x{}".format(
                    DET_W, DET_H, error, PREVIEW_W, PREVIEW_H))
                set_preview(sensor, CAM_CHN_ID_1, PREVIEW_W, PREVIEW_H)
                sensor.set_pixformat(Sensor.GRAYSCALE, chn=CAM_CHN_ID_1)
                self.det_w = PREVIEW_W
                self.det_h = PREVIEW_H
            sensor.run()
        except BaseException as error:
            print("[CVLab] sensor init failed: {}".format(error))
            sys.print_exception(error)
            close_sensor(sensor)
            raise AppUnavailable(CAMERA_UNAVAILABLE)
        self.sensor = sensor

        self._apply_mode_color()
        print("[CVLab] ready: preview {}x{} | detect {}x{} | mem_free={}".format(
            self.img_w, self.img_h, self.det_w, self.det_h, gc.mem_free()))

    # ---------- mode / overlay plumbing ----------
    def _apply_mode_color(self):
        """Assign the current mode's ink colour to every pool slot once."""
        color = lv.color_hex(MODES[self.mode]["color"])
        for obj in self.boxes:
            obj.set_style_border_color(color, 0)
        for obj in self.lines:
            obj.set_style_line_color(color, 0)
        for obj in self.dots:
            obj.set_style_bg_color(color, 0)

    def _hide_overlays(self):
        for pool in (self.boxes, self.lines, self.dots):
            for obj in pool:
                obj.add_flag(lv.obj.FLAG.HIDDEN)

    def _switch_mode(self, idx):
        if idx == self.mode:
            return
        self.prev = None            # drop the motion reference of the old mode
        self.mode = idx
        self._hide_overlays()
        self._apply_mode_color()
        self.last_hits = 0
        self._update_stat()
        print("[CVLab] mode -> {}".format(MODES[idx]["id"]))

    def _on_back(self):
        """CANCEL key: the picker is one level; otherwise let the default exit run."""
        if self.picker is not None:
            self._close_picker()
            return True
        return False

    # ---------- detectors ----------
    def _blobs(self, img, lo, hi):
        out = []
        blobs = img.find_blobs([(lo, hi)], pixels_threshold=BLOB_MIN_PIXELS,
                               area_threshold=BLOB_MIN_PIXELS, merge=True,
                               margin=BLOB_MARGIN)
        for blob in blobs:
            try:
                x, y, w, h = _rect_of(blob)
                if w <= 0 or h <= 0:
                    continue
                cx, cy = _center_of(blob, x, y, w, h)
            except BaseException:
                continue
            out.append((int(x), int(y), int(w), int(h), cx, cy))
            if len(out) >= BOX_POOL:
                break
        return out

    def _segments(self, img):
        """Finite segments: find_lines returns infinite lines clipped to the frame
        and is far noisier at this size (measured 16 hits against 2 segments)."""
        out = []
        found = img.find_line_segments(
            merge_distance=SEGMENT_MERGE_DISTANCE,
            max_theta_diff=SEGMENT_MAX_THETA_DIFF)
        for obj in found:
            try:
                x1, y1, x2, y2 = _line_of(obj)
            except BaseException:
                continue
            out.append((int(x1), int(y1), int(x2), int(y2)))
            if len(out) >= LINE_POOL:
                break
        return out

    def _rects(self, img):
        out = []
        found = img.find_rects(threshold=RECT_THRESHOLD)
        for obj in found:
            try:
                x, y, w, h = _rect_of(obj)
            except BaseException:
                continue
            corners = None
            try:
                corners = obj.corners()
            except BaseException:
                pass
            out.append((int(x), int(y), int(w), int(h), corners))
            if len(out) >= BOX_POOL:
                break
        return out

    def _motion(self, img):
        """Changed regions between this detection frame and the previous one.

        Keeps the binarised frame; a stored reference older than one detection
        interval is what makes slow motion visible instead of averaging out.
        """
        cur = img.copy()
        cur.binary([(MOTION_BIN_LO, MOTION_BIN_HI)])
        prev = self.prev
        self.prev = cur
        if prev is None:
            return []
        diff = prev.b_xor(cur)
        out = []
        blobs = diff.find_blobs([(175, 255)],
                                pixels_threshold=MOTION_MIN_PIXELS,
                                area_threshold=MOTION_MIN_PIXELS,
                                merge=True, margin=MOTION_MARGIN)
        for blob in blobs:
            try:
                x, y, w, h = _rect_of(blob)
                if w <= 0 or h <= 0:
                    continue
                cx, cy = _center_of(blob, x, y, w, h)
            except BaseException:
                continue
            out.append((int(x), int(y), int(w), int(h), cx, cy))
            if len(out) >= BOX_POOL:
                break
        return out

    def _detect(self):
        """Run the current mode's detector on the grayscale channel."""
        try:
            img = self.sensor.snapshot(chn=CAM_CHN_ID_1)
        except BaseException as error:
            self._show_error("detect snapshot")
            print("[CVLab] detect snapshot error:", error)
            return

        mode = self.mode
        try:
            if mode == MODE_BLOBS_DARK:
                hits = self._blobs(img, 0, 80)
            elif mode == MODE_BLOBS_BRIGHT:
                hits = self._blobs(img, 175, 255)
            elif mode == MODE_LINES:
                hits = self._segments(img)
            elif mode == MODE_RECTS:
                hits = self._rects(img)
            else:
                hits = self._motion(img)
            self._detect_error_shown = False
        except BaseException as error:
            if not self._detect_error_shown:
                # Report once per failure burst: an unsupported detector raises
                # on every frame and would otherwise flood the serial log.
                self._detect_error_shown = True
                print("[CVLab] {} failed: {}".format(MODES[mode]["id"], error))
                sys.print_exception(error)
                self._show_error("detector error")
            hits = []

        self._draw(hits)
        self.last_hits = len(hits)
        self._print_state(hits)

    # ---------- drawing ----------
    def _sx(self, value):
        return int(value * self.img_w / self.det_w)

    def _sy(self, value):
        return int(value * self.img_h / self.det_h)

    def _draw(self, hits):
        for pool in (self.boxes, self.lines, self.dots):
            for obj in pool:
                obj.add_flag(lv.obj.FLAG.HIDDEN)

        mode = self.mode
        if mode in (MODE_BLOBS_DARK, MODE_BLOBS_BRIGHT, MODE_MOTION):
            self._draw_blobs(hits)
        elif mode == MODE_LINES:
            self._draw_segments(hits)
        else:
            self._draw_rects(hits)

    def _dot(self, slot, x, y):
        if slot >= len(self.dots):
            return slot
        dot = self.dots[slot]
        dot.set_pos(x - DOT_SIZE // 2, y - DOT_SIZE // 2)
        dot.clear_flag(lv.obj.FLAG.HIDDEN)
        return slot + 1

    def _draw_blobs(self, hits):
        dot_slot = 0
        for i, (x, y, w, h, cx, cy) in enumerate(hits):
            if i >= len(self.boxes):
                break
            box = self.boxes[i]
            box.set_pos(self._sx(x), self._sy(y))
            box.set_size(self._sx(w), self._sy(h))
            box.clear_flag(lv.obj.FLAG.HIDDEN)
            dot_slot = self._dot(dot_slot, self._sx(cx), self._sy(cy))

    def _draw_segments(self, hits):
        for i, (x1, y1, x2, y2) in enumerate(hits):
            if i >= len(self.lines):
                break
            ln = self.lines[i]
            ln.set_points([{"x": self._sx(x1), "y": self._sy(y1)},
                           {"x": self._sx(x2), "y": self._sy(y2)}], 2)
            ln.clear_flag(lv.obj.FLAG.HIDDEN)

    def _draw_rects(self, hits):
        dot_slot = 0
        for i, (x, y, w, h, corners) in enumerate(hits):
            if i >= len(self.boxes):
                break
            box = self.boxes[i]
            box.set_pos(self._sx(x), self._sy(y))
            box.set_size(self._sx(w), self._sy(h))
            box.clear_flag(lv.obj.FLAG.HIDDEN)
            if corners:
                for point in corners:
                    try:
                        px, py = point[0], point[1]
                    except BaseException:
                        continue
                    dot_slot = self._dot(dot_slot, self._sx(px), self._sy(py))

    # ---------- status ----------
    def _update_stat(self):
        mode = MODES[self.mode]
        self.stat_mode.set_text(mode["name"])
        self.stat_mode.set_style_text_color(lv.color_hex(mode["color"]), 0)
        self.stat_fps.set_text("FPS {}".format(self.fps_text))
        self.stat_count.set_text(str(self.last_hits))
        self.stat_count.set_style_text_color(
            lv.color_hex(COL_COUNT if self.last_hits > 0 else COL_COUNT_IDLE), 0)
        if self.frozen:
            self.stat_hint.set_text("FROZEN")
            self.stat_hint.set_style_text_color(lv.color_hex(COL_FROZEN), 0)
        else:
            self.stat_hint.set_text("TAP to switch")
            self.stat_hint.set_style_text_color(lv.color_hex(COL_HINT), 0)

    def _show_error(self, text):
        self.stat_hint.set_text(text)
        self.stat_hint.set_style_text_color(lv.color_hex(COL_ERROR), 0)

    def _period(self):
        """Detection interval for the current mode, from its measured cost."""
        if self.mode == MODE_RECTS:
            return RECT_PERIOD_MS
        if self.mode == MODE_MOTION:
            return MOTION_PERIOD_MS
        return DETECT_PERIOD_MS

    def _print_state(self, hits):
        now = time.ticks_ms()
        if time.ticks_diff(now, self._print_ms) < PRINT_MS:
            return
        self._print_ms = now
        summary = ""
        if hits:
            first = hits[0]
            summary = " first={}".format(tuple(first[:4]))
        print("[CVLab] mode={} hits={}{}".format(
            MODES[self.mode]["id"], len(hits), summary))

    # ---------- mode picker ----------
    def _close_picker(self):
        if self.picker is not None:
            try:
                self.picker.delete()
            except BaseException as error:
                print("[CVLab] picker delete error:", error)
            self.picker = None
            print("[CVLab] picker closed")
        self.frozen = False
        self._update_stat()

    def _open_picker(self):
        """Backdrop plus a centred panel; tapping outside closes it."""
        if self.picker is not None:
            return
        print("[CVLab] picker open")
        self.frozen = True
        self._update_stat()

        backdrop = lv.obj(self.content)
        backdrop.set_size(self.img_w, self.img_h)
        backdrop.align(lv.ALIGN.TOP_LEFT, 0, 0)
        backdrop.set_style_bg_color(lv.color_hex(0x000000), 0)
        backdrop.set_style_bg_opa(lv.OPA._50, 0)
        backdrop.set_style_border_width(0, 0)
        backdrop.set_style_pad_all(0, 0)
        backdrop.clear_flag(lv.obj.FLAG.SCROLLABLE)
        backdrop.add_flag(lv.obj.FLAG.CLICKABLE)
        beep.on_click(backdrop, lambda e: self._close_picker())

        panel = lv.obj(backdrop)
        panel.set_size(520, 76 + len(MODES) * 58)
        panel.align(lv.ALIGN.CENTER, 0, 0)
        panel.set_style_bg_color(lv.color_hex(0x101418), 0)
        panel.set_style_bg_opa(lv.OPA.COVER, 0)
        panel.set_style_border_width(2, 0)
        panel.set_style_border_color(lv.color_hex(COL_SEP), 0)
        panel.set_style_radius(14, 0)
        panel.set_style_pad_all(12, 0)
        panel.clear_flag(lv.obj.FLAG.SCROLLABLE)
        panel.add_flag(lv.obj.FLAG.CLICKABLE)   # swallow clicks, do not close

        title = lv.label(panel)
        title.set_text("CV Lab mode")
        title.set_style_text_color(lv.color_hex(0xE6EAF0), 0)
        title.align(lv.ALIGN.TOP_MID, 0, 2)

        hint = lv.label(panel)
        hint.set_text("tap a row to switch, tap outside to close")
        hint.set_style_text_color(lv.color_hex(0x8A97A3), 0)
        hint.align(lv.ALIGN.TOP_MID, 0, 24)

        font = _font("font_montserrat_16", "font_montserrat_14")
        y = 48
        for idx, mode in enumerate(MODES):
            row = lv.btn(panel)
            row.set_size(484, 50)
            row.align(lv.ALIGN.TOP_MID, 0, y)
            row.set_style_radius(10, 0)
            row.set_style_border_width(2 if idx == self.mode else 0, 0)
            row.set_style_border_color(lv.color_hex(mode["color"]), 0)
            row.set_style_bg_color(lv.color_hex(0x1E262E), 0)
            row.set_style_bg_opa(lv.OPA.COVER, 0)
            row.set_style_pad_all(0, 0)
            row.clear_flag(lv.obj.FLAG.SCROLLABLE)
            row.add_flag(lv.obj.FLAG.CLICKABLE)
            lab = lv.label(row)
            mark = "> " if idx == self.mode else "  "
            lab.set_text("{}{}   ({})".format(mark, mode["name"], mode["note"]))
            lab.set_style_text_color(
                lv.color_hex(mode["color"] if idx == self.mode else 0xE6EAF0), 0)
            if font is not None:
                lab.set_style_text_font(font, 0)
            lab.center()
            lab.clear_flag(lv.obj.FLAG.CLICKABLE)   # clicks land on the row
            beep.on_click(row, lambda e, i=idx: self._pick(i))
            y += 58

        self.picker = backdrop

    def _pick(self, idx):
        self._close_picker()
        if idx != self.mode:
            self._switch_mode(idx)

    # ---------- main loop ----------
    def step(self):
        if self.sensor is None:
            return

        if self.ctx.take_ok():
            if self.picker is not None:
                self._close_picker()
            else:
                self._open_picker()

        if self.frozen:
            return

        # Display frame first, so a slow detector never stalls the picture.
        try:
            img = self.sensor.snapshot(chn=CAM_CHN_ID_2)
            img.as_lvgl_img_src(self.img_widget)
            self.img_widget.invalidate()
        except BaseException as error:
            self._show_error("display error")
            print("[CVLab] display error:", error)
            self._no_signal.on_fail(self.ctx)
            return
        self._no_signal.on_ok()

        now = time.ticks_ms()
        if time.ticks_diff(now, self._detect_ms) >= self._period():
            self._detect_ms = now
            self._detect()
            self._update_stat()

        # LVGL img sources and sensor frames are cyclic garbage; collect
        # periodically or the heap fills with unreclaimed buffers.
        self._gc_tick += 1
        if self._gc_tick >= 8:
            self._gc_tick = 0
            gc.collect()

        self.frame_count += 1
        elapsed = time.ticks_diff(now, self.last_fps_ms)
        if elapsed >= 1000:
            self.fps_text = "{:.1f}".format(self.frame_count * 1000.0 / elapsed)
            self.frame_count = 0
            self.last_fps_ms = now
            self._update_stat()

    def destroy(self):
        print("[CVLab] destroy begin")
        self.picker = None
        self.prev = None
        if self.sensor is not None:
            try:
                self.sensor.stop()
            except BaseException as error:
                print("[CVLab]   sensor.stop error:", error)
            try:
                self.sensor.deinit()
            except BaseException as error:
                print("[CVLab]   sensor.deinit error:", error)
            self.sensor = None
        gc.collect()
        print("[CVLab] destroy done | mem_free={}".format(gc.mem_free()))


def create_app(content, ctx):
    return CVLabApp(content, ctx)
