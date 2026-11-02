# Plate app: live licence-plate detection and recognition (Chinese plates).
#
# Two-stage chain, both models from the firmware image:
#   yolo_license_plate_det (640x640) -> quad -> crop -> licence_reco (220x32) ->
#   a 7-character plate string.
#
# Four measured facts drive the shape of this file (docs/board-ai-runtime.md):
#   1. The detection post-process ignores display_size and leaves the letterbox
#      pad in y, so every corner gets Y_OFF subtracted here. All five display
#      arguments tried produced identical output.
#   2. The detected quad must be passed through untouched. Collapsing it to an
#      axis-aligned rect, or forcing the plate's 3.14:1 aspect, moved the left
#      edge into background and turned 渝 into 豫/川/新 on 6 of 7 test frames,
#      while the raw quad decoded 6 of 7 correctly.
#   3. ocr_rec_preprocess returns a single-channel crop, so the recognition ai2d
#      is built for one channel.
#   4. A reading's province character can be wrong on one frame while every digit
#      is right, so the label is the majority of the last few readings rather than
#      one frame's result.
#
# Cost per frame on board: detection 68-72 ms, crop 4-11 ms, recognition 6-15 ms.
# Recognition runs on at most one plate per frame, round-robin, so several plates
# in view do not stretch the frame. Measured 9.7 fps with two plates in frame.
#
# Keys: CANCEL (GPIO0) exits (launcher default); OK (GPIO53) freezes/resumes. The
# top bar is one hit target and toggles the same freeze.

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
from apps.error import (AppUnavailable, CAMERA_UNAVAILABLE, NoSignalCounter,
                        close_sensor)
from camera_fov import set_preview

PREVIEW_W = 800               # capture size shared by preview and AI (= screen)
PREVIEW_H = 480
DET_MODEL = "/sdcard/examples/kmodel/yolo_license_plate_det.kmodel"
REC_MODEL = "/sdcard/examples/kmodel/licence_reco.kmodel"
DET_IN = [640, 640]
REC_IN = [220, 32]

DET_CONF = 0.2
DET_NMS = 0.2
DET_MAX = 10
MAX_PLATES = 4                # overlay pool size, also the recognitions on screen

# The post-process reports y in the letterboxed model space without removing the
# top pad, so y has to be corrected by pad_top * preview_w / model_w. For the
# measured 800x480 into 640x640 this is always 160, but it is computed from the
# formula so a different preview size stays correct.
_y_scale = min(float(DET_IN[0]) / PREVIEW_W, float(DET_IN[1]) / PREVIEW_H)
PAD_TOP = int((DET_IN[1] - int(PREVIEW_H * _y_scale)) / 2)
Y_OFF = int(PAD_TOP * PREVIEW_W / DET_IN[0])

# A Chinese plate is 440x140, i.e. 3.14:1. Measured, this ratio must NOT be used
# to widen the crop: growing the box pushed the left edge into background and the
# province character flipped to a wrong one (渝 -> 豫 on 6 of 7 test frames), while
# the detected quad decoded 6 of 7 correctly. The detector under-reads width on a
# very large plate, but widening costs more than it buys, so the quad is used as
# detected and stability comes from time-domain voting instead.
QUAD_POINTS = 5               # four corners plus the closing point

# Geometry gate on a detection. A Chinese plate is 3.14:1, so anything far from
# that is background clutter: measured on a cluttered scene, the low 0.2
# confidence threshold also passes 144x48 and 151x61 boxes that decode as random
# strings. The aspect band is deliberately wide because a plate seen at an angle
# foreshortens, and the height floor drops boxes too small to hold 7 glyphs.
MIN_ASPECT = 2.0
MAX_ASPECT = 4.6
MIN_PLATE_H = 34

# One recognition per frame per plate, and a plate's label is the majority of the
# last VOTE_WINDOW readings. A single frame can decode a wrong province character
# (measured: 渝 -> 川 / 浙 / 新 under blur and low resolution) while the digits stay
# right, so voting on the whole string fixes the only unstable part.
VOTE_WINDOW = 5
VOTE_MIN = 3                  # readings a string needs before it is displayed
REC_PERIOD_MS = 0             # 0 = every frame; recognition measured 6-15 ms
MIN_TEXT_LEN = 2              # blank noise decodes as one stray character
# A plate that moved this far is a different plate, so its history is dropped
# rather than voted together with the previous one.
MOVE_RESET_PX = 96

