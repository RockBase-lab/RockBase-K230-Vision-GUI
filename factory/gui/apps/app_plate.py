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
#  4. A reading can be wrong in one character while the rest is right, so the label
#     needs several agreeing readings and a box that stops moving between them.
#
# Cost per iteration on board: detection 68 ms, LVGL repaint 22 ms, recognition
# 6 ms. Detection is 67% of that and does not shrink with the plate, so it runs
# every DETECT_EVERY iterations while the preview keeps every one. Measured 14.3
# fps preview with the plate reading at about 7 per second.
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
from core import paths
from core import i18n
from core.errors import (AppUnavailable, CAMERA_UNAVAILABLE, NoSignalCounter,
                        close_sensor)
from hardware.camera_fov import set_preview
from core.model_pool import get as model_get

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

# Detection gate. The post-process returns one score per box (res[2], measured
# 0.74-0.89 on real plates), which is the reliable discriminator: a plate 61x20 in
# frame scored 0.885, higher than a near plate at 0.736.
#
# A height floor was used first and had to be withdrawn: it dropped that 61x20 plate
# entirely (nothing on screen, while the box was correct and the string decoded),
# and it never rejected any of the clutter boxes measured, because those were 144x48
# and 151x61, i.e. taller than the floor.
DET_SCORE_MIN = 0.35
MIN_PLATE_H = 12              # only rejects degenerate boxes, not distant plates

# Geometry band. A plate is 3.14:1, but it foreshortens at an angle, so the band is
# wide. Measured: every accepted real plate sat at 2.2-3.9.
MIN_ASPECT = 2.0
MAX_ASPECT = 4.6

# A decoded string must look like a plate. The province slot is a closed set, so a
# random reading such as "HA188E" (H is not a province) is rejected outright; the
# length band admits both the 7-character standard and the 8-character new-energy
# format.
PLATE_MIN_LEN = 6
PLATE_MAX_LEN = 8

# A reading is only shown after STREAK_N consecutive identical readings. A plate far
# away puts about 8x14 pixels behind each character, so a single frame is often
# wrong in a character or two (measured: 14 distinct readings over 192 frames of one
# plate), but the correct string is the most frequent and arrives in runs. Counting
# a streak rejects the scattered wrong readings that a 5-frame majority let through.
#
# STREAK_SWITCH is a hysteresis band: a label that is already on screen is only
# replaced by a different run of this length. Without it, two readings that both
# reach STREAK_N (measured: the true string at 77% and a one-character variant at
# 18%, both in runs) make the caption flip back and forth every second or so.
STREAK_N = 4
STREAK_SWITCH = 8

# Detection runs every DETECT_EVERY iterations, the preview every iteration.
#
# Measured per-iteration cost is detection 68 ms, LVGL task 22 ms, recognition
# 6 ms, the rest ~2.5 ms. Detection is a 640x640 inference that does not depend on
# what the loop does around it, so running it half as often is the only lever that
# raises the preview rate:
#
#   detect every frame     100.5 ms/iter   9.9 fps
#   detect every 2nd        69.1 ms/iter  14.5 fps
#   no detection at all     35.6 ms/iter  28.1 fps
#
# The plate reading halves to about 7 per second, which still settles a label in
# under a second at STREAK_N = 4, and the overlay holds its last box between
# detections. The preview is what the eye watches, so it gets the extra frames.
DETECT_EVERY = 2

# Detector boxes jitter by a pixel or two between frames, and the recogniser is
# sensitive to that: the same plate cropped from a 1 px shifted box measured a
# different second character. The box a slot uses is therefore an exponential moving
# average of what the detector reports, so a static plate converges on one crop and
# the reading becomes reproducible. BOX_EMA is the weight of the newest box; 0.35
# settles in about three frames, which is faster than STREAK_N needs.
BOX_EMA = 0.35
REC_PERIOD_MS = 0             # 0 = every frame; recognition measured 6-15 ms
MIN_TEXT_LEN = 2              # crude floor; _valid_text is the real check
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

