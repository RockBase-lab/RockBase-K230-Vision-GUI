# OCR app: snapshot text detection + recognition (Chinese and English).
#
# Chain: ocr_det_int16 (640x640) -> aicube.ocr_post_process -> per-box crops ->
# ocr_rec_int16 (512x32) -> CTC decode against examples/utils/dict.txt (6549
# entries, ASCII + common Chinese, so one model covers both languages).
#
# Snapshot UX, not live: det kpu measures 221-294 ms and rec 66-68 ms per box
# (board), so a full pass costs ~0.3 s + boxes x 0.07 s. OK/tap freezes the
# preview and runs one pass; OK again returns to live preview. The result replaces
# the frame with a full-screen translucent mask holding one line per region, in
# reading order.
#
# Open stays cheap on purpose (models only, ~2 s): the 4.9 s CJK font load runs
# on the first scan instead, so the preview is already smooth when the app
# appears. Only the first scan pays it; the font is cached at module level.
#
# chn1 is 800x480 like the preview: ocr_post_process reports coordinates in the
# ai2d input space, so with input == widget size the boxes map to the screen
# with no scaling (the letterbox pad is inside ai2d and already undone).
#
# The rec crop comes back from post_process as (1,h,w,3) HWC; its ai2d must be
# rebuilt per box (input size changes each time, measured ~1 ms). Output is
# (128,1,6549): argmax over axis 2, then CTC dedup against the dict.
#
# Keys: CANCEL(GPIO0) exits (launcher default); OK(GPIO53) scans/resumes.

import gc
import os
import sys
import time

import lvgl as lv
import ulab.numpy as np
import nncase_runtime as nn
import aicube
from libs.AI2D import Ai2d
from libs.Utils import letterbox_pad_param
from media.sensor import Sensor, CAM_CHN_ID_1, CAM_CHN_ID_2
from ui import beep
from core.errors import (AppUnavailable, CAMERA_UNAVAILABLE, NoSignalCounter,
                        close_sensor)
from hardware.camera_fov import set_preview
from core.model_pool import get as model_get
from core import font_pool
from core import ocr_dict

PREVIEW_W = 800               # capture size shared by preview and AI (= screen)
PREVIEW_H = 480
DET_MODEL = "/sdcard/examples/kmodel/ocr_det_int16.kmodel"
REC_MODEL = "/sdcard/examples/kmodel/ocr_rec_int16.kmodel"
DICT_PATH = "/sdcard/examples/utils/dict.txt"
DET_IN = [640, 640]
REC_IN = [512, 32]

# Thresholds from the official example; on a cluttered desk scene the board run
# returned 20-24 raw boxes, most of them noise, so the size floor and the cap
# below do the real filtering.
MASK_THR = 0.25
BOX_THR = 0.3
MAX_BOXES = 16                # recognitions per pass (rec costs ~70 ms each)
MIN_BOX_H = 10                # noise boxes measured 6-14 px tall on a desk scene
MIN_BOX_W = 16

TOPBAR_H = 56
TOPBAR_Y = 10
TOPBAR_LEFT = 8
TOPBAR_RIGHT = 8
SEG_PAD = 18

COL_TITLE = 0x00E5A0          # teal: app name
COL_COUNT = 0x64B5F6          # sky blue: recognised line count when non-zero
COL_COUNT_IDLE = 0x6B7885     # grey: demoted zero so it is not loud
COL_SEP = 0x3A4650
COL_HINT = 0x7A8794
COL_BUSY = 0xFFC857           # amber: a pass is running
COL_ERROR = 0xFF6B6B

MASK_PAD_X = 28               # side margin so a full poem line does not touch
MASK_PAD_Y = 20
MASK_GAP = 6                  # row gap; shrinks when a frame holds many lines
LINE_H = 22                   # 16px font line height plus leading
COL_RESULT = 0xF2F5F7         # near white: readable over the 80% black mask