TOPBAR_H = 56
TOPBAR_Y = 10
TOPBAR_LEFT = 8
TOPBAR_RIGHT = 8
SEG_PAD = 18

COL_TITLE = 0x00E5A0          # teal: app name
COL_FPS = 0xFFC857            # amber: frame rate
COL_COUNT = 0x64B5F6          # sky blue: plate count when non-zero
COL_COUNT_IDLE = 0x6B7885     # grey: demoted count so "0 plate" is not loud
COL_SEP = 0x3A4650
COL_HINT = 0x7A8794
COL_FROZEN = 0xFF8A65
COL_ERROR = 0xFF6B6B
COL_BOX = 0x5CE8C0            # plate outline: same mint as the pose joint dots
COL_TEXT = 0x4CD964           # recognised string: green, read at arm's length

BOX_LINE_W = 3
# The 30px font reports a 33px line height (measured), plus 1px pad top and
# bottom, plus the 3px gap to the plate: 38 keeps the caption fully on screen
# even at the bottom edge.
CAPTION_H = 38
CAPTION_PAD = 8

FONT_FILE = "plate_cjk_30.bin"

# Character table of licence_reco, in model output order. Index 0 is the CTC
# blank, which is why every lookup is index - 1.
DICT = ["挂", "使", "领", "澳", "港", "皖", "沪", "津", "渝", "冀",
        "晋", "蒙", "辽", "吉", "黑", "苏", "浙", "京", "闽", "赣",
        "鲁", "豫", "鄂", "湘", "粤", "桂", "琼", "川", "贵", "云",
        "藏", "陕", "甘", "青", "宁", "新", "警", "学",
        "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
        "A", "B", "C", "D", "E", "F", "G", "H", "J", "K",
        "L", "M", "N", "P", "Q", "R", "S", "T", "U", "V",
        "W", "X", "Y", "Z", "_", "-"]
DICT_SIZE = len(DICT)

MODEL_MISSING = ("Plate models missing on SD card.\n"
                 "Expected:\n"
                 "  " + DET_MODEL + "\n"
                 "  " + REC_MODEL)


def _font(*names):
    """Return the first built-in font that exists among the candidate names."""
    for name in names:
        font = getattr(lv, name, None)
        if font:
            return font
    return None


def _assets_dir():
    """Asset directory derived from this module's __file__ (gui/ is above apps/)."""
    path = globals().get("__file__")
    if path:
        try:
            base = path.rsplit("/", 1)[0]        # .../gui/apps
            return base.rsplit("/", 1)[0] + "/assets"
        except BaseException:
            pass
    return "/sdcard/factory/gui/assets"


_PLATE_FONT = None
_PLATE_FONT_TRIED = False


def _plate_font():
    """Font that carries the plate's Chinese province characters.

    Nunito and the built-in montserrat have no CJK glyphs, so a decoded plate
    would render as boxes. The baked subset holds only the 38 characters the
    dictionary can emit. font_load needs the "A:" drive prefix; without it the
    call returns None and the label silently falls back to montserrat.
    """
    global _PLATE_FONT, _PLATE_FONT_TRIED
    if _PLATE_FONT_TRIED:
        return _PLATE_FONT
    _PLATE_FONT_TRIED = True
    load = getattr(lv, "font_load", None)
    if load is not None:
        path = "A:" + _assets_dir() + "/fonts/" + FONT_FILE
        try:
            font = load(path)
            if font is not None:
                _PLATE_FONT = font
                return _PLATE_FONT
            print("[Plate] font load returned None: {}".format(path))
        except BaseException as error:
            print("[Plate] font load failed: {} | {}".format(path, error))
    _PLATE_FONT = _font("font_montserrat_20", "font_montserrat_16")
    print("[Plate] cjk font missing, falling back to montserrat")
    return _PLATE_FONT

def _seq_len(obj):
    """len() of a sequence, or -1 for a scalar.

    hasattr(obj, "__len__") is not usable: ulab ndarrays answer len() without
    exposing the attribute, so every array looked like a scalar (measured).
    """
    try:
        return len(obj)
    except BaseException:
        return -1


