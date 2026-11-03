# Liveness app: live spoof detection on the largest face.
#
# Fourth stage of the Face group. Detect -> expand the box -> letterboxed crop into
# 112x112 -> face_liveness_rgb.kmodel -> one score, where a higher value means more
# likely a real person.
#
# The crop is **expanded** by CROP_PAD_W / CROP_PAD_H before the resize. That margin
# is what carries the signal, and the measurements are unambiguous:
#
#                     expanded 224x192    tight box
#   live person       0.999 (index 1)     0.06-0.32, overlapping
#   screen photo      0.000 (index 1)     0.11-0.33, overlapping
#
# A tight crop collapses both classes onto the same values, so it cannot be used even
# though it looks like the more faithful input. Op order is crop -> resize -> pad
# with mid grey 128, which is also what the model was trained against.
#
# Measured on board: detector 9.6 ms, liveness run 1.36 ms (constant), model loads
# 198 ms and 78 ms (380 KB), and the app holds 25.4 fps with a face in view.
#
# The output is one tensor of shape [1, 2] summing to 1, and **index 1 is the live
# probability**: a live face measured 0.9989-0.9997 there while a screen-replayed
# photo measured 0.0001-0.0004. Reading index 0 inverts every verdict.
#
# Score spread with the expanded crop: live 0.9989-0.9997 over 24 frames, photo
# 0.0001-0.0004. The two classes are three orders of magnitude apart, so 0.50 is a
# safe default with a wide margin on both sides.

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
from model_pool import get as model_get
from apps.error import (AppUnavailable, CAMERA_UNAVAILABLE, NoSignalCounter,
                        close_sensor)

PREVIEW_W = 800
PREVIEW_H = 480

DET_MODEL = "/sdcard/examples/kmodel/face_detection_320.kmodel"
LIVE_MODEL = "/sdcard/examples/kmodel/face_liveness_rgb.kmodel"
ANCHOR_PATH = "/sdcard/examples/utils/prior_data_320.bin"
DET_IN = [320, 320]
LIVE_IN = 112
ANCHOR_N = 4200

DET_CONF = 0.50
NMS_TH = 0.2

# Crop expansion, in source pixels. Required, not cosmetic: see the note above.
CROP_PAD_W = 224
CROP_PAD_H = 192
PAD_GREY = [128, 128, 128]

# Score presets. Index 1 of the model output is the live probability: a live face
# measured 0.9989-0.9997 and a screen photo 0.0001-0.0004, so 0.50 sits in the middle
# of an empty band.
THRESHOLDS = [
    ("Lenient 0.30", 0.30),
    ("Normal 0.50", 0.50),
    ("Strict 0.75", 0.75),
]
DEFAULT_TH = 1

TOPBAR_H = 56
TOPBAR_Y = 10
TOPBAR_LEFT = 8
TOPBAR_RIGHT = 8
SEG_PAD = 18

COL_TITLE = 0x8FBF9F
COL_FPS = 0xFFC857
COL_REAL = 0x00E5A0
COL_SPOOF = 0xFF6B6B
COL_IDLE = 0x6B7885
COL_SEP = 0x3A4650
COL_HINT = 0x7A8794
COL_FROZEN = 0xFF8A65
COL_ERROR = 0xFF6B6B

LAB_H = 18

MODEL_MISSING = ("Liveness model missing on SD card.\n"
                 "Expected:\n"
                 "  /sdcard/examples/kmodel/face_detection_320.kmodel\n"
                 "  /sdcard/examples/kmodel/face_liveness_rgb.kmodel\n"
                 "  /sdcard/examples/utils/prior_data_320.bin")


def letterbox():
    """Letterbox parameters from 800x480 to 320x320: (scale, pad_top)."""
    mw, mh = DET_IN
    scale = min(float(mw) / PREVIEW_W, float(mh) / PREVIEW_H)
    pad_top = int((mh - int(PREVIEW_H * scale)) / 2)
    return scale, pad_top