MODEL_MISSING = ("OCR models missing on SD card.\n"
                 "Expected:\n"
                 "  " + DET_MODEL + "\n"
                 "  " + REC_MODEL + "\n"
                 "  " + DICT_PATH)

# The CJK font covers every character the recognition dictionary can emit: 6549
# glyphs = 6255 hanzi + 293 punctuation and symbol characters + a space
# (tools/ocr_charset.txt). A narrower subset renders the omitted characters as boxes
# even though the model read them correctly, which is why the full vocabulary ships.
# lv.font_load costs ~1.14 ms per glyph and is not cached by the loader, so glyph
# count is the only lever: this build loads in 7.5 s.
# The boot sequence preloads it into font_pool, so getting it here is a lookup.
FONT_FILE = "ocr_cjk_16.bin"


def _font(*names):
    """Return the first built-in font that exists among the candidate names."""
    for name in names:
        font = getattr(lv, name, None)
        if font:
            return font
    return None


def _ocr_font():
    """The prebaked CJK bitmap font for result text, from the shared font cache."""
    font = font_pool.get(FONT_FILE)
    if font is not None:
        return font
    print("[OCR] CJK font unavailable, falling back to a Latin font")
    return _font("font_montserrat_16", "font_montserrat_14")


def _reading_key(item):
    """Sort key for reading order: top-to-bottom, then left-to-right."""
    coords = item[0]
    ys = (coords[1], coords[3], coords[5], coords[7])
    xs = (coords[0], coords[2], coords[4], coords[6])
    return (min(ys), min(xs))


def _chw2hwc(features):
    """(3,H,W) CHW view -> contiguous (H,W,3). The post-process needs HWC."""
    s = (features.shape[0], features.shape[1], features.shape[2])
    c_hw = features.reshape((s[0], s[1] * s[2]))
    hw_c = c_hw.transpose()
    new_array = hw_c.copy()
    hwc = new_array.reshape((s[1], s[2], s[0]))
    del c_hw, hw_c, new_array
    return hwc