class PlateApp:
    def __init__(self, content, ctx):
        self.ctx = ctx
        self.content = content
        self.sensor = None
        self.kpu_det = None
        self.kpu_rec = None
        self.ai2d_det = None
        # Recognition pre-processing depends on the crop size, which changes with
        # the plate on screen. Rebuilding costs about 1 ms, so it only happens
        # when that size actually changes instead of once per frame.
        self.ai2d_rec = None
        self._rec_shape = None
        self.frozen = False
        self.frame_count = 0
        self.last_fps_ms = time.ticks_ms()
        self.fps_text = "-"
        self.plate_count = 0
        self.infer_ms = 0
        self._gc_tick = 0
        self._print_ms = 0
        self._no_signal = NoSignalCounter()
        # Round-robin state: which detected plate gets recognised this frame, and
        # the last time each slot was read.
        self._rec_cursor = 0
        self._rec_ms = [0] * MAX_PLATES
        # Per-slot vote history: the last VOTE_WINDOW readings. The displayed text
        # is their majority, so one bad frame cannot change the label.
        self._hist = [[] for _ in range(MAX_PLATES)]
        self._texts = [""] * MAX_PLATES
        self._last_count = 0
        # Centre of each slot's box last frame, used to notice that a different
        # plate took the slot (the vote history then has to be dropped).
        self._centres = [(0, 0)] * MAX_PLATES

        self.img_w = ctx.width
        self.img_h = ctx.height

        # ---- preview ----
        self.img_widget = lv.img(content)
        self.img_widget.set_size(self.img_w, self.img_h)
        self.img_widget.align(lv.ALIGN.CENTER, 0, 0)

        # ---- overlay pool: one quad outline plus one caption per plate ----
        # A plate is rarely axis aligned in frame, so the outline is drawn as a
        # closed polyline through the four detected corners; a rectangular widget
        # would cover background and under-report the plate's extent.
        self.boxes = []
        self.captions = []
        caption_font = _plate_font()
        for _ in range(MAX_PLATES):
            outline = lv.line(content)
            outline.set_points([{"x": 0, "y": 0}] * QUAD_POINTS, QUAD_POINTS)
            outline.set_style_line_width(BOX_LINE_W, 0)
            outline.set_style_line_color(lv.color_hex(COL_BOX), 0)
            outline.set_style_line_opa(lv.OPA.COVER, 0)
            try:
                outline.set_style_line_rounded(True, 0)
            except BaseException:
                pass
            outline.add_flag(lv.obj.FLAG.HIDDEN)
            self.boxes.append(outline)

            caption = lv.label(content)
            caption.set_text("")
            caption.set_style_text_color(lv.color_hex(COL_TEXT), 0)
            caption.set_style_bg_color(lv.color_hex(0x000000), 0)
            caption.set_style_bg_opa(lv.OPA._70, 0)
            caption.set_style_pad_hor(CAPTION_PAD, 0)
            caption.set_style_pad_ver(1, 0)
            caption.set_style_radius(4, 0)
            if caption_font is not None:
                caption.set_style_text_font(caption_font, 0)
            caption.add_flag(lv.obj.FLAG.HIDDEN)
            self.captions.append(caption)

        # ---- top bar: the whole strip is one click target (freeze toggle) ----
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
            if bar_font is not None:
                lab.set_style_text_font(bar_font, 0)
            # clicks must reach the strip, otherwise taps on the padding are lost
            lab.clear_flag(lv.obj.FLAG.CLICKABLE)
            return lab

        self.stat_title = _seg("PLATE", COL_TITLE)
        self.stat_sep1 = _seg("|", COL_SEP)
        self.stat_fps = _seg("FPS -", COL_FPS)
        self.stat_sep2 = _seg("|", COL_SEP)
        self.stat_count = _seg("0 plate", COL_COUNT_IDLE)
        self.stat_hint = _seg("TAP freeze", COL_HINT)

        self._init_ai()

    # ---------------- AI pipeline ----------------
    def _init_ai(self):
        t0 = time.ticks_ms()
        try:
            if (not self._exists(DET_MODEL) or not self._exists(REC_MODEL)):
                raise AppUnavailable(MODEL_MISSING)
        except AppUnavailable:
            raise
        except BaseException as error:
            print("[Plate] file check error:", error)

        sensor = None
        try:
            sensor = Sensor(fps=30)
            sensor.reset()
            # chn1 = AI input, planar RGBP888 because ai2d declares NCHW_FMT.
            # Its to_numpy_ref() is already (3, H, W), which is what
            # ocr_rec_preprocess wants, so no conversion happens per frame.
            set_preview(sensor, CAM_CHN_ID_1, PREVIEW_W, PREVIEW_H)
            sensor.set_pixformat(Sensor.RGBP888, chn=CAM_CHN_ID_1)
            # chn2 = preview; as_lvgl_img_src only accepts a packed format.
            set_preview(sensor, CAM_CHN_ID_2, PREVIEW_W, PREVIEW_H)
            sensor.set_pixformat(Sensor.RGB888, chn=CAM_CHN_ID_2)
            sensor.run()
        except BaseException as error:
            print("[Plate] sensor init failed: {}".format(error))
            sys.print_exception(error)
            close_sensor(sensor)
            raise AppUnavailable(CAMERA_UNAVAILABLE)
        self.sensor = sensor

        try:
            self._load_models()
        except AppUnavailable:
            raise
        except BaseException as error:
            print("[Plate] model load failed: {}".format(error))
            sys.print_exception(error)
            self._release_models()
            close_sensor(self.sensor)
            self.sensor = None
            raise AppUnavailable(MODEL_MISSING)

        print("[Plate] ready: load={}ms y_off={} mem_free={}".format(
            time.ticks_diff(time.ticks_ms(), t0), Y_OFF, gc.mem_free()))
        self._update_stat()

    @staticmethod
    def _exists(path):
        try:
            return os.stat(path)[6] > 0
        except BaseException:
            return False

    def _load_models(self):
        mw, mh = DET_IN
        kpu = nn.kpu()
        kpu.load_kmodel(DET_MODEL)
        self.kpu_det = kpu

        kpu = nn.kpu()
        kpu.load_kmodel(REC_MODEL)
        self.kpu_rec = kpu

        a2d = Ai2d()
        a2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                           np.uint8, np.uint8)
        # pad acts in output space, so pad_top is the band inside the 640 model
        a2d.pad([0, 0, 0, 0, PAD_TOP, PAD_TOP, 0, 0], 0, [128, 128, 128])
        a2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
        a2d.build([1, 3, PREVIEW_H, PREVIEW_W], [1, 3, mh, mw])
        self.ai2d_det = a2d

        print("[Plate] models loaded pad_top={} y_off={} mem_free={}".format(
            PAD_TOP, Y_OFF, gc.mem_free()))

    def _release_models(self):
        for name in ("kpu_det", "kpu_rec"):
            kpu = getattr(self, name)
            if kpu is None:
                continue
            try:
                kpu.release()
            except BaseException as error:
                print("[Plate]   {}.release error: {}".format(name, error))
            setattr(self, name, None)
        if self.ai2d_det is not None:
            try:
                self.ai2d_det.deinit()
            except BaseException as error:
                print("[Plate]   ai2d.deinit error: {}".format(error))
            self.ai2d_det = None
        if self.ai2d_rec is not None:
            try:
                self.ai2d_rec.deinit()
            except BaseException as error:
                print("[Plate]   rec ai2d.deinit error: {}".format(error))
            self.ai2d_rec = None
        self._rec_shape = None
        gc.collect()

    def _detect(self, ai_img):
        """One frame through the detector. Returns a list of quads (8 floats each).

        The returned y is already corrected by Y_OFF, so callers never see the
        letterbox pad.
        """
        tin = self.ai2d_det.run(ai_img.to_numpy_ref())
        self.kpu_det.set_input_tensor(0, tin)
        self.kpu_det.run()
        ot = self.kpu_det.get_output_tensor(0)
        try:
            raw = ot.to_numpy()
        finally:
            ot.release()            # native tensors must be released
        del tin

        # (1, 13, 8400): transposing puts anchors on axis 0 as the post-process
        # expects. A view is not enough, aidemo needs contiguous memory.
        grid = raw[0].transpose().copy()
        del raw
        res = aidemo.yolo_license_plate_det_postprocess(
            grid, [PREVIEW_H, PREVIEW_W], [DET_IN[1], DET_IN[0]],
            [PREVIEW_H, PREVIEW_W], DET_CONF, DET_NMS, DET_MAX)
        del grid

        quads = []
        src = res[0]
        n = _seq_len(src)
        for i in range(n if n < MAX_PLATES else MAX_PLATES):
            quad = self._correct_quad(src[i])
            if self._plausible(quad):
                quads.append(quad)
        del res
        return quads

    @staticmethod
    def _plausible(quad):
        """Whether a detected quad has a plate's proportions and size.

        The confidence threshold has to stay low (0.2) to catch a distant plate,
        which also lets background edges through: measured on a cluttered scene,
        four boxes appeared and three of them were 144x48 or 151x61 clutter that
        decoded as random strings. Filtering on geometry removes them without
        costing a real plate, because every true detection measured 2.2-3.9 : 1.
        """
        xs = [quad[0], quad[2], quad[4], quad[6]]
        ys = [quad[1], quad[3], quad[5], quad[7]]
        w = max(xs) - min(xs)
        h = max(ys) - min(ys)
        if h < MIN_PLATE_H or w < 1.0:
            return False
        aspect = w / h
        return MIN_ASPECT <= aspect <= MAX_ASPECT

    def _correct_quad(self, q):
        """Detected quad with the letterbox pad removed, rotation preserved.

        The post-process leaves pad_top in y, so every corner loses Y_OFF. Nothing
        else is done: the four corners stay a rotated quad because the recogniser
        rectifies the plate itself, and any axis-aligning or aspect-forcing step
        measured worse than passing the corners through (see QUAD_POINTS comment).
        Corners are clamped to the frame because the crop routine requires all
        four inside it.
        """
        points = []
        for k in range(4):
            x = float(q[k * 2])
            y = float(q[k * 2 + 1]) - Y_OFF
            if x < 0.0:
                x = 0.0
            elif x > PREVIEW_W - 1:
                x = PREVIEW_W - 1.0
            if y < 0.0:
                y = 0.0
            elif y > PREVIEW_H - 1:
                y = PREVIEW_H - 1.0
            points.append(x)
            points.append(y)
        return np.array(points)

    def _build_rec_ai2d(self, h, w):
        """(Re)build the recognition pre-process for a crop of h x w.

        The crop is single channel, so the build declares one input channel. A
        three-channel build is accepted by the runtime but only one plane holds
        data; keeping the declaration honest makes a future crop format change
        fail loudly instead of silently reading adjacent memory.
        """
        if self.ai2d_rec is not None:
            try:
                self.ai2d_rec.deinit()
            except BaseException as error:
                print("[Plate]   rec ai2d.deinit error: {}".format(error))
            self.ai2d_rec = None
            self._rec_shape = None
        a2d = Ai2d()
        a2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                           np.uint8, np.uint8)
        a2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
        a2d.build([1, 1, h, w], [1, 3, REC_IN[1], REC_IN[0]])
        self.ai2d_rec = a2d
        self._rec_shape = (h, w)

    def _recognize(self, chw, quad):
        """Crop one plate and decode it. Returns the string, or None on failure.

        The crop comes back single channel, so the recognition ai2d is built for
        one channel; output is (steps, 1, 74) and index 0 is the CTC blank.
        """
        crops = aidemo.ocr_rec_preprocess(chw, [PREVIEW_H, PREVIEW_W], [quad])
        imgs = crops[0]
        if _seq_len(imgs) <= 0:
            return None
        crop = imgs[0]
        h = len(crop[0][0])
        w = len(crop[0][0][0])
        del crops, imgs

        if self._rec_shape != (h, w) or self.ai2d_rec is None:
            self._build_rec_ai2d(h, w)

        tin = self.ai2d_rec.run(crop)
        self.kpu_rec.set_input_tensor(0, tin)
        self.kpu_rec.run()

        ot = self.kpu_rec.get_output_tensor(0)
        try:
            out = ot.to_numpy()
        finally:
            ot.release()

        flat = out.reshape((-1, DICT_SIZE))
        n = len(flat)
        idx = np.argmax(flat, axis=1)
        text = ""
        prev = -1
        for i in range(n):
            v = int(idx[i])
            if v > 0 and v != prev:
                text += DICT[v - 1] if v - 1 < DICT_SIZE else ""
            prev = v
        del flat, idx
        return text

    # ---------------- drawing ----------------
    def _show_plates(self, quads):
        n = len(quads)
        for i in range(MAX_PLATES):
            if i >= n:
                self.boxes[i].add_flag(lv.obj.FLAG.HIDDEN)
                self.captions[i].add_flag(lv.obj.FLAG.HIDDEN)
                continue
            q = quads[i]
            # Closed polyline through the four corners, so a rotated plate gets a
            # matching outline instead of an over-large bounding box.
            pts = []
            min_x = PREVIEW_W
            max_y = 0
            for k in range(4):
                x = int(q[k * 2])
                y = int(q[k * 2 + 1])
                pts.append({"x": x, "y": y})
                if x < min_x:
                    min_x = x
                if y > max_y:
                    max_y = y
            pts.append(pts[0])

            box = self.boxes[i]
            box.set_points(pts, QUAD_POINTS)
            box.clear_flag(lv.obj.FLAG.HIDDEN)

            caption = self.captions[i]
            text = self._texts[i]
            if not text:
                caption.add_flag(lv.obj.FLAG.HIDDEN)
                continue
            caption.set_text(text)
            caption.clear_flag(lv.obj.FLAG.HIDDEN)
            # below the plate when it fits, otherwise just inside its bottom edge
            cy = max_y + 3
            if cy + CAPTION_H > self.img_h:
                cy = max_y - CAPTION_H - 3
            if cy < 0:
                cy = 0
            caption.set_pos(min_x, cy)

    def _update_stat(self):
        self.stat_fps.set_text("FPS {}".format(self.fps_text))
        if self.plate_count > 0:
            self.stat_count.set_text("{} plate".format(self.plate_count))
            self.stat_count.set_style_text_color(lv.color_hex(COL_COUNT), 0)
        else:
            self.stat_count.set_text("0 plate")
            self.stat_count.set_style_text_color(
                lv.color_hex(COL_COUNT_IDLE), 0)
        if self.frozen:
            self.stat_hint.set_text("FROZEN")
            self.stat_hint.set_style_text_color(lv.color_hex(COL_FROZEN), 0)
        else:
            self.stat_hint.set_text("TAP freeze")
            self.stat_hint.set_style_text_color(lv.color_hex(COL_HINT), 0)

    def _show_error(self, text):
        self.stat_hint.set_text(text)
        self.stat_hint.set_style_text_color(lv.color_hex(COL_ERROR), 0)

    def _toggle_freeze(self):
        self.frozen = not self.frozen
        self._update_stat()

    # ---------------- frame ----------------
    def step(self):
        if self.sensor is None:
            return

        # OK is handled before the frozen check, otherwise a freeze entered with
        # OK could never be left with OK.
        if self.ctx.take_ok():
            self._toggle_freeze()

        if self.frozen:
            return

        try:
            ai_img = self.sensor.snapshot(chn=CAM_CHN_ID_1)
            img = self.sensor.snapshot(chn=CAM_CHN_ID_2)
        except BaseException as error:
            print("[Plate] snapshot error:", error)
            self._show_error("snapshot error")
            self._no_signal.on_fail(self.ctx)
            return
        self._no_signal.on_ok()

        t0 = time.ticks_ms()
        quads = []
        try:
            quads = self._detect(ai_img)
        except BaseException as error:
            print("[Plate] detect error:", error)
            sys.print_exception(error)

        # preview always updates, even when detection failed
        try:
            img.as_lvgl_img_src(self.img_widget)
            self.img_widget.invalidate()
        except BaseException:
            self._show_error("display error")
            return

        # ---- recognition: one plate per frame, round-robin ----
        # Slots are matched by index, so a change in count means the on-screen
        # plates were re-ordered or replaced: drop the old strings rather than
        # paint the previous car's number onto the next one.
        if len(quads) != self._last_count:
            self._last_count = len(quads)
            self._reset_votes()

        # A slot whose box jumped is a different plate, so its history is not a
        # valid sample of the same string any more.
        for i in range(len(quads)):
            q = quads[i]
            cx = int((q[0] + q[2] + q[4] + q[6]) * 0.25)
            cy = int((q[1] + q[3] + q[5] + q[7]) * 0.25)
            ox, oy = self._centres[i]
            if abs(cx - ox) > MOVE_RESET_PX or abs(cy - oy) > MOVE_RESET_PX:
                self._hist[i] = []
                self._texts[i] = ""
            self._centres[i] = (cx, cy)

        now = time.ticks_ms()
        if quads:
            slot = self._rec_cursor % len(quads)
            self._rec_cursor += 1
            waited = time.ticks_diff(now, self._rec_ms[slot])
            if waited >= REC_PERIOD_MS:
                self._rec_ms[slot] = now
                try:
                    chw = ai_img.to_numpy_ref()
                    text = self._recognize(chw, quads[slot])
                    del chw
                except BaseException as error:
                    print("[Plate] recognize error:", error)
                    sys.print_exception(error)
                    text = None
                if text is not None:
                    self._vote(slot, text)

        self.plate_count = len(quads)
        self._show_plates(quads)
        self._print_state(quads)

        self.infer_ms = time.ticks_diff(time.ticks_ms(), t0)
        del quads

        # LVGL image sources and sensor frames are reference cycles that never
        # get freed by refcounting; without a periodic collect the contiguous
        # heap fills and to_numpy starts failing.
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

    def _reset_votes(self):
        """Forget every reading; used when the plate count changed."""
        self._hist = [[] for _ in range(MAX_PLATES)]
        self._texts = [""] * MAX_PLATES
        self._rec_ms = [0] * MAX_PLATES
        self._centres = [(0, 0)] * MAX_PLATES

    def _vote(self, slot, text):
        """Add a reading to the slot's history and show the majority.

        The province character is the only unstable part of a reading: with a
        plate a little far away, the model returns 川 / 浙 / 新 where 渝 was
        intended, while every digit stays correct. Requiring a majority over the
        last VOTE_WINDOW frames turns those into a minority that never reaches
        the label, without delaying a correct reading by more than a few frames.

        A string shorter than MIN_TEXT_LEN is blank noise and is not recorded: it
        would otherwise be a vote that no other reading can equal.
        """
        if len(text) < MIN_TEXT_LEN:
            return
        hist = self._hist[slot]
        hist.append(text)
        if len(hist) > VOTE_WINDOW:
            del hist[0]

        # Majority without a Counter (MicroPython has none); VOTE_WINDOW is 5, so
        # the double loop is 25 comparisons at most.
        best = ""
        best_n = 0
        for candidate in hist:
            n = 0
            for other in hist:
                if other == candidate:
                    n += 1
            if n > best_n:
                best_n = n
                best = candidate

        # VOTE_MIN keeps a single reading from painting the label, and re-reading
        # the same string must not reset a vote that is already settled.
        if best_n >= VOTE_MIN and self._texts[slot] != best:
            self._texts[slot] = best

    def _print_state(self, quads):
        """Serial line, throttled: what is on screen, so a run can be checked."""
        now = time.ticks_ms()
        if time.ticks_diff(now, self._print_ms) < 500:
            return
        self._print_ms = now
        if not quads:
            return
        parts = []
        for i in range(len(quads)):
            q = quads[i]
            parts.append("{}[{},{}]{}x{}".format(
                self._texts[i] or "?", int(q[0]), int(q[1]),
                int(q[2]) - int(q[0]), int(q[5]) - int(q[1])))
        print("[Plate] {} | infer {}ms | mem_free {}".format(
            " | ".join(parts), self.infer_ms, gc.mem_free()))

    def destroy(self):
        print("[Plate] destroy begin")
        self._release_models()
        if self.sensor is not None:
            for name in ("stop", "deinit"):
                try:
                    getattr(self.sensor, name)()
                except BaseException as error:
                    print("[Plate]   sensor.{} error: {}".format(name, error))
            self.sensor = None
        gc.collect()
        print("[Plate] destroy done | mem_free={}".format(gc.mem_free()))


def create_app(content, ctx):
    return PlateApp(content, ctx)