def y_pad_offset():
    """Detector y includes the letterbox pad; subtract this to reach image space."""
    scale, pad_top = letterbox()
    return int(pad_top / scale)


def rgb_param():
    """[W, H + y_off]: the post-process uses it as the clip rectangle as well."""
    return [PREVIEW_W, PREVIEW_H + y_pad_offset()]


def pad_param(src_w, src_h, dst_w, dst_h):
    """Centre a src rectangle in dst: (top, bottom, left, right, scale)."""
    ratio = min(float(dst_w) / src_w, float(dst_h) / src_h)
    new_w = int(src_w * ratio)
    new_h = int(src_h * ratio)
    pad_w = (dst_w - new_w) // 2
    pad_h = (dst_h - new_h) // 2
    return pad_h, dst_h - new_h - pad_h, pad_w, dst_w - new_w - pad_w, ratio


def _font(*names):
    for name in names:
        font = getattr(lv, name, None)
        if font:
            return font
    return None


def label_dy(box_y):
    """Y offset of a label relative to its box top: keep it inside the frame."""
    return -LAB_H if box_y >= LAB_H else 1


def expand_box(box):
    """Grow the box by the crop padding, clamped to the frame.

    Clamping matters at the borders: a crop rectangle that reads past the buffer
    would be rejected by ai2d or produce garbage.
    """
    x, y, w, h = box
    cw = w + CROP_PAD_W
    ch = h + CROP_PAD_H
    cx = x - (cw - w) // 2
    cy = y - (ch - h) // 2
    if cx < 0:
        cw += cx
        cx = 0
    if cy < 0:
        ch += cy
        cy = 0
    if cx + cw > PREVIEW_W:
        cw = PREVIEW_W - cx
    if cy + ch > PREVIEW_H:
        ch = PREVIEW_H - cy
    if cw <= 1 or ch <= 1:
        return None
    return cx, cy, cw, ch