class OcrApp:
    def __init__(self, content, ctx):
        self.ctx = ctx
        self.content = content
        self.sensor = None
        self.kpu_det = None
        self.kpu_rec = None
        self.ai2d_det = None
        self.ai2d_rec = None
        self.dict_word = []
        # LIVE: preview runs. BUSY: one pass is running (inside step).
        # RESULT: overlays and panel show the last pass, preview paused.
        self.state = "LIVE"
        self.frame_count = 0
        self.last_fps_ms = time.ticks_ms()
        self.fps_text = "-"
        self.texts = []
        self._gc_tick = 0
        self._font_applied = False
        self._no_signal = NoSignalCounter()

        self.img_w = ctx.width
        self.img_h = ctx.height

        # ---- preview ----
        self.img_widget = lv.img(content)
        self.img_widget.set_size(self.img_w, self.img_h)
        self.img_widget.align(lv.ALIGN.CENTER, 0, 0)

        # ---- result view: full-screen translucent mask over the frozen frame,
        # one line of recognised text per row in reading order. A mask instead
        # of per-box captions because OCR strings are long and text regions
        # overlap, so captions would collide. Created before the top bar so the
        # bar keeps its z-order and stays readable above the mask.
        self.mask = lv.obj(content)
        self.mask.set_size(self.img_w, self.img_h)
        self.mask.align(lv.ALIGN.CENTER, 0, 0)
        self.mask.set_style_pad_all(0, 0)
        self.mask.set_style_pad_row(MASK_GAP, 0)
        self.mask.set_style_pad_left(MASK_PAD_X, 0)
        self.mask.set_style_pad_right(MASK_PAD_X, 0)
        self.mask.set_style_pad_top(TOPBAR_Y + TOPBAR_H + 10, 0)
        self.mask.set_style_pad_bottom(MASK_PAD_Y, 0)
        self.mask.set_style_bg_color(lv.color_hex(0x000000), 0)
        self.mask.set_style_bg_opa(lv.OPA._80, 0)
        self.mask.set_style_border_width(0, 0)
        self.mask.set_style_radius(0, 0)
        self.mask.clear_flag(lv.obj.FLAG.SCROLLABLE)
        self.mask.add_flag(lv.obj.FLAG.CLICKABLE)
        self.mask.add_flag(lv.obj.FLAG.HIDDEN)
        # Tap anywhere on the mask to resume the live preview.
        beep.on_click(self.mask, lambda e: self._toggle_scan())
        self.mask.set_flex_flow(lv.FLEX_FLOW.COLUMN)
        self.mask.set_flex_align(lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER,
                                 lv.FLEX_ALIGN.CENTER)
        self.panel_labels = []
        # With the boot preload the font is already resident, so apply it now; only a
        # build with BOOT_PRELOAD_FONTS=False pays the 7.5 s load, and then it is
        # deferred to the first scan so the preview still appears immediately.
        preloaded = font_pool.is_loaded(FONT_FILE)
        ready_font = _ocr_font() if preloaded else None
        for _ in range(MAX_BOXES):
            lab = lv.label(self.mask)
            lab.set_text("")
            lab.set_width(lv.pct(100))
            lab.set_style_text_align(lv.TEXT_ALIGN.CENTER, 0)
            lab.set_style_text_color(lv.color_hex(COL_RESULT), 0)
            if ready_font is not None:
                lab.set_style_text_font(ready_font, 0)
            lab.clear_flag(lv.obj.FLAG.CLICKABLE)
            lab.add_flag(lv.obj.FLAG.HIDDEN)
            self.panel_labels.append(lab)
        self._font_applied = ready_font is not None

        # ---- top bar: the whole strip is one click target (scan toggle) ----
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
        beep.on_click(self.stat_btn, lambda e: self._toggle_scan())
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
            lab.clear_flag(lv.obj.FLAG.CLICKABLE)
            return lab

        self.stat_title = _seg("OCR", COL_TITLE)
        self.stat_sep1 = _seg("|", COL_SEP)
        self.stat_fps = _seg("FPS -", COL_HINT)
        self.stat_sep2 = _seg("|", COL_SEP)
        self.stat_count = _seg("0 lines", COL_COUNT_IDLE)
        self.stat_hint = _seg("TAP scan", COL_HINT)

        self._init_ai()

    # ---------------- AI pipeline ----------------
    def _init_ai(self):
        t0 = time.ticks_ms()
        for path in (DET_MODEL, REC_MODEL, DICT_PATH):
            if not self._exists(path):
                raise AppUnavailable(MODEL_MISSING)

        # Shared cache: the boot sequence fills it, so this is a lookup. Keeping one
        # copy also stops every open from re-reading 6549 strings.
        self.dict_word = ocr_dict.words()
        if not self.dict_word:
            raise AppUnavailable(MODEL_MISSING)

        sensor = None
        try:
            sensor = Sensor(fps=30)
            sensor.reset()
            # chn1 = AI input, planar RGBP888 because ai2d declares NCHW_FMT.
            # Same size as the preview so post-process coordinates are already
            # screen coordinates.
            set_preview(sensor, CAM_CHN_ID_1, PREVIEW_W, PREVIEW_H)
            sensor.set_pixformat(Sensor.RGBP888, chn=CAM_CHN_ID_1)
            # chn2 = preview; as_lvgl_img_src only accepts a packed format.
            set_preview(sensor, CAM_CHN_ID_2, PREVIEW_W, PREVIEW_H)
            sensor.set_pixformat(Sensor.RGB888, chn=CAM_CHN_ID_2)
            sensor.run()
        except BaseException as error:
            print("[OCR] sensor init failed: {}".format(error))
            sys.print_exception(error)
            close_sensor(sensor)
            raise AppUnavailable(CAMERA_UNAVAILABLE)
        self.sensor = sensor

        try:
            self._load_models()
        except BaseException as error:
            print("[OCR] model load failed: {}".format(error))
            sys.print_exception(error)
            self._release_models()
            close_sensor(self.sensor)
            self.sensor = None
            raise AppUnavailable(MODEL_MISSING)

        print("[OCR] ready: load={}ms dict={} mem_free={}".format(
            time.ticks_diff(time.ticks_ms(), t0), len(self.dict_word),
            gc.mem_free()))
        self._update_stat()

    @staticmethod
    def _exists(path):
        try:
            return os.stat(path)[6] > 0
        except BaseException:
            return False

    def _load_models(self):
        # Both are pooled (boot preloads them), so these are lookups.
        self.kpu_det = model_get(DET_MODEL)
        self.kpu_rec = model_get(REC_MODEL)

        a2d = Ai2d()
        a2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                          np.uint8, np.uint8)
        # Letterbox 800x480 -> 640x640: scale 0.8, so 128 px of pad top and
        # bottom in the model space. The pad acts in output space (verified for
        # the other ai2d users).
        top, bottom, left, right, _ = letterbox_pad_param(
            [PREVIEW_W, PREVIEW_H], DET_IN)
        a2d.pad([0, 0, 0, 0, top, bottom, left, right], 0, [128, 128, 128])
        a2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
        a2d.build([1, 3, PREVIEW_H, PREVIEW_W], [1, 3, DET_IN[1], DET_IN[0]])
        self.ai2d_det = a2d
        print("[OCR] models loaded pad={} mem_free={}".format(top, gc.mem_free()))

    def _release_models(self):
        # The kpus belong to the shared pool; only the ai2d instances are per-app.
        self.kpu_det = None
        self.kpu_rec = None
        for name in ("ai2d_det", "ai2d_rec"):
            a2d = getattr(self, name)
            if a2d is None:
                continue
            try:
                a2d.deinit()
            except BaseException as error:
                print("[OCR]   {}.deinit error: {}".format(name, error))
            setattr(self, name, None)
        gc.collect()

    # ---------------- one recognition pass ----------------
    def _detect(self, ai_img):
        """Detector pass. Returns [(coords, crop), ...] in screen coordinates.

        ocr_post_process returns [[crop(1,h,w,3 HWC), [8 coords]], ...] with the
        coordinates already in the ai2d input space (= screen here). The output
        tensor is NHWC (1,640,640,2): channel 0 is the text mask.
        """
        src = ai_img.to_numpy_ref()
        tin = self.ai2d_det.run(src)
        self.kpu_det.set_input_tensor(0, tin)
        self.kpu_det.run()
        ot = self.kpu_det.get_output_tensor(0)
        try:
            raw = ot.to_numpy()
        finally:
            ot.release()            # native tensors must be released
        del tin

        hwc = _chw2hwc(src)
        boxes = aicube.ocr_post_process(raw[:, :, :, 0].reshape(-1),
                                        hwc.reshape(-1), DET_IN,
                                        [PREVIEW_W, PREVIEW_H],
                                        MASK_THR, BOX_THR)
        del raw, hwc, src

        kept = []
        for box in boxes or []:
            coords = box[1]
            xs = [coords[0], coords[2], coords[4], coords[6]]
            ys = [coords[1], coords[3], coords[5], coords[7]]
            w = max(xs) - min(xs)
            h = max(ys) - min(ys)
            if h < MIN_BOX_H or w < MIN_BOX_W:
                continue
            kept.append((w * h, box))
        # Biggest regions first: with more candidates than the cap, a real text
        # block beats the slivers. Insertion sort, at most a few dozen entries.
        for a in range(1, len(kept)):
            cur = kept[a]
            b = a - 1
            while b >= 0 and kept[b][0] < cur[0]:
                kept[b + 1] = kept[b]
                b -= 1
            kept[b + 1] = cur
        return [item[1] for item in kept[:MAX_BOXES]]

    def _recognize(self, crop):
        """Decode one crop array. crop is (1,h,w,3) HWC from the post-process.

        Output is (128,1,6549): argmax over the class axis, then CTC dedup
        (skip the blank = last dict entry, collapse repeats). The rec ai2d is
        rebuilt per crop because its input size changes with every box.
        """
        shape = crop.shape
        if len(shape) != 4:
            return ""
        h, w = shape[1], shape[2]
        if self.ai2d_rec is not None:
            try:
                self.ai2d_rec.deinit()
            except BaseException:
                pass
        a2d = Ai2d()
        a2d.set_ai2d_dtype(nn.ai2d_format.RGB_packed, nn.ai2d_format.NCHW_FMT,
                          np.uint8, np.uint8)
        top, bottom, left, right, _ = letterbox_pad_param([w, h], REC_IN)
        a2d.pad([0, 0, 0, 0, top, bottom, left, right], 0, [0, 0, 0])
        a2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
        a2d.build([shape[0], shape[1], shape[2], shape[3]],
                  [1, 3, REC_IN[1], REC_IN[0]])
        self.ai2d_rec = a2d

        tin = a2d.run(crop)
        self.kpu_rec.set_input_tensor(0, tin)
        self.kpu_rec.run()
        ot = self.kpu_rec.get_output_tensor(0)
        try:
            out = ot.to_numpy()
        finally:
            ot.release()

        # ulab ndarray has no .ndim; the class axis is the last one.
        axis = len(out.shape) - 1
        preds = np.argmax(out, axis=axis).reshape((-1))
        blank = len(self.dict_word) - 1
        text = ""
        prev = -1
        for i in range(len(preds)):
            v = int(preds[i])
            if v != blank and v != prev:
                text += self.dict_word[v]
            prev = v
        del out, preds, tin
        return text

    def _scan(self):
        """Full pass on the current frame: detect, recognize, draw. Blocking by
        design (~0.3 s det + ~70 ms per box; the first call also loads the CJK
        font, which costs about 4.9 s)."""
        self._ensure_font()
        self.texts = []
        try:
            ai_img = self.sensor.snapshot(chn=CAM_CHN_ID_1)
            boxes = self._detect(ai_img)
        except BaseException as error:
            print("[OCR] detect error:", error)
            sys.print_exception(error)
            self._show_error("detect error")
            return
        for box in boxes:
            try:
                text = self._recognize(box[0])
            except BaseException as error:
                print("[OCR] recognize error:", error)
                text = ""
            if text:
                self.texts.append((box[1], text))
            gc.collect()
        self._show_results()
        self._update_stat()

    # ---------------- drawing ----------------
    def _ensure_font(self):
        """Apply the CJK font to the result labels, loading it on first use.

        Called from the scan pass, never from __init__: the load freezes the
        screen for ~4.9 s, which at open looks like the app has hung before any
        preview is shown.
        """
        if self._font_applied:
            return
        font = _ocr_font()
        if font is not None:
            for lab in self.panel_labels:
                lab.set_style_text_font(font, 0)
        self._font_applied = True

    def _show_results(self):
        # Reading order, not detection order: the post-process emits boxes in
        # mask scan order, which splits a column of text unpredictably.
        order = sorted(self.texts, key=_reading_key)
        n = len(order) if len(order) < MAX_BOXES else MAX_BOXES

        # Shrink the row gap when many lines have to fit under the top bar.
        avail = self.img_h - (TOPBAR_Y + TOPBAR_H + 10) - MASK_PAD_Y
        gap = MASK_GAP
        if n > 1:
            gap = (avail - n * LINE_H) // (n - 1)
            if gap < 0:
                gap = 0
            elif gap > MASK_GAP:
                gap = MASK_GAP
        self.mask.set_style_pad_row(gap, 0)

        for i in range(MAX_BOXES):
            lab = self.panel_labels[i]
            if i >= n:
                lab.add_flag(lv.obj.FLAG.HIDDEN)
                continue
            lab.set_text(order[i][1])
            lab.clear_flag(lv.obj.FLAG.HIDDEN)

        if n:
            self.mask.clear_flag(lv.obj.FLAG.HIDDEN)
        else:
            self.mask.add_flag(lv.obj.FLAG.HIDDEN)

    def _clear_overlays(self):
        for lab in self.panel_labels:
            lab.add_flag(lv.obj.FLAG.HIDDEN)
        self.mask.add_flag(lv.obj.FLAG.HIDDEN)

    def _update_stat(self):
        n = len(self.texts)
        if n > 0:
            self.stat_count.set_text("{} lines".format(n))
            self.stat_count.set_style_text_color(lv.color_hex(COL_COUNT), 0)
        else:
            self.stat_count.set_text("0 lines")
            self.stat_count.set_style_text_color(
                lv.color_hex(COL_COUNT_IDLE), 0)
        if self.state == "BUSY":
            if not self._font_applied:
                # First scan: most of the wait is the one-off font load, so say
                # so instead of "READING..." for five seconds.
                self.stat_hint.set_text("LOADING...")
            else:
                self.stat_hint.set_text("READING...")
            self.stat_hint.set_style_text_color(lv.color_hex(COL_BUSY), 0)
        elif self.state == "RESULT":
            self.stat_hint.set_text("TAP resume")
            self.stat_hint.set_style_text_color(lv.color_hex(COL_HINT), 0)
        else:
            self.stat_hint.set_text("TAP scan")
            self.stat_hint.set_style_text_color(lv.color_hex(COL_HINT), 0)

    def _show_error(self, text):
        self.stat_hint.set_text(text)
        self.stat_hint.set_style_text_color(lv.color_hex(COL_ERROR), 0)

    def _toggle_scan(self):
        if self.state == "LIVE":
            self.state = "BUSY"
            self._update_stat()
        elif self.state == "RESULT":
            self.state = "LIVE"
            self.texts = []
            self._clear_overlays()
            self._update_stat()

    # ---------------- frame ----------------
    def step(self):
        if self.sensor is None:
            return

        if self.ctx.take_ok():
            self._toggle_scan()

        if self.state != "LIVE":
            if self.state == "BUSY":
                # Paint the hint before the blocking pass, otherwise the screen
                # shows nothing for the whole second.
                try:
                    lv.refr_now(None)
                except BaseException:
                    pass
                self._scan()
                self.state = "RESULT"
                # _scan updated the bar while still BUSY ("READING..."); repaint
                # it now so the hint tells the user the result can be dismissed.
                self._update_stat()
            return

        try:
            img = self.sensor.snapshot(chn=CAM_CHN_ID_2)
        except BaseException as error:
            print("[OCR] snapshot error:", error)
            self._show_error("snapshot error")
            self._no_signal.on_fail(self.ctx)
            return
        self._no_signal.on_ok()

        try:
            img.as_lvgl_img_src(self.img_widget)
            self.img_widget.invalidate()
        except BaseException:
            self._show_error("display error")
            return

        self._gc_tick += 1
        if self._gc_tick >= 8:
            self._gc_tick = 0
            gc.collect()

        self.frame_count += 1
        now = time.ticks_ms()
        elapsed = time.ticks_diff(now, self.last_fps_ms)
        if elapsed >= 1000:
            self.fps_text = "{:.1f}".format(self.frame_count * 1000.0 / elapsed)
            self.frame_count = 0
            self.last_fps_ms = now
            self.stat_fps.set_text("FPS {}".format(self.fps_text))

    def destroy(self):
        print("[OCR] destroy begin")
        self._release_models()
        if self.sensor is not None:
            for name in ("stop", "deinit"):
                try:
                    getattr(self.sensor, name)()
                except BaseException as error:
                    print("[OCR]   sensor.{} error: {}".format(name, error))
            self.sensor = None
        gc.collect()
        print("[OCR] destroy done | mem_free={}".format(gc.mem_free()))


def create_app(content, ctx):
    return OcrApp(content, ctx)