# Every character the recogniser can emit in the province slot: the dictionary's
# first 38 entries, which include the special prefixes (挂 / 使 / 领 / 澳 / 港).
PROVINCE_CHARS = "".join(DICT[:38])

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
        path = "A:" + paths.asset_path("fonts", FONT_FILE)
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
        # Which iteration this is; detection runs when (_tick % DETECT_EVERY) == 0.
        self._tick = 0
        # Per-slot reading state: the last raw string and how many frames in a row
        # produced it. The label is set when that streak is long enough.
        self._streak_text = [""] * MAX_PLATES
        self._streak_n = [0] * MAX_PLATES
        self._texts = [""] * MAX_PLATES
        self._last_count = 0
        # Centre of each slot's box last frame, used to notice that a different
        # plate took the slot (the reading history then has to be dropped).
        self._centres = [(0, 0)] * MAX_PLATES
        # Smoothed box per slot: the geometry actually cropped and drawn.
        self._box_ema = [None] * MAX_PLATES

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
            f = i18n.font_for(text, bar_font)
            if f is not None:
                lab.set_style_text_font(f, 0)
            # clicks must reach the strip, otherwise taps on the padding are lost
            lab.clear_flag(lv.obj.FLAG.CLICKABLE)
            return lab

        self.stat_title = _seg("PLATE", COL_TITLE)
        self.stat_sep1 = _seg("|", COL_SEP)
        self.stat_fps = _seg("FPS -", COL_FPS)
        self.stat_sep2 = _seg("|", COL_SEP)
        self.stat_count = _seg(i18n.t("ai.no_plates"), COL_COUNT_IDLE)
        self.stat_hint = _seg(i18n.t("ai.tap_freeze_short"), COL_HINT)

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
        # Both are pooled (boot preloads them), so these are lookups.
        self.kpu_det = model_get(DET_MODEL)
        self.kpu_rec = model_get(REC_MODEL)

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
        # The kpus belong to the shared pool; only the ai2d instances are per-app.
        self.kpu_det = None
        self.kpu_rec = None
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

        # The post-process returns (quads, rects, scores). The score is what makes
        # a distant plate usable: the 640 model localises a 61x20 plate with 0.885
        # confidence, so filtering on size instead threw it away.
        src = res[0]
        scores = res[2]
        n = _seq_len(src)
        candidates = []
        for i in range(n):
            try:
                score = float(scores[i])
            except BaseException:
                score = 1.0
            if score < DET_SCORE_MIN:
                continue
            quad = self._correct_quad(src[i])
            if self._plausible(quad):
                candidates.append((score, quad))
        del res

        # Highest confidence first, so a real plate wins a slot over background
        # clutter when there are more candidates than slots. Insertion sort: at
        # most DET_MAX (10) entries, and MicroPython's sort does not take a key.
        for a in range(1, len(candidates)):
            current = candidates[a]
            b = a - 1
            while b >= 0 and candidates[b][0] < current[0]:
                candidates[b + 1] = candidates[b]
                b -= 1
            candidates[b + 1] = current

        quads = []
        for i in range(len(candidates) if len(candidates) < MAX_PLATES else MAX_PLATES):
            quads.append(candidates[i][1])
        del candidates
        return quads

    @staticmethod
    def _plausible(quad):
        """Whether a detected quad has a plate's proportions.

        The height floor only rejects degenerate boxes. It is deliberately small:
        a plate at 61x20 in frame is a normal reading distance, and rejecting it was
        the cause of "clearly visible but not recognised". Clutter is handled by the
        detection score and by the decoded-string format check instead, because
        clutter boxes measured larger (144x48, 151x61) than that plate.
        """
        xs = [quad[0], quad[2], quad[4], quad[6]]
        ys = [quad[1], quad[3], quad[5], quad[7]]
        w = max(xs) - min(xs)
        h = max(ys) - min(ys)
        if h < MIN_PLATE_H or w < 1.0:
            return False
        aspect = w / h
        return MIN_ASPECT <= aspect <= MAX_ASPECT

    @staticmethod
    def _valid_text(text):
        """Whether a decoded string has a plate's shape.

        The province slot is a closed set of 38 characters, so a reading whose first
        character is not one of them (measured noise: "HA188E") cannot be a plate
        and is dropped before it can enter a vote. The length band admits the
        7-character standard and the 8-character new-energy format.
        """
        if not (PLATE_MIN_LEN <= len(text) <= PLATE_MAX_LEN):
            return False
        return text[0] in PROVINCE_CHARS

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
            self.stat_count.set_text(i18n.t("ai.plates").format(self.plate_count))
            self.stat_count.set_style_text_color(lv.color_hex(COL_COUNT), 0)
        else:
            self.stat_count.set_text(i18n.t("ai.no_plates"))
            self.stat_count.set_style_text_color(
                lv.color_hex(COL_COUNT_IDLE), 0)
        if self.frozen:
            self.stat_hint.set_text(i18n.t("ai.frozen"))
            self.stat_hint.set_style_text_color(lv.color_hex(COL_FROZEN), 0)
        else:
            self.stat_hint.set_text(i18n.t("ai.tap_freeze_short"))
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

        # The preview frame is grabbed every iteration; the AI frame only on a
        # detection iteration, because its snapshot is pure overhead otherwise.
        try:
            img = self.sensor.snapshot(chn=CAM_CHN_ID_2)
            due = (self._tick % DETECT_EVERY) == 0
            ai_img = self.sensor.snapshot(chn=CAM_CHN_ID_1) if due else None
        except BaseException as error:
            print("[Plate] snapshot error:", error)
            self._show_error(i18n.t("ai.snapshot_error"))
            self._no_signal.on_fail(self.ctx)
            self._tick += 1
            return
        self._no_signal.on_ok()
        self._tick += 1

        quads = None
        if due:
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
            self._show_error(i18n.t("ai.display_error"))
            return

        if due:
            self._run_detection_cycle(ai_img, quads)

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

    def _run_detection_cycle(self, ai_img, quads):
        """Everything that only has to happen on a detection iteration.

        Kept out of step() so the preview path stays short: this is the part that
        costs 68 ms (inference) plus 6 ms (recognition), and the overlay update.
        """
        t0 = time.ticks_ms()

        # Slots are matched by index, so a change in count means the on-screen
        # plates were re-ordered or replaced: drop the old strings rather than
        # paint the previous car's number onto the next one.
        if len(quads) != self._last_count:
            self._last_count = len(quads)
            self._reset_votes()

        # A slot whose box jumped is a different plate, so its reading history is
        # not a valid sample of the same string any more. Otherwise the box is
        # smoothed, because a static plate's crop has to stop moving for the
        # recogniser to return the same string frame after frame.
        for i in range(len(quads)):
            q = quads[i]
            cx = int((q[0] + q[2] + q[4] + q[6]) * 0.25)
            cy = int((q[1] + q[3] + q[5] + q[7]) * 0.25)
            ox, oy = self._centres[i]
            if abs(cx - ox) > MOVE_RESET_PX or abs(cy - oy) > MOVE_RESET_PX:
                self._streak_text[i] = ""
                self._streak_n[i] = 0
                self._texts[i] = ""
                self._box_ema[i] = None
            self._centres[i] = (cx, cy)
            quads[i] = self._smooth_box(i, q)

        # ---- recognition: one plate per detection, round-robin ----
        if quads:
            slot = self._rec_cursor % len(quads)
            self._rec_cursor += 1
            now = time.ticks_ms()
            if time.ticks_diff(now, self._rec_ms[slot]) >= REC_PERIOD_MS:
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

    def _smooth_box(self, slot, quad):
        """Exponential moving average of a slot's box.

        Returns a new array so the caller can replace its quad without touching the
        detector's output. With BOX_EMA = 0.35 a static plate settles on one crop
        within about three frames; a moving plate lags slightly, which is invisible
        on a 164 px tile but keeps the reading reproducible.
        """
        old = self._box_ema[slot]
        if old is None:
            smoothed = quad
        else:
            keep = 1.0 - BOX_EMA
            smoothed = np.array([
                old[0] * keep + quad[0] * BOX_EMA,
                old[1] * keep + quad[1] * BOX_EMA,
                old[2] * keep + quad[2] * BOX_EMA,
                old[3] * keep + quad[3] * BOX_EMA,
                old[4] * keep + quad[4] * BOX_EMA,
                old[5] * keep + quad[5] * BOX_EMA,
                old[6] * keep + quad[6] * BOX_EMA,
                old[7] * keep + quad[7] * BOX_EMA])
        self._box_ema[slot] = smoothed
        return smoothed

    def _reset_votes(self):
        """Forget every reading; used when the plate count changed."""
        self._streak_text = [""] * MAX_PLATES
        self._streak_n = [0] * MAX_PLATES
        self._texts = [""] * MAX_PLATES
        self._rec_ms = [0] * MAX_PLATES
        self._centres = [(0, 0)] * MAX_PLATES
        self._box_ema = [None] * MAX_PLATES

    def _vote(self, slot, text):
        """Record a reading and show it once STREAK_N frames agree.

        A distant plate hides only about 8x14 pixels behind each character, so one
        frame is frequently wrong in a character or two while the rest of the string
        is right (measured: 14 distinct readings over 192 frames of a single plate).
        The correct string still dominates and arrives in runs, so a streak of
        identical readings separates it from the scattered noise.

        Counting a streak rather than a majority means a wrong reading cannot
        accumulate its way into the label: one different reading resets the count.
        It also means a replaced plate cannot inherit the previous plate's label
        once the box moves, which is handled by _centres in step().

        STREAK_SWITCH applies once a label is already on screen, so a variant string
        has to hold for longer than a fresh label does. That is what stops the
        caption from alternating between two close readings.

        A string that cannot be a plate is not recorded at all: it would only reset
        a streak that is on its way to being right.
        """
        if len(text) < MIN_TEXT_LEN:
            return
        if not self._valid_text(text):
            return

        if text == self._streak_text[slot]:
            self._streak_n[slot] += 1
        else:
            self._streak_text[slot] = text
            self._streak_n[slot] = 1

        needed = STREAK_N if not self._texts[slot] else STREAK_SWITCH
        if self._streak_text[slot] != self._texts[slot] and self._streak_n[slot] >= needed:
            self._texts[slot] = self._streak_text[slot]

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