class FaceLivenessApp:
    def __init__(self, content, ctx):
        self.ctx = ctx
        self.content = content
        self.sensor = None
        self.kpu_det = None
        self.kpu_live = None
        self.a2d_det = None
        self.a2d_live = None
        self.anchors = None

        self.frozen = False
        self.frame_count = 0
        self.last_fps_ms = time.ticks_ms()
        self.fps_text = "-"
        self.y_off = y_pad_offset()
        self._gc_tick = 0
        self._no_signal = NoSignalCounter()

        self.has_face = False
        self.score = None            # last liveness score, None = not computed
        self.is_real = None          # True/False once a score exists
        self.th_idx = DEFAULT_TH
        self._shown = None

        self.picker = None           # threshold overlay (None = closed)

        self.img_w = ctx.width
        self.img_h = ctx.height

        self.img_widget = lv.img(content)
        self.img_widget.set_size(self.img_w, self.img_h)
        self.img_widget.align(lv.ALIGN.CENTER, 0, 0)

        self.box = lv.obj(content)
        self.box.set_style_bg_opa(lv.OPA.TRANSP, 0)
        self.box.set_style_border_width(3, 0)
        self.box.set_style_radius(6, 0)
        self.box.clear_flag(lv.obj.FLAG.SCROLLABLE)
        self.box.add_flag(lv.obj.FLAG.HIDDEN)

        self.box_label = lv.label(content)
        self.box_label.add_flag(lv.obj.FLAG.HIDDEN)
        self.box_label.set_style_bg_opa(lv.OPA._80, 0)
        self.box_label.set_style_pad_hor(4, 0)
        self.box_label.set_style_pad_ver(1, 0)
        self.box_label.set_style_radius(3, 0)
        label_font = _font("font_montserrat_14", "font_montserrat_12")
        if label_font is not None:
            self.box_label.set_style_text_font(label_font, 0)

        self._build_stat_bar()
        self._init_ai()
        self._update_stat()

    # ---------- stat bar ----------
    def _build_stat_bar(self):
        bar_w = self.img_w - TOPBAR_LEFT - TOPBAR_RIGHT
        self.stat_btn = lv.btn(self.content)
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
        self.bar_font = bar_font

        def seg(text, color):
            lab = lv.label(self.stat_btn)
            lab.set_text(text)
            lab.set_width(lv.SIZE_CONTENT)
            lab.set_style_text_color(lv.color_hex(color), 0)
            if bar_font is not None:
                lab.set_style_text_font(bar_font, 0)
            lab.clear_flag(lv.obj.FLAG.CLICKABLE)
            return lab

        self.seg_title = seg("Liveness", COL_TITLE)
        self.seg_sep1 = seg("|", COL_SEP)
        self.seg_verdict = seg("no face", COL_IDLE)
        self.seg_sep2 = seg("|", COL_SEP)
        self.seg_fps = seg("FPS -", COL_FPS)
        self.seg_hint = seg("OK: freeze", COL_HINT)

    def _update_stat(self):
        self.seg_title.set_text(
            "Liveness {:.2f}".format(THRESHOLDS[self.th_idx][1]))
        self.seg_title.set_style_text_color(lv.color_hex(COL_TITLE), 0)

        if not self.has_face:
            self.seg_verdict.set_text("no face")
            self.seg_verdict.set_style_text_color(lv.color_hex(COL_IDLE), 0)
        elif self.score is None:
            # A face is there but no score yet: say so rather than guessing.
            self.seg_verdict.set_text("measuring")
            self.seg_verdict.set_style_text_color(lv.color_hex(COL_IDLE), 0)
        elif self.is_real:
            self.seg_verdict.set_text("REAL {:.2f}".format(self.score))
            self.seg_verdict.set_style_text_color(lv.color_hex(COL_REAL), 0)
        else:
            self.seg_verdict.set_text("SPOOF {:.2f}".format(self.score))
            self.seg_verdict.set_style_text_color(lv.color_hex(COL_SPOOF), 0)

        self.seg_fps.set_text("FPS {}".format(self.fps_text))
        if self.picker is not None:
            self.seg_hint.set_text("FROZEN")
            self.seg_hint.set_style_text_color(lv.color_hex(COL_FROZEN), 0)
        elif self.frozen:
            self.seg_hint.set_text("FROZEN")
            self.seg_hint.set_style_text_color(lv.color_hex(COL_FROZEN), 0)
        else:
            self.seg_hint.set_text("TAP: threshold")
            self.seg_hint.set_style_text_color(lv.color_hex(COL_HINT), 0)

    def _show_error(self, text):
        self.seg_hint.set_text(text)
        self.seg_hint.set_style_text_color(lv.color_hex(COL_ERROR), 0)

    # ---------- AI pipeline ----------
    def _init_ai(self):
        t0 = time.ticks_ms()
        try:
            if (not self._exists(DET_MODEL) or not self._exists(LIVE_MODEL)
                    or not self._exists(ANCHOR_PATH)):
                raise AppUnavailable(MODEL_MISSING)
        except AppUnavailable:
            raise
        except BaseException as error:
            print("[Liveness] file check error:", error)

        try:
            f = open(ANCHOR_PATH, "rb")
            raw = f.read()
            f.close()
            del f
            self.anchors = np.frombuffer(raw, dtype=np.float).reshape(
                (ANCHOR_N, 4))
            del raw
        except BaseException as error:
            print("[Liveness] anchor load failed:", error)
            raise AppUnavailable(MODEL_MISSING)

        sensor = None
        try:
            sensor = Sensor(fps=30)
            sensor.reset()
            # chn1 = AI input, planar RGBP888 because ai2d declares NCHW_FMT.
            sensor.set_framesize(w=PREVIEW_W, h=PREVIEW_H, chn=CAM_CHN_ID_1,
                                 crop=True)
            sensor.set_pixformat(Sensor.RGBP888, chn=CAM_CHN_ID_1)
            # chn2 = preview; as_lvgl_img_src only accepts a packed format.
            sensor.set_framesize(w=PREVIEW_W, h=PREVIEW_H, chn=CAM_CHN_ID_2,
                                 crop=True)
            sensor.set_pixformat(Sensor.RGB888, chn=CAM_CHN_ID_2)
            sensor.run()
        except BaseException as error:
            print("[Liveness] sensor init failed:", error)
            sys.print_exception(error)
            close_sensor(sensor)
            raise AppUnavailable(CAMERA_UNAVAILABLE)
        self.sensor = sensor

        self._load_models()
        print("[Liveness] ready: load={}ms th={:.2f} mem_free={}".format(
            time.ticks_diff(time.ticks_ms(), t0), THRESHOLDS[self.th_idx][1],
            gc.mem_free()))

    @staticmethod
    def _exists(path):
        try:
            return os.stat(path)[6] > 0
        except BaseException:
            return False

    def _load_models(self):
        # Both are pooled (boot preloads them), so these are lookups.
        self.kpu_det = model_get(DET_MODEL)
        self.kpu_live = model_get(LIVE_MODEL)

        _scale, pad_top = letterbox()
        a2d = Ai2d()
        a2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                           np.uint8, np.uint8)
        a2d.pad([0, 0, 0, 0, pad_top, pad_top, 0, 0], 0, [104, 117, 123])
        a2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
        a2d.build([1, 3, PREVIEW_H, PREVIEW_W],
                  [1, 3, DET_IN[1], DET_IN[0]])
        self.a2d_det = a2d

        # The liveness crop changes every frame (it follows the box), so only the
        # dtype is set here and crop/pad/resize/build run per frame.
        self.a2d_live = Ai2d()
        self.a2d_live.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT,
                                     nn.ai2d_format.NCHW_FMT, np.uint8, np.uint8)

    def _release_models(self):
        # The kpus belong to the shared pool; only the ai2d instances are per-app.
        self.kpu_det = None
        self.kpu_live = None
        for name in ("a2d_det", "a2d_live"):
            a2d = getattr(self, name)
            if a2d is not None:
                try:
                    a2d.deinit()
                except BaseException as e:
                    print("[Liveness]   {}.deinit error: {}".format(name, e))
                setattr(self, name, None)
        gc.collect()

    def _detect(self, img):
        """Largest face box in image coordinates, or None."""
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
                DET_CONF, NMS_TH, DET_IN[0], self.anchors, rgb_param(), outs)
        finally:
            del outs, tin
        if len(res) == 0 or not res[0]:
            return None
        boxes = res[0]
        best = 0
        for i in range(1, len(boxes)):
            if boxes[i][2] * boxes[i][3] > boxes[best][2] * boxes[best][3]:
                best = i
        b = boxes[best]
        x = int(b[0])
        y = int(b[1]) - self.y_off
        w = int(b[2])
        h = int(b[3])
        if w <= 0 or h <= 0:
            return None
        return x, y, w, h

    def _liveness(self, img, box):
        """Score one face, or None when the crop cannot be built.

        Order follows the model contract: crop the expanded box, resize, then pad
        with mid grey. Padding before the crop would pad the whole frame instead.
        """
        crop = expand_box(box)
        if crop is None:
            return None
        cx, cy, cw, ch = crop
        top, bottom, left, right, _scale = pad_param(cw, ch, LIVE_IN, LIVE_IN)
        self.a2d_live.crop(cx, cy, cw, ch)
        self.a2d_live.resize(nn.interp_method.tf_bilinear,
                             nn.interp_mode.half_pixel)
        self.a2d_live.pad([0, 0, 0, 0, top, bottom, left, right], 0, PAD_GREY)
        self.a2d_live.build([1, 3, PREVIEW_H, PREVIEW_W],
                            [1, 3, LIVE_IN, LIVE_IN])

        tin = self.a2d_live.run(img.to_numpy_ref())
        self.kpu_live.set_input_tensor(0, tin)
        self.kpu_live.run()
        flat = None
        for i in range(self.kpu_live.outputs_size()):
            ot = self.kpu_live.get_output_tensor(i)
            try:
                arr = ot.to_numpy()
                if flat is None:
                    flat = arr.flatten()
            finally:
                ot.release()
        del tin
        if flat is None or len(flat) == 0:
            return None
        # The model emits one tensor of shape [1, 2], a two-class softmax whose
        # values sum to 1. **Index 1 is the live probability**: a live face measured
        # 0.9989-0.9997 there against 0.0001-0.0004 for a screen-replayed photo.
        # Index 0 is the spoof class, so reading it inverts every verdict.
        score = float(flat[1]) if len(flat) > 1 else float(flat[0])
        del flat
        return score

    # ---------- threshold overlay ----------
    def _open_picker(self):
        """Threshold presets. Idempotent: returns immediately if already open."""
        if self.picker is not None:
            return
        print("[Liveness] picker open")
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
        panel.set_size(560, 96 + len(THRESHOLDS) * 58)
        panel.align(lv.ALIGN.CENTER, 0, 0)
        panel.set_style_bg_color(lv.color_hex(0x101418), 0)
        panel.set_style_bg_opa(lv.OPA.COVER, 0)
        panel.set_style_border_width(2, 0)
        panel.set_style_border_color(lv.color_hex(COL_SEP), 0)
        panel.set_style_radius(14, 0)
        panel.set_style_pad_all(12, 0)
        panel.clear_flag(lv.obj.FLAG.SCROLLABLE)
        panel.add_flag(lv.obj.FLAG.CLICKABLE)

        title = lv.label(panel)
        title.set_text("Liveness threshold")
        title.set_style_text_color(lv.color_hex(0xE6EAF0), 0)
        title.align(lv.ALIGN.TOP_MID, 0, 2)

        hint = lv.label(panel)
        hint.set_text("tap a row to apply, tap outside to close")
        hint.set_style_text_color(lv.color_hex(0x8A97A3), 0)
        hint.align(lv.ALIGN.TOP_MID, 0, 24)

        font = _font("font_montserrat_16", "font_montserrat_14")
        y = 48
        for idx, (name, _conf) in enumerate(THRESHOLDS):
            row = lv.btn(panel)
            row.set_size(524, 50)
            row.align(lv.ALIGN.TOP_MID, 0, y)
            row.set_style_radius(10, 0)
            row.set_style_border_width(2 if idx == self.th_idx else 0, 0)
            row.set_style_border_color(lv.color_hex(0x00C853), 0)
            row.set_style_bg_color(lv.color_hex(0x1E262E), 0)
            row.set_style_bg_opa(lv.OPA.COVER, 0)
            row.set_style_pad_all(0, 0)
            row.clear_flag(lv.obj.FLAG.SCROLLABLE)
            row.add_flag(lv.obj.FLAG.CLICKABLE)
            lab = lv.label(row)
            mark = "> " if idx == self.th_idx else "  "
            lab.set_text("{}{}".format(mark, name))
            lab.set_style_text_color(
                lv.color_hex(0x00C853 if idx == self.th_idx else 0xE6EAF0), 0)
            if font is not None:
                lab.set_style_text_font(font, 0)
            lab.center()
            lab.clear_flag(lv.obj.FLAG.CLICKABLE)
            beep.on_click(row, lambda e, i=idx: self._pick(i))
            y += 58

        self.picker = backdrop

    def _pick(self, idx):
        self._close_picker()
        if idx != self.th_idx:
            self.th_idx = idx
            print("[Liveness] threshold -> {}".format(THRESHOLDS[idx][0]))
            # Re-score the current frame's verdict against the new threshold so the
            # display cannot contradict the bar until the next frame arrives.
            if self.score is not None:
                self.is_real = self.score >= THRESHOLDS[self.th_idx][1]
            self._update_stat()

    def _close_picker(self):
        """Close the overlay and unfreeze. Every close path must unfreeze."""
        if self.picker is not None:
            try:
                self.picker.delete()
            except BaseException as e:
                print("[Liveness] picker delete error:", e)
            self.picker = None
            print("[Liveness] picker closed")
        self.frozen = False
        self._update_stat()

    # ---------- drawing ----------
    def _draw_box(self, box):
        if box is None:
            self.box.add_flag(lv.obj.FLAG.HIDDEN)
            self.box_label.add_flag(lv.obj.FLAG.HIDDEN)
            return
        x, y, w, h = box
        if self.is_real is None:
            color_hex = COL_IDLE
            text = "measuring"
        elif self.is_real:
            color_hex = COL_REAL
            text = "REAL  {:.2f}".format(self.score)
        else:
            color_hex = COL_SPOOF
            text = "SPOOF  {:.2f}".format(self.score)
        color = lv.color_hex(color_hex)
        self.box.clear_flag(lv.obj.FLAG.HIDDEN)
        self.box.set_pos(x, y)
        self.box.set_size(w, h)
        self.box.set_style_border_color(color, 0)
        self.box_label.set_text(text)
        self.box_label.set_style_text_color(color, 0)
        self.box_label.set_style_bg_color(lv.color_hex(0x000000), 0)
        self.box_label.clear_flag(lv.obj.FLAG.HIDDEN)
        self.box_label.align_to(self.box, lv.ALIGN.TOP_LEFT, 0, label_dy(y))

    # ---------- main loop ----------
    def step(self):
        if self.sensor is None:
            return

        if self.picker is not None:
            return
        if self.frozen:
            return

        try:
            ai_img = self.sensor.snapshot(chn=CAM_CHN_ID_1)
            img = self.sensor.snapshot(chn=CAM_CHN_ID_2)
        except BaseException as error:
            self._show_error("snapshot error")
            self._no_signal.on_fail(self.ctx)
            return
        self._no_signal.on_ok()

        box = None
        try:
            box = self._detect(ai_img)
        except BaseException as error:
            print("[Liveness] detect error:", error)
            sys.print_exception(error)

        self.has_face = box is not None
        if box is None:
            self.score = None
            self.is_real = None
        else:
            try:
                score = self._liveness(ai_img, box)
            except BaseException as error:
                print("[Liveness] liveness error:", error)
                sys.print_exception(error)
                score = None
            self.score = score
            self.is_real = (None if score is None
                            else score >= THRESHOLDS[self.th_idx][1])

        try:
            img.as_lvgl_img_src(self.img_widget)
            self.img_widget.invalidate()
        except BaseException as error:
            self._show_error("display error")
            return

        self._draw_box(box)

        # Refresh the bar as soon as its text would change: a stale "no face" while
        # the box already says REAL reads as a bug.
        key = (self.has_face, None if self.score is None else int(self.score * 100),
               self.is_real)
        if key != self._shown:
            self._shown = key
            self._update_stat()

        # LVGL image sources and sensor frames are cyclic garbage; without a
        # periodic collect to_numpy starts failing every few dozen frames.
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

    def destroy(self):
        print("[Liveness] destroy begin")
        self._close_picker()
        self._release_models()
        if self.sensor is not None:
            for name in ("stop", "deinit"):
                try:
                    getattr(self.sensor, name)()
                    print("[Liveness]   sensor.{} ok".format(name))
                except BaseException as e:
                    print("[Liveness]   sensor.{} error: {}".format(name, e))
            self.sensor = None
        gc.collect()
        print("[Liveness] destroy done | mem_free={}".format(gc.mem_free()))


def create_app(content, ctx):
    return FaceLivenessApp(content, ctx)
