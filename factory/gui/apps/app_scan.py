# Scan app: full-screen viewfinder + live QR code detection.
#
# The display path matches Camera/Vision: chn2 (RGB888 packed, 800x480 = screen size) is
# composited into an LVGL img widget through as_lvgl_img_src.
# Detection path: chn1 delivers a separate GRAYSCALE frame to find_qrcodes() (grayscale is
# the format that decodes reliably).
# The dual-channel setup follows the same verified path as Camera's chn0/chn2 channels;
# only the sizes differ.
#
# After a QR code is found: freeze the picture (stop streaming into the display; the img
# widget keeps the last frame buffer, the same freeze technique as the Vision overlay) and
# show the payload in a non-full-screen white popup.
# Popup: translucent backdrop + centred opaque white panel (dark text); tapping the backdrop
# or the "SCAN AGAIN" button closes it and resumes scanning; the same content is ignored
# briefly after resuming, so it does not re-trigger immediately.
#
# Keys: CANCEL (GPIO0) closes the popup when open (back_handler), otherwise exits (launcher);
# OK (GPIO53) closes the popup when open. The Home floating button in the top-left exits on
# tap (immersive mode).
# Audio: every button goes through beep.on_click; a successful detection additionally plays
# beep.tap().

import gc
import sys
import time

import lvgl as lv
from media.sensor import Sensor, CAM_CHN_ID_1, CAM_CHN_ID_2

from ui import beep
from core.errors import (AppUnavailable, CAMERA_UNAVAILABLE, NoSignalCounter,
                       close_sensor)
from hardware.camera_fov import set_preview

# Detection channel size. It must keep the 5:3 output aspect of the preview
# channel, otherwise the two channels crop different windows and the strips that
# only the preview sees can never be decoded. Grayscale, because that is the
# format find_qrcodes decodes reliably.
DET_W = 800
DET_H = 480
DETECT_PERIOD_MS = 60         # detection throttle: ~16 fps of detection is plenty, the saved CPU goes to display
IGNORE_AGAIN_MS = 2000        # after closing the popup, the same content does not pop up again during this window

# Popup size: not full screen. 620x380 leaves a backdrop margin on all sides of the 800x480
# screen, so tapping the backdrop closes it.
PANEL_W = 620
PANEL_H = 380
PANEL_BG = 0xFFFFFF           # opaque white background
PANEL_LINE = 0xD5DBE2
COL_TITLE = 0x1F2937          # dark title/body text (on white)
COL_TEXT = 0x111827
COL_BTN = 0x2E7D32            # "SCAN AGAIN" dark green with white text, clear contrast on the white popup
COL_HINT = 0x6B7885           # viewfinder status text (grey)
PAYLOAD_MAX_CHARS = 900       # truncate overlong payloads so the label cannot blow up memory


def _font(*names):
    """Look up built-in fonts by candidate name and return the first available one (font
    names differ between bindings)."""
    for name in names:
        font = getattr(lv, name, None)
        if font:
            return font
    return None


