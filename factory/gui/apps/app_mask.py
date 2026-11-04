# Mask: live face parsing (RetinaFace 320 -> face_parse) over a 640x384 preview.
#
# Two models, both resident in the pool. Detection letterboxes 640x384 into the
# 320 square (scale 0.5, pad_top 64), so returned y carries +128 and rgb_param is
# [640, 512]; the largest box is then cropped by affine into the parse input.
# Measured on board: det 9 ms, parse 110 ms warm, post 16 ms, clear 10 ms, and
# the full step lands at 205 ms/frame (4.9 fps) once the ARGB overlay is blended.
# A second face adds 110 ms, so only the largest is parsed.
#
# The 983 KB ARGB mask (W*H*4) fixes the preview size: 800x480 would need 1.5 MB,
# which does not fit after release_home. One bytearray is reused every frame
# behind an lv.img_dsc_t and zeroed with *= 0, because the post-process leaves
# every pixel outside the mask untouched and the old mask would smear.
#
# Keys: CANCEL(GPIO0) exits; OK(GPIO53) or a tap on the bar freezes the picture.

import gc
import os
import sys
import time

import lvgl as lv
import ulab.numpy as np
import nncase_runtime as nn
import aidemo
from libs.AI2D import Ai2d
from media.sensor import Sensor, CAM_CHN_ID_1, CAM_CHN_ID_2
from ui import beep
from core import i18n
from core.errors import (AppUnavailable, CAMERA_UNAVAILABLE, NoSignalCounter,
                       close_sensor)
from hardware.camera_fov import set_preview
from core.model_pool import get as model_get

PREVIEW_W = 640               # preview == AI frame == mask buffer width
PREVIEW_H = 384

DET_MODEL = "/sdcard/examples/kmodel/face_detection_320.kmodel"
PARSE_MODEL = "/sdcard/examples/kmodel/face_parse.kmodel"
ANCHOR_PATH = "/sdcard/examples/utils/prior_data_320.bin"
MODEL_IN = 320
ANCHOR_N = 4200

DET_CONF = 0.50
NMS_TH = 0.2
# The parse crop keeps a margin around the box; 2.7 is the factor the model was
# trained with and a smaller one clips the ears and jaw.
CROP_FACTOR = 2.7

TOPBAR_H = 56
TOPBAR_Y = 10
TOPBAR_LEFT = 8
TOPBAR_RIGHT = 8
SEG_PAD = 18
PRINT_MS = 200

COL_TITLE = 0x00E5A0
COL_FPS = 0xFFC857
COL_CNT = 0x64B5F6
COL_CNT_IDLE = 0x6B7885
COL_SEP = 0x3A4650
COL_HINT = 0x7A8794
COL_FROZEN = 0xFF8A65
COL_ERROR = 0xFF6B6B

MODEL_MISSING = ("Face model or anchors missing on SD card.\n"
                 "Expected:\n"
                 "  /sdcard/examples/kmodel/face_detection_320.kmodel\n"
                 "  /sdcard/examples/kmodel/face_parse.kmodel\n"
                 "  /sdcard/examples/utils/prior_data_320.bin")


def det_letterbox():
    """Letterbox from 640x384 to the 320 square: scale 0.5, pad_top 64."""
    scale = min(float(MODEL_IN) / PREVIEW_W, float(MODEL_IN) / PREVIEW_H)
    pad_top = int((MODEL_IN - int(PREVIEW_H * scale)) / 2)
    return scale, pad_top


def y_pad_offset():
    """Returned y still contains the letterbox padding: real y = y - this.

    Derivable, not empirical: the post-process inverts y_out = (pad_top +
    screen_y * scale) / scale, so the offset is pad_top / scale (= 128 here).
    """
    scale, pad_top = det_letterbox()
    return int(pad_top / scale)


def rgb_param():
    """Fifth argument of face_det_post_process.

    It sets both the letterbox scale and the clipping bounds, so it must be the
    preview width by the preview height plus the y offset: [640, 512]. A height
    of 384 clips a face at the bottom to a single strip, and the width must stay
    the larger value so the scale keeps being decided by it.
    """
    return [PREVIEW_W, PREVIEW_H + y_pad_offset()]


def _font(*names):
    for name in names:
        font = getattr(lv, name, None)
        if font:
            return font
    return None