class ScanApp:
    def __init__(self, content, ctx):
        self.ctx = ctx
        self.content = content
        self.sensor = None
        self.frozen = False           # True = popup open, stop streaming to the display
        self.popup = None             # result overlay (None = not open)
        self.last_payload = None      # last detected content (ignored briefly after resuming)
        self.ignore_until = 0
        self._gc_tick = 0
        # Runtime disconnect detection (same as Camera): consecutive frame failures while the
        # camera is unplugged -> report to the launcher
        self._no_signal = NoSignalCounter()
        self._det_last_ms = 0
        self.frame_count = 0
        self.last_fps_ms = time.ticks_ms()

        self.img_w = ctx.width
        self.img_h = ctx.height

        # ---- Viewfinder ----
        self.img_widget = lv.img(content)
        self.img_widget.set_size(self.img_w, self.img_h)
        self.img_widget.align(lv.ALIGN.CENTER, 0, 0)

        # ---- Top centre status: scanning + FPS ----
        self.stat_label = lv.label(content)
        self.stat_label.set_text("scan: point at a QR code")
        self.stat_label.set_style_text_color(lv.color_hex(COL_HINT), 0)
        self.stat_label.align(lv.ALIGN.TOP_MID, 0, 8)

        # ---- Top-left exit button (immersive, no status bar, same as Vision) ----
        home = lv.btn(content)
        home.set_size(56, 56)
        home.align(lv.ALIGN.TOP_LEFT, 8, 8)
        home.set_style_radius(28, 0)
        home.set_style_bg_color(lv.color_hex(0x000000), 0)
        home.set_style_bg_opa(lv.OPA._50, 0)
        home.set_style_border_width(0, 0)
        icon = lv.label(home)
        icon.set_text(lv.SYMBOL.LEFT)
        icon.center()
        beep.on_click(home, lambda e: ctx.request_exit())

        # CANCEL key: close the popup when open, otherwise exit as usual
        ctx.set_back_handler(self._on_back)

        # ---- Sensor: chn2 for display (packed RGB888) + chn1 for detection (GRAYSCALE) ----
        # Convert a missing camera into AppUnavailable: the launcher shows a notice panel and
        # lets CANCEL return to the desktop.
        sensor = None
        try:
            sensor = Sensor(fps=30)
            sensor.reset()
            set_preview(sensor, CAM_CHN_ID_2, self.img_w, self.img_h)
            sensor.set_pixformat(Sensor.RGB888, chn=CAM_CHN_ID_2)
            set_preview(sensor, CAM_CHN_ID_1, DET_W, DET_H)
            sensor.set_pixformat(Sensor.GRAYSCALE, chn=CAM_CHN_ID_1)
            sensor.run()
        except BaseException as error:
            print("[Scan] sensor init failed: {}".format(error))
            sys.print_exception(error)
            close_sensor(sensor)
            raise AppUnavailable(CAMERA_UNAVAILABLE)
        self.sensor = sensor

        print("[Scan] ready: preview {}x{} | detect {}x{} mem_free={}".format(
            self.img_w, self.img_h, DET_W, DET_H, gc.mem_free()))

    def _on_back(self):
        """CANCEL key: popup open -> close it and consume; otherwise return False so the
        default exit runs."""
        if self.popup is not None:
            self._close_result()
            return True
        return False

    # ---------- Result overlay ----------
    def _open_result(self, payload):
        """Freeze the picture and show the white result panel. Idempotent: no second popup
        while one is open."""
        if self.popup is not None:
            return
        self.frozen = True
        self.last_payload = payload
        print("[Scan] found QR ({} chars): {}".format(
            len(payload), payload[:120]))
        beep.tap()

        # Backdrop: full-screen translucent black, tapping empty space closes it
        backdrop = lv.obj(self.content)
        backdrop.set_size(self.img_w, self.img_h)
        backdrop.align(lv.ALIGN.TOP_LEFT, 0, 0)
        backdrop.set_style_bg_color(lv.color_hex(0x000000), 0)
        backdrop.set_style_bg_opa(lv.OPA._50, 0)
        backdrop.set_style_border_width(0, 0)
        backdrop.set_style_pad_all(0, 0)
        backdrop.clear_flag(lv.obj.FLAG.SCROLLABLE)
        backdrop.add_flag(lv.obj.FLAG.CLICKABLE)
        beep.on_click(backdrop, lambda e: self._close_result())

        # Panel: not full screen, opaque white. Created above the backdrop and swallows clicks
        # (so they do not close it)
        panel = lv.obj(backdrop)
        panel.set_size(PANEL_W, PANEL_H)
        panel.align(lv.ALIGN.CENTER, 0, 0)
        panel.set_style_bg_color(lv.color_hex(PANEL_BG), 0)
        panel.set_style_bg_opa(lv.OPA.COVER, 0)
        panel.set_style_border_width(2, 0)
        panel.set_style_border_color(lv.color_hex(PANEL_LINE), 0)
        panel.set_style_radius(16, 0)
        panel.set_style_pad_all(0, 0)
        panel.clear_flag(lv.obj.FLAG.SCROLLABLE)
        panel.add_flag(lv.obj.FLAG.CLICKABLE)

        title = lv.label(panel)
        title.set_text("QR Code")
        title.set_style_text_color(lv.color_hex(COL_TITLE), 0)
        title_font = _font("font_montserrat_24", "font_montserrat_20",
                           "font_montserrat_16")
        if title_font is not None:
            title.set_style_text_font(title_font, 0)
        title.align(lv.ALIGN.TOP_MID, 0, 14)

        # payload area: fixed-height vertical scroll container, so long text cannot break the
        # panel
        body_w = PANEL_W - 32
        body_h = PANEL_H - 56 - 76       # title on top, button at the bottom
        body = lv.obj(panel)
        body.set_size(body_w, body_h)
        body.align(lv.ALIGN.TOP_MID, 0, 52)
        body.set_style_bg_opa(lv.OPA.TRANSP, 0)
        body.set_style_border_width(0, 0)
        body.set_style_pad_all(0, 0)
        try:
            body.set_scroll_dir(lv.DIR.VER)
        except Exception:
            pass
        body.clear_flag(lv.obj.FLAG.SCROLLABLE)
        body.add_flag(lv.obj.FLAG.SCROLLABLE)

        text = payload
        if len(text) > PAYLOAD_MAX_CHARS:
            text = text[:PAYLOAD_MAX_CHARS] + "..."
        lab = lv.label(body)
        lab.set_text(text)
        lab.set_width(body_w)
        lab.set_long_mode(lv.label.LONG.WRAP)
        lab.set_style_text_color(lv.color_hex(COL_TEXT), 0)
        body_font = _font("font_montserrat_16", "font_montserrat_14")
        if body_font is not None:
            lab.set_style_text_font(body_font, 0)
        lab.align(lv.ALIGN.TOP_LEFT, 0, 0)

        # Bottom button: close the popup and keep scanning
        btn = lv.btn(panel)
        btn.set_size(240, 56)
        btn.align(lv.ALIGN.BOTTOM_MID, 0, -12)
        btn.set_style_radius(12, 0)
        btn.set_style_bg_color(lv.color_hex(COL_BTN), 0)
        btn.set_style_bg_opa(lv.OPA.COVER, 0)
        btn.set_style_border_width(0, 0)
        btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
        btn.add_flag(lv.obj.FLAG.CLICKABLE)
        btn_lab = lv.label(btn)
        btn_lab.set_text("SCAN AGAIN")
        btn_lab.set_style_text_color(lv.color_hex(0xFFFFFF), 0)
        if body_font is not None:
            btn_lab.set_style_text_font(body_font, 0)
        btn_lab.center()
        btn_lab.clear_flag(lv.obj.FLAG.CLICKABLE)   # clicks land on the button
        beep.on_click(btn, lambda e: self._close_result())

        self.popup = backdrop

    def _close_result(self):
        """Close the overlay and unfreeze the picture. The same content is ignored briefly so
        aiming at the same code does not pop up again and again."""
        if self.popup is not None:
            try:
                self.popup.delete()
            except BaseException as e:
                print("[Scan] popup delete error:", e)
            self.popup = None
        self.frozen = False
        if self.last_payload is not None:
            self.ignore_until = time.ticks_add(time.ticks_ms(), IGNORE_AGAIN_MS)
        gc.collect()

    # ---------- Detection ----------
    def _detect(self):
        """Run find_qrcodes on the chn1 grayscale frame. A hit outside the ignore window ->
        popup.

        Multi-scale (measured on board): a real-world image (WeChat contact card, 888x1140)
        returns 0 from find_qrcodes when the code's modules are ~13 px, while
        mean_pool(2,2) (~6.5 px/module) or scale 0.6 (~8 px) decodes it. The viewfinder has
        the same problem: when the code is held too close and fills too much of the screen,
        full-frame detection misses it. So when the full frame finds nothing, pool it 2x and
        search again (measured +22 ms, inside the 60 ms throttle window). The pooled frame is
        used for detection only and does not affect the display or the rect.
        """
        try:
            img = self.sensor.snapshot(chn=CAM_CHN_ID_1)
            codes = img.find_qrcodes()
            if not codes:
                codes = img.mean_pool(2, 2).find_qrcodes()
        except BaseException as error:
            print("[Scan] detect error:", error)
            sys.print_exception(error)
            return
        for code in codes:
            try:
                payload = code.payload()
            except BaseException as error:
                print("[Scan] payload error:", error)
                continue
            if not payload:
                continue
            if (payload == self.last_payload and
                    time.ticks_diff(time.ticks_ms(), self.ignore_until) < 0):
                continue
            self._open_result(payload)
            return

    def step(self):
        if self.sensor is None:
            return

        # OK key: close the popup when open (same OK semantics as Vision)
        if self.ctx.take_ok():
            if self.popup is not None:
                self._close_result()
            return

        if self.frozen:
            return

        # Display frame: chn2 packed RGB888 (as_lvgl_img_src only accepts packed)
        try:
            img = self.sensor.snapshot(chn=CAM_CHN_ID_2)
            img.as_lvgl_img_src(self.img_widget)
            self.img_widget.invalidate()
        except BaseException as error:
            self.stat_label.set_text("snapshot error: {}".format(error))
            self._no_signal.on_fail(self.ctx)
            return
        self._no_signal.on_ok()

        # Detection frame: throttled by DETECT_PERIOD_MS; detection itself is slow, so it does
        # not need to run every frame
        now = time.ticks_ms()
        if time.ticks_diff(now, self._det_last_ms) >= DETECT_PERIOD_MS:
            self._det_last_ms = now
            self._detect()

        # LVGL img src / sensor frame objects are cyclic garbage, reclaimed periodically (same
        # as Vision)
        self._gc_tick += 1
        if self._gc_tick >= 8:
            self._gc_tick = 0
            gc.collect()

        self.frame_count += 1
        elapsed = time.ticks_diff(now, self.last_fps_ms)
        if elapsed >= 1000:
            fps = self.frame_count * 1000.0 / elapsed
            self.stat_label.set_text("scan: point at a QR code | FPS {:.1f}".format(fps))
            self.frame_count = 0
            self.last_fps_ms = now

    def destroy(self):
        print("[Scan] destroy begin")
        self.popup = None
        self.frozen = False
        if self.sensor is not None:
            try:
                self.sensor.stop()
                print("[Scan]   sensor.stop ok")
            except BaseException as e:
                print("[Scan]   sensor.stop error:", e)
            try:
                self.sensor.deinit()
                print("[Scan]   sensor.deinit ok")
            except BaseException as e:
                print("[Scan]   sensor.deinit error:", e)
            self.sensor = None
        print("[Scan] destroy done")


def create_app(content, ctx):
    return ScanApp(content, ctx)