def crop_matrix(box):
    """Affine coefficients that map `box` (preview domain) into the 320 square."""
    x, y, w, h = box
    scale = MODEL_IN * 2.0 / (CROP_FACTOR * (h if h > w else w))
    half = MODEL_IN / 2.0
    return [scale, 0, half - scale * (x + w / 2.0),
            0, scale, half - scale * (y + h / 2.0)]


class MaskApp:
    def __init__(self, content, ctx):
        self.ctx = ctx
        self.content = content
        self.sensor = None
        self.kpu_det = None
        self.kpu_parse = None
        self.a2d_det = None
        self.a2d_parse = None
        self.anchors = None
        self.masks = None          # numpy view over _mask_buf
        self._mask_buf = None      # bytearray backing the ARGB pixels
        self._mask_dsc = None      # persistent lv.img_dsc_t over _mask_buf
        self.frozen = False
        self.frame_count = 0
        self.last_fps_ms = time.ticks_ms()
        self.fps_text = "-"
        self.last_faces = 0
        self._gc_tick = 0
        self._print_ms = 0
        self._no_signal = NoSignalCounter()
        self.y_off = y_pad_offset()

        self.img_w = ctx.width
        self.img_h = ctx.height
        self.off_x = (self.img_w - PREVIEW_W) // 2
        self.off_y = (self.img_h - PREVIEW_H) // 2

        self.img_widget = lv.img(content)
        self.img_widget.set_size(PREVIEW_W, PREVIEW_H)
        self.img_widget.align(lv.ALIGN.TOP_LEFT, self.off_x, self.off_y)

        # Mask overlay: same rect, stacked above the preview, hidden until a face
        # is parsed.
        self.mask_widget = lv.img(content)
        self.mask_widget.set_size(PREVIEW_W, PREVIEW_H)
        self.mask_widget.align(lv.ALIGN.TOP_LEFT, self.off_x, self.off_y)
        self.mask_widget.add_flag(lv.obj.FLAG.HIDDEN)

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
        beep.on_click(self.stat_btn, lambda e: self._toggle_freeze())
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
            f = i18n.font_for(text, bar_font)
            if f is not None:
                lab.set_style_text_font(f, 0)
            lab.clear_flag(lv.obj.FLAG.CLICKABLE)   # the whole strip is the target
            return lab

        self.seg_title = _seg(i18n.name("mask", "Mask"), COL_TITLE)
        self.seg_sep1 = _seg("|", COL_SEP)
        self.seg_count = _seg(i18n.t("ai.no_face"), COL_CNT_IDLE)
        self.seg_sep2 = _seg("|", COL_SEP)
        self.seg_fps = _seg("FPS -", COL_FPS)
        self.seg_hint = _seg(i18n.t("ai.tap_freeze"), COL_HINT)

        self._init_ai()

    # ---------- AI pipeline ----------
    @staticmethod
    def _exists(path):
        try:
            return os.stat(path)[6] > 0
        except BaseException:
            return False

    def _init_ai(self):
        t0 = time.ticks_ms()
        for path in (DET_MODEL, PARSE_MODEL, ANCHOR_PATH):
            if not self._exists(path):
                raise AppUnavailable(MODEL_MISSING)

        try:
            raw = open(ANCHOR_PATH, "rb").read()
            self.anchors = np.frombuffer(raw, dtype=np.float).reshape(
                (ANCHOR_N, 4))
            del raw
        except BaseException as error:
            print("[Mask] anchor load failed:", error)
            sys.print_exception(error)
            raise AppUnavailable(MODEL_MISSING)

        sensor = None
        try:
            sensor = Sensor(fps=30)
            sensor.reset()
            # chn1 feeds both stages (detection letterbox, then the parse crop);
            # it must be RGBP888 because ai2d declares NCHW input. chn2 is the
            # packed preview that as_lvgl_img_src accepts.
            set_preview(sensor, CAM_CHN_ID_1, PREVIEW_W, PREVIEW_H)
            sensor.set_pixformat(Sensor.RGBP888, chn=CAM_CHN_ID_1)
            set_preview(sensor, CAM_CHN_ID_2, PREVIEW_W, PREVIEW_H)
            sensor.set_pixformat(Sensor.RGB888, chn=CAM_CHN_ID_2)
            sensor.run()
        except BaseException as error:
            print("[Mask] sensor init failed:", error)
            sys.print_exception(error)
            close_sensor(sensor)
            raise AppUnavailable(CAMERA_UNAVAILABLE)
        self.sensor = sensor

        self.kpu_det = model_get(DET_MODEL)
        self.kpu_parse = model_get(PARSE_MODEL)

        _scale, pad_top = det_letterbox()
        a2d = Ai2d()
        a2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                           np.uint8, np.uint8)
        a2d.pad([0, 0, 0, 0, pad_top, pad_top, 0, 0], 0, [104, 117, 123])
        a2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
        a2d.build([1, 3, PREVIEW_H, PREVIEW_W],
                  [1, 3, MODEL_IN, MODEL_IN])
        self.a2d_det = a2d

        # The parse crop is a per-face affine, so only the dtype is set here;
        # affine + build run on each frame because the box moves.
        a2d_parse = Ai2d()
        a2d_parse.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT,
                                 nn.ai2d_format.NCHW_FMT, np.uint8, np.uint8)
        self.a2d_parse = a2d_parse

        # ARGB mask: a bytearray so one dsc can point at it for the whole session
        # (the binding rejects ARGB in as_lvgl_img_src and rejects an ndarray as
        # dsc data). The numpy view shares this memory, so post-processing
        # straight into the view makes the pixels visible without a copy.
        try:
            self._mask_buf = bytearray(PREVIEW_W * PREVIEW_H * 4)
            self.masks = np.frombuffer(self._mask_buf,
                                       dtype=np.uint8).reshape(
                (PREVIEW_H, PREVIEW_W, 4))
        except BaseException as error:
            print("[Mask] masks alloc failed:", error)
            close_sensor(self.sensor)
            self.sensor = None
            raise AppUnavailable("Mask buffer too large for this build.")
        self._mask_dsc = lv.img_dsc_t({
            "header": {"cf": lv.COLOR_FORMAT.ARGB8888,
                       "w": PREVIEW_W, "h": PREVIEW_H},
            "data_size": len(self._mask_buf), "data": self._mask_buf})

        print("[Mask] ready: load={}ms y_off={} mem_free={}".format(
            time.ticks_diff(time.ticks_ms(), t0), self.y_off, gc.mem_free()))
        self._update_stat()

    def _detect(self, img):
        """Boxes in the preview domain with the letterbox padding removed.

        Every output tensor is to_numpy()d and released right away; a missed
        release leaks native memory and the board drops its serial link.
        """
        tin = self.a2d_det.run(img.to_numpy_ref())
        self.kpu_det.set_input_tensor(0, tin)
        self.kpu_det.run()
        outs = []
        for i in range(self.kpu_det.outputs_size()):
            ot = self.kpu_det.get_output_tensor(i)
            try:
                outs.append(ot.to_numpy())
            finally:
                ot.release()
        try:
            res = aidemo.face_det_post_process(
                DET_CONF, NMS_TH, MODEL_IN, self.anchors, rgb_param(), outs)
        finally:
            del outs, tin
        if len(res) == 0 or not res[0]:
            return None
        boxes = []
        for b in res[0]:
            w = int(b[2])
            h = int(b[3])
            if w <= 0 or h <= 0:
                continue        # clipped at the frame edge: nothing to crop
            boxes.append((int(b[0]), int(b[1]) - self.y_off, w, h))
        return boxes or None

    def _parse(self, img, box):
        """Run the parse model on one box; returns the raw (1,320,320,19) map."""
        m = crop_matrix(box)
        self.a2d_parse.affine(nn.interp_method.cv2_bilinear, 0, 0, 127, 1, m)
        self.a2d_parse.build([1, 3, PREVIEW_H, PREVIEW_W],
                             [1, 3, MODEL_IN, MODEL_IN])
        tin = self.a2d_parse.run(img.to_numpy_ref())
        self.kpu_parse.set_input_tensor(0, tin)
        self.kpu_parse.run()
        out = None
        for i in range(self.kpu_parse.outputs_size()):
            ot = self.kpu_parse.get_output_tensor(i)
            try:
                arr = ot.to_numpy()
                if out is None:
                    out = arr
            finally:
                ot.release()
        del tin
        return out

    def _infer(self, img):
        """Detect, then parse the largest face into the mask buffer.

        Returns the number of parsed faces (0 or 1) for the bar counter.
        """
        boxes = self._detect(img)
        if not boxes:
            return 0
        best = 0
        for i in range(1, len(boxes)):
            if boxes[i][2] * boxes[i][3] > boxes[best][2] * boxes[best][3]:
                best = i
        box = boxes[best]
        res = self._parse(img, box)
        if res is None:
            return 0
        # The post-process writes only inside the mask, so the previous frame has
        # to be erased first or a moving face leaves a trail.
        self.masks *= 0
        aidemo.face_parse_post_process(self.masks, [PREVIEW_W, PREVIEW_H],
                                       [PREVIEW_W, PREVIEW_H], MODEL_IN,
                                       list(box), res)
        del res
        return 1

    # ---------- drawing ----------
    def _update_stat(self):
        self.seg_fps.set_text("FPS {}".format(self.fps_text))
        if self.last_faces > 0:
            self.seg_count.set_text(i18n.t("ai.face_one"))
            self.seg_count.set_style_text_color(lv.color_hex(COL_CNT), 0)
        else:
            self.seg_count.set_text(i18n.t("ai.no_face"))
            self.seg_count.set_style_text_color(lv.color_hex(COL_CNT_IDLE), 0)
        if self.frozen:
            self.seg_hint.set_text(i18n.t("ai.frozen"))
            self.seg_hint.set_style_text_color(lv.color_hex(COL_FROZEN), 0)
        else:
            self.seg_hint.set_text(i18n.t("ai.tap_freeze"))
            self.seg_hint.set_style_text_color(lv.color_hex(COL_HINT), 0)

    def _show_error(self, text):
        self.seg_hint.set_text(text)
        self.seg_hint.set_style_text_color(lv.color_hex(COL_ERROR), 0)

    def _toggle_freeze(self):
        self.frozen = not self.frozen
        self._update_stat()

    def step(self):
        if self.sensor is None:
            return
        if self.ctx.take_ok():
            self._toggle_freeze()
        if self.frozen:
            return

        try:
            ai_img = self.sensor.snapshot(chn=CAM_CHN_ID_1)
            img = self.sensor.snapshot(chn=CAM_CHN_ID_2)
        except BaseException as error:
            self._show_error(i18n.t("ai.snapshot_error"))
            self._no_signal.on_fail(self.ctx)
            return
        self._no_signal.on_ok()

        n = 0
        try:
            n = self._infer(ai_img)
        except BaseException as error:
            print("[Mask] infer error:", error)
            sys.print_exception(error)

        try:
            img.as_lvgl_img_src(self.img_widget)
            self.img_widget.invalidate()
        except BaseException as error:
            self._show_error(i18n.t("ai.display_error"))
            return

        if n > 0:
            self.mask_widget.clear_flag(lv.obj.FLAG.HIDDEN)
            self.mask_widget.set_src(self._mask_dsc)
            self.mask_widget.invalidate()
        else:
            self.mask_widget.add_flag(lv.obj.FLAG.HIDDEN)
        self.last_faces = n
        self._print_state(n)

        # Sensor frames and LVGL sources are cyclic garbage that refcounting
        # never frees; without a periodic collect to_numpy starts failing.
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
            self._update_stat()

    def _print_state(self, n):
        now = time.ticks_ms()
        if time.ticks_diff(now, self._print_ms) < PRINT_MS:
            return
        self._print_ms = now
        print("[Mask] parsed={}".format(n))

    def destroy(self):
        print("[Mask] destroy begin")
        # Both kpus belong to the pool; only the ai2d instances are per-app.
        self.kpu_det = None
        self.kpu_parse = None
        for name in ("a2d_det", "a2d_parse"):
            a2d = getattr(self, name)
            if a2d is not None:
                try:
                    a2d.deinit()
                except BaseException as e:
                    print("[Mask]   {}.deinit error: {}".format(name, e))
                setattr(self, name, None)
        self.masks = None
        self._mask_buf = None
        self._mask_dsc = None
        if self.sensor is not None:
            for name in ("stop", "deinit"):
                try:
                    getattr(self.sensor, name)()
                except BaseException as e:
                    print("[Mask]   sensor.{} error: {}".format(name, e))
            self.sensor = None
        gc.collect()
        print("[Mask] destroy done | mem_free={}".format(gc.mem_free()))


def create_app(content, ctx):
    return MaskApp(content, ctx)
