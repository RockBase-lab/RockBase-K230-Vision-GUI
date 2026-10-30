# Faces app: full-screen live face detection (RetinaFace 320 + 5 landmarks).
#
# Role: the first-stage detector view of the Face group -- where the faces are,
# how many, plus the detector's own 5 points (both eyes, nose tip, both mouth
# corners). No recognition, no liveness, no occlusion masking; each of those has
# its own app.
#
# Display path matches Camera/Scan/Vision: chn2 (RGB888 packed, 800x480 = the
# screen) composited into an LVGL img widget via as_lvgl_img_src, with boxes and
# landmarks overlaid as LVGL objects (never drawn into the shared buffer, which
# would corrupt the next frame). The AI frame comes from chn1 (RGBP888 planar),
# matching ai2d's NCHW declaration.
#
# Measured on board (2026-10-30, 800x480 preview, 320 model):
#   ai2d 1.5 ms + kpu 4.0 ms + post 3.0 ms ~ 8.5 ms/frame, 3.6x faster than
#   yolov8n_320's 31 ms because RetinaFace 320 runs a single pass on a smaller
#   input. A face centred at about 50 cm was detected in 120/120 frames at
#   conf=0.5.
#
# Contract, all measured -- do not copy the yolov8 handling from Vision:
#   1) kpu.outputs_size() == 9: 3 scales x (8-channel cls / 4-channel bbox /
#      20-channel landmark), ordered cls[40,20,10] -> bbox[40,20,10] ->
#      lm[40,20,10]. Every output tensor must be to_numpy()d and then released
#      immediately; missing one leaks native memory.
#   2) Post-processing is aidemo.face_det_post_process(conf, nms, model_w,
#      anchors, rgb_size, results) and returns [boxes, landmarks]:
#        res[0] = (N,4) x,y,w,h
#        res[1] = (N,10), the 5 points (x0,y0,x1,y1,...)
#      With no face it returns len(res) == 0 (not ([],[])), so the result must be
#      checked for emptiness.
#   3) **Boxes carry no score**: 4 columns only, no confidence. That is why the bar
#      reports how many faces rather than a confidence; a score would mean parsing
#      the cls tensor separately (deferred to V2).
#   4) Coordinates are in the preview domain but **y does not subtract the
#      letterbox top padding**: real y = returned y - 160, where 160 = pad_top /
#      scale and can be derived exactly (see y_pad_offset()). x/w/h need no fix.
#   5) The 5th parameter (rgb888p_size) must be [W, H + y_off] = [800, 640], not
#      [480, 800] and not [800, 480]. It sets both the letterbox scale and the
#      **clipping bounds**; a wrong value clips right-edge boxes to w=0 (entirely
#      invisible) and bottom boxes to h=7. See the comment in rgb_param()
#      (measured).
#
# The threshold is exposed as presets instead of model switching: yunet_640 goes
# through aidemo.yunet_postprocess with completely different parameters and shape
# of results (strides/top_k, landmark output unverified), which does not justify
# forking the post-processing for V1.
#
# Keys: CANCEL(GPIO0) exits (launcher default); OK(GPIO53) freezes/resumes or
# closes the overlay. There is no back/Home button, same as Vision's Detect: the
# whole screen is the viewfinder and the top only holds one info bar (tap = cycle
# threshold preset). A mis-tap would interrupt detection, so exiting stays on the
# physical CANCEL key.
#
# Resource discipline: the sensor configures chn1/chn2 only, **no chn0** (no still
# capture). One less mpp channel saves resources and lowers the risk of leftovers
# such as "AO will not open after exit"; measured, AO still opens normally after
# this app runs (P0 probe PASS).

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

PREVIEW_W = 800               # capture size shared by preview and AI (= screen, source == widget)
PREVIEW_H = 480

MODEL_PATH = "/sdcard/examples/kmodel/face_detection_320.kmodel"
ANCHOR_PATH = "/sdcard/examples/utils/prior_data_320.bin"
MODEL_IN = [320, 320]
ANCHOR_N = 4200               # anchor count of prior_data_320.bin, (4200, 4) float

# Upper bound on faces drawn per frame. Boxes + labels + 5 landmarks are all
# pre-built and reused: 6 faces = 6 boxes + 6 labels + 30 dots = 42 objects. At
# 12 objects this app ran at about 10 fps, with the cost sitting in LVGL FULL
# redraws rather than in the boxes themselves; 6 faces measured smooth.
MAX_FACES = 6
POINTS_PER_FACE = 5
DOT_SIZE = 6                  # landmark dot diameter

# Top info bar: spans the full width (8 px margin each side), 56 px high, shifted
# down 10 px. As in Vision's Detect, **no back button**: the whole screen is the
# viewfinder and a mis-tap would interrupt detection, so exiting stays on the
# physical CANCEL key (one convention across Camera / Scan / Vision). 56 px is
# the smallest height a finger reliably hits; tapping a 20 px tall text strip
# misses often.
TOPBAR_H = 56
TOPBAR_Y = 10
TOPBAR_LEFT = 8               # same margin as the right side; the bar spans the screen width
TOPBAR_RIGHT = 8

# Segment colours: subject name highlighted, metrics next, hints dimmest (as in
# Vision).
COL_TITLE = 0x00E5A0
COL_FPS = 0xFFC857
COL_CNT = 0x64B5F6
COL_CNT_IDLE = 0x6B7885
COL_SEP = 0x3A4650
COL_HINT = 0x7A8794
COL_FROZEN = 0xFF8A65
COL_ERROR = 0xFF6B6B
SEG_PAD = 18

# Reserved label height: montserrat_14 is 14 px tall plus 1 px pad_ver on each
# side = 16, rounded up to 18 for margin. The label height cannot be measured at
# runtime: widgets are not laid out yet during app construction and
# get_height() is always 0.
LAB_H = 18

# Landmark colour (teal, matching the title); boxes rotate through 6 colours by
# face index so people stay distinguishable.
COL_POINTS = 0x00E5A0
FACE_COLORS = [0x7FB6D9, 0xC99AAE, 0x9C92C9, 0x6FB8A4, 0xC9AE7A, 0xCC8A92]

# Threshold presets. The default RetinaFace threshold is 0.5; measured, 0.25
# detects more frames (fewer misses at the same distance) while 0.7 keeps only
# faces it is sure about. NMS is fixed at 0.2.
THRESHOLDS = [
    ("Recall 0.25", 0.25),
    ("Normal 0.50", 0.50),
    ("Strict 0.70", 0.70),
]
DEFAULT_TH = 1
NMS_TH = 0.2

# Serial print throttle: about once per 200 ms is enough to see changes without
# flooding the log or slowing the main loop.
PRINT_MS = 200

MODEL_MISSING = ("Face model or anchors missing on SD card.\n"
                 "Expected:\n"
                 "  /sdcard/examples/kmodel/face_detection_320.kmodel\n"
                 "  /sdcard/examples/utils/prior_data_320.bin")


def letterbox():
    """Letterbox parameters from 800x480 to 320x320.

    The pad acts in the output space after scaling. The 5:3 preview is wider than
    the square model, so width saturates first and grey bands appear only
    vertically:
      scale = min(320/800, 320/480) = 0.4
      content_h = 480 * 0.4 = 192
      pad_top = (320 - 192) / 2 = 64
    Returns (scale, pad_top).
    """
    mw, mh = MODEL_IN
    scale = min(float(mw) / PREVIEW_W, float(mh) / PREVIEW_H)
    pad_top = int((mh - int(PREVIEW_H * scale)) / 2)
    return scale, pad_top


def y_pad_offset():
    """Post-processed y still includes the letterbox top padding.

    This is not an empirical constant but exactly derivable: the post-processing
    inverts a standard letterbox transform
      y_out = (pad_top + screen_y * scale) / scale = pad_top / scale + screen_y
    so y_off always equals pad_top / scale (= pad_top * preview width / model
    width). With an 800x480 preview and a 320 model: pad_top=64, scale=0.4 -> 160.

    Measured on board (face centred, conf=0.5, detected in 120/120 frames): box
    centre (360, 245), dy = +5 after subtracting 160; without it everything sits
    ~140 too low. x / w / h need no correction.
    """
    scale, pad_top = letterbox()
    return int(pad_top / scale)


def rgb_param():
    """The 5th parameter of face_det_post_process (called rgb888p_size in the API).

    Two traps, both confirmed on board:

    1) **It is not [height, width]**. Post-processing uses it for the letterbox
       ratio
         scale = min(model_w / param_w, model_h / param_h)
       Passing [480, 800] yields scale = min(0.667, 0.4) = 0.4, which happens to be
       right, so the mistake stays invisible. But the same rectangle is also used
       as the **clipping bounds**, cropping boxes to param_w x param_h. With an x
       bound of only 480, a large face on the right (x+w ~ 734) is clipped to
       x=480, w=0 and disappears from the screen entirely. That was the real cause
       of "large faces on the right half are never detected" (measured).

    2) **The height must include y_off**. The returned y already contains the
       letterbox top padding (real y = returned y - y_off), so a face at the very
       bottom can return y up to H + y_off = 640. Writing the height as 480 clips
       that face to h=7, leaving a single strip.

    Hence [W, H + y_off]. The scale must still be decided by the width, so
    H + y_off <= W has to hold (640 <= 800 for 800x480), otherwise the scale
    changes and every coordinate is wrong.
    """
    return [PREVIEW_W, PREVIEW_H + y_pad_offset()]


def _font(*names):
    for name in names:
        font = getattr(lv, name, None)
        if font:
            return font
    return None


def label_dy(box_y):
    """Y offset of a label relative to its box top: move it into the box's inner
    top-left corner when it does not fit."""
    return -LAB_H if box_y >= LAB_H else 1


class FaceDetApp:
    def __init__(self, content, ctx):
        self.ctx = ctx
        self.content = content
        self.sensor = None
        self.kpu = None
        self.ai2d = None
        self.anchors = None
        self.frozen = False
        self.frame_count = 0
        self.last_fps_ms = time.ticks_ms()
        self.fps_text = "-"
        self.last_faces = 0           # boxes actually drawn on screen (<= MAX_FACES)
        self._more_faces = False      # raw detections exceeded the pool, so display "6+"
        self._gc_tick = 0
        self._print_ms = 0
        self.th_idx = DEFAULT_TH
        self.y_off = y_pad_offset()
        self.picker = None            # threshold picker overlay (None = closed)
        self._no_signal = NoSignalCounter()

        self.img_w = ctx.width
        self.img_h = ctx.height

        # ---- preview ----
        self.img_widget = lv.img(content)
        self.img_widget.set_size(self.img_w, self.img_h)
        self.img_widget.align(lv.ALIGN.CENTER, 0, 0)

        # ---- box / label / landmark pools (all pre-built; per frame only geometry
        # and visibility change) ----
        self.box_objs = []
        self.box_labels = []
        label_font = _font("font_montserrat_14", "font_montserrat_12")
        for _ in range(MAX_FACES):
            box = lv.obj(content)
            box.set_style_bg_opa(lv.OPA.TRANSP, 0)
            box.set_style_border_width(3, 0)
            box.set_style_radius(6, 0)
            box.clear_flag(lv.obj.FLAG.SCROLLABLE)  # objects scroll by default; drop it so no scrollbar shows
            box.add_flag(lv.obj.FLAG.HIDDEN)
            self.box_objs.append(box)
            lab = lv.label(content)
            lab.add_flag(lv.obj.FLAG.HIDDEN)
            lab.set_style_bg_opa(lv.OPA._80, 0)
            lab.set_style_pad_hor(4, 0)
            lab.set_style_pad_ver(1, 0)
            lab.set_style_radius(3, 0)
            if label_font is not None:
                lab.set_style_text_font(label_font, 0)
            self.box_labels.append(lab)

        self.dot_objs = []
        for _ in range(MAX_FACES * POINTS_PER_FACE):
            dot = lv.obj(content)
            dot.set_size(DOT_SIZE, DOT_SIZE)
            dot.set_style_radius(DOT_SIZE // 2 + 1, 0)   # radius >= half width => a circle
            dot.set_style_bg_opa(lv.OPA.COVER, 0)
            dot.set_style_bg_color(lv.color_hex(COL_POINTS), 0)
            dot.set_style_border_width(0, 0)
            dot.set_style_pad_all(0, 0)
            dot.clear_flag(lv.obj.FLAG.SCROLLABLE)
            dot.add_flag(lv.obj.FLAG.HIDDEN)
            self.dot_objs.append(dot)

        # ---- top info bar: the whole strip is clickable and opens the presets ----
        # The hit area must sit on the container and be large enough: with the
        # event on a label the hit area was only the 16 px tall text, so taps on
        # the padding did nothing (measured on board). The strip spans the screen
        # width (8 px margin each side) and doubles as the visual margin left by
        # having no back button.
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
            lab.clear_flag(lv.obj.FLAG.CLICKABLE)  # click lands on the whole strip
            return lab

        self.seg_title = _seg("Faces", COL_TITLE)
        self.seg_sep1 = _seg("|", COL_SEP)
        self.seg_count = _seg("no face", COL_CNT_IDLE)
        self.seg_sep2 = _seg("|", COL_SEP)
        self.seg_fps = _seg("FPS -", COL_FPS)
        self.seg_hint = _seg("TAP: threshold", COL_HINT)
        # ---- AI pipeline (loaded during construction; kpu.load measured ~390 ms) ----
        self._init_ai()

    # ---------- AI pipeline ----------
    def _init_ai(self):
        t0 = time.ticks_ms()

        # A missing model or anchor file is "resource unavailable", converted to
        # AppUnavailable so the launcher raises its panel instead of splashing a
        # traceback on screen. Without anchors detection is useless (always empty
        # results), so fail explicitly rather than silently using zero anchors.
        try:
            if not self._exists(MODEL_PATH) or not self._exists(ANCHOR_PATH):
                raise AppUnavailable(MODEL_MISSING)
        except AppUnavailable:
            raise
        except BaseException as error:
            print("[Faces] file check error:", error)

        try:
            f = open(ANCHOR_PATH, "rb")
            raw = f.read()
            f.close()
            del f
            self.anchors = np.frombuffer(raw, dtype=np.float).reshape(
                (ANCHOR_N, 4))
            del raw
            print("[Faces] anchors", self.anchors.shape)
        except BaseException as error:
            print("[Faces] anchor load failed:", error)
            sys.print_exception(error)
            raise AppUnavailable(MODEL_MISSING)

        sensor = None
        try:
            sensor = Sensor(fps=30)
            sensor.reset()
            # chn1 = AI input, must be RGBP888 (planar, to_numpy_ref() yields CHW).
            # ai2d declares NCHW_FMT; feeding packed RGB888 makes it read HWC
            # memory as NCHW and the model sees garbage (measured in Vision).
            sensor.set_framesize(w=PREVIEW_W, h=PREVIEW_H, chn=CAM_CHN_ID_1,
                                 crop=True)
            sensor.set_pixformat(Sensor.RGBP888, chn=CAM_CHN_ID_1)
            # chn2 = preview display, packed RGB888 (as_lvgl_img_src only takes packed).
            sensor.set_framesize(w=PREVIEW_W, h=PREVIEW_H, chn=CAM_CHN_ID_2,
                                 crop=True)
            sensor.set_pixformat(Sensor.RGB888, chn=CAM_CHN_ID_2)
            sensor.run()
        except BaseException as error:
            print("[Faces] sensor init failed:", error)
            sys.print_exception(error)
            close_sensor(sensor)
            raise AppUnavailable(CAMERA_UNAVAILABLE)
        self.sensor = sensor

        self._load_model()

        load_ms = time.ticks_diff(time.ticks_ms(), t0)
        print("[Faces] ready: load={}ms pad_top={} y_off={} mem_free={}".format(
            load_ms, letterbox()[1], self.y_off, gc.mem_free()))
        self._update_stat()

    @staticmethod
    def _exists(path):
        try:
            return os.stat(path)[6] > 0
        except BaseException:
            return False

    def _release_model(self):
        if self.kpu is not None:
            try:
                self.kpu.release()
            except BaseException as e:
                print("[Faces]   kpu.release error:", e)
            self.kpu = None
        if self.ai2d is not None:
            try:
                self.ai2d.deinit()
            except BaseException as e:
                print("[Faces]   ai2d.deinit error:", e)
            self.ai2d = None
        gc.collect()

    def _load_model(self):
        mw, mh = MODEL_IN
        _scale, pad_top = letterbox()

        kpu = nn.kpu()
        kpu.load_kmodel(MODEL_PATH)
        self.kpu = kpu

        ai2d = Ai2d()
        ai2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                            np.uint8, np.uint8)
        # The pad acts in the output space after scaling (verified with a geometry
        # probe); left/right stay 0 because the 5:3 preview is wider than the
        # square model, so width saturates first and grey bands appear only
        # vertically. The fill colour is RetinaFace's training BGR mean, which
        # reduces false detections along the edges.
        ai2d.pad([0, 0, 0, 0, pad_top, pad_top, 0, 0], 0, [104, 117, 123])
        ai2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
        ai2d.build([1, 3, PREVIEW_H, PREVIEW_W], [1, 3, mh, mw])
        self.ai2d = ai2d

        print("[Faces] model loaded: in={}x{} pad_top={} mem_free={}".format(
            mw, mh, pad_top, gc.mem_free()))

    def _infer(self, img):
        """One frame: ai2d -> kpu -> face_det_post_process. img must come from the
        RGBP888 channel.

        Returns aidemo's raw result ([boxes, landmarks]) or None when no face is
        found. Memory discipline: all 9 output tensors are to_numpy()d and
        released one by one; missing one leaks native memory and the board
        crashes with USB dropping after a few dozen frames.
        """
        conf = THRESHOLDS[self.th_idx][1]
        tin = self.ai2d.run(img.to_numpy_ref())
        self.kpu.set_input_tensor(0, tin)
        self.kpu.run()
        outs = []
        n = self.kpu.outputs_size()
        for i in range(n):
            ot = self.kpu.get_output_tensor(i)
            try:
                outs.append(ot.to_numpy())
            finally:
                ot.release()
        try:
            res = aidemo.face_det_post_process(
                conf, NMS_TH, MODEL_IN[0], self.anchors,
                rgb_param(), outs)
        finally:
            del outs, tin
        if len(res) == 0:
            return None
        return res

    # ---------- drawing ----------
    def _hide_all(self):
        for box in self.box_objs:
            box.add_flag(lv.obj.FLAG.HIDDEN)
        for lab in self.box_labels:
            lab.add_flag(lv.obj.FLAG.HIDDEN)
        for dot in self.dot_objs:
            dot.add_flag(lv.obj.FLAG.HIDDEN)

    def _show_faces(self, res):
        """Map the result onto the pre-built box / label / dot pools.

        Coordinates are already in the 800x480 preview domain, but y still holds
        the letterbox top padding, so self.y_off is subtracted. Returns (drawn
        boxes, whether more remain) for the bar counter -- the counter must match
        the boxes actually visible on screen, otherwise it misleads, as with "53"
        reported while only 6 were drawn (measured).
        """
        boxes = res[0] if res else None
        points = res[1] if res and len(res) > 1 else None
        n = len(boxes) if boxes else 0
        if n > MAX_FACES:
            n = MAX_FACES

        drawn = 0
        for i in range(MAX_FACES):
            box = self.box_objs[i]
            lab = self.box_labels[i]
            if i >= n:
                box.add_flag(lv.obj.FLAG.HIDDEN)
                lab.add_flag(lv.obj.FLAG.HIDDEN)
                continue
            b = boxes[i]
            x = int(b[0])
            y = int(b[1]) - self.y_off
            w = int(b[2])
            h = int(b[3])
            # Post-processing yields degenerate boxes clipped to w/h=0 at the
            # frame edge (measured x=480, w=0). Nothing can be drawn for them, so
            # they are skipped and not counted.
            if w <= 0 or h <= 0:
                box.add_flag(lv.obj.FLAG.HIDDEN)
                lab.add_flag(lv.obj.FLAG.HIDDEN)
                continue
            drawn += 1
            color = lv.color_hex(FACE_COLORS[i % len(FACE_COLORS)])
            box.clear_flag(lv.obj.FLAG.HIDDEN)
            box.set_pos(x, y)
            box.set_size(w, h)
            box.set_style_border_color(color, 0)
            # Post-processing returns no score, so the label shows an index (when
            # there are several faces) or just FACE.
            lab.set_text("#{} FACE".format(i + 1) if n > 1 else "FACE")
            lab.set_style_text_color(color, 0)
            lab.set_style_bg_color(lv.color_hex(0x000000), 0)
            lab.clear_flag(lv.obj.FLAG.HIDDEN)
            lab.align_to(box, lv.ALIGN.TOP_LEFT, 0, label_dy(y))

            # 5 landmarks: each face owns a fixed POINTS_PER_FACE slice of the pool
            base = i * POINTS_PER_FACE
            if points and i < len(points):
                pts = points[i]
                cnt = len(pts) // 2
                if cnt > POINTS_PER_FACE:
                    cnt = POINTS_PER_FACE
                for k in range(POINTS_PER_FACE):
                    dot = self.dot_objs[base + k]
                    if k >= cnt:
                        dot.add_flag(lv.obj.FLAG.HIDDEN)
                        continue
                    px = int(pts[k * 2])
                    py = int(pts[k * 2 + 1]) - self.y_off
                    dot.set_pos(px - DOT_SIZE // 2, py - DOT_SIZE // 2)
                    dot.clear_flag(lv.obj.FLAG.HIDDEN)
            else:
                for k in range(POINTS_PER_FACE):
                    self.dot_objs[base + k].add_flag(lv.obj.FLAG.HIDDEN)

        # hide the dots of faces beyond the current count (when n shrinks)
        keep = drawn * POINTS_PER_FACE
        for k in range(keep, len(self.dot_objs)):
            self.dot_objs[k].add_flag(lv.obj.FLAG.HIDDEN)
        return drawn, len(boxes) > MAX_FACES if boxes else False

    def _print_faces(self, res):
        """Print detections throttled by PRINT_MS, using the same conversion as the
        screen so the two can be compared."""
        now = time.ticks_ms()
        if time.ticks_diff(now, self._print_ms) < PRINT_MS:
            return
        self._print_ms = now
        if not res or not res[0]:
            return
        boxes = res[0]
        points = res[1] if len(res) > 1 else None
        n = min(len(boxes), MAX_FACES)
        parts = []
        for i in range(n):
            b = boxes[i]
            x = int(b[0])
            y = int(b[1]) - self.y_off
            w = int(b[2])
            h = int(b[3])
            if w <= 0 or h <= 0:
                continue          # degenerate boxes are neither printed nor drawn
            parts.append("FACE#{} [{},{},{}x{}]".format(i + 1, x, y, w, h))
            if points and i < len(points) and len(points[i]) >= 10:
                p = points[i]
                parts[-1] += " lm({},{})".format(
                    int(p[0]), int(p[1]) - self.y_off)
        if not parts:
            return
        print("[Faces] {} | {}".format(
            "lvl {}".format(THRESHOLDS[self.th_idx][0]), " | ".join(parts)))

    def _update_stat(self):
        conf = THRESHOLDS[self.th_idx][1]
        self.seg_title.set_text("Faces {:.2f}".format(conf))
        self.seg_title.set_style_text_color(lv.color_hex(COL_TITLE), 0)
        if self.last_faces > 0:
            self.seg_count.set_text("{}{} face{}".format(
                self.last_faces, "+" if self._more_faces else "",
                "" if self.last_faces == 1 else "s"))
            self.seg_count.set_style_text_color(lv.color_hex(COL_CNT), 0)
        else:
            self.seg_count.set_text("no face")
            self.seg_count.set_style_text_color(lv.color_hex(COL_CNT_IDLE), 0)
        self.seg_fps.set_text("FPS {}".format(self.fps_text))
        if self.frozen:
            self.seg_hint.set_text("FROZEN")
            self.seg_hint.set_style_text_color(lv.color_hex(COL_FROZEN), 0)
        else:
            self.seg_hint.set_text("TAP: threshold")
            self.seg_hint.set_style_text_color(lv.color_hex(COL_HINT), 0)

    def _show_error(self, text):
        self.seg_hint.set_text(text)
        self.seg_hint.set_style_text_color(lv.color_hex(COL_ERROR), 0)

    # ---------- threshold overlay ----------
    def _close_picker(self):
        """Close the overlay and unfreeze. Every close path must unfreeze,
        otherwise the picture stalls on the last frame."""
        if self.picker is not None:
            try:
                self.picker.delete()
            except BaseException as e:
                print("[Faces] picker delete error:", e)
            self.picker = None
            print("[Faces] picker closed")
        self.frozen = False
        self._update_stat()

    def _open_picker(self):
        """Open the threshold presets. Idempotent: returns immediately if open.

        Same two layers as Vision's model list: a full-screen backdrop (tap
        outside to close) plus a centred panel that swallows clicks.
        """
        if self.picker is not None:
            return
        print("[Faces] picker open")
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
        panel.set_size(560, 76 + len(THRESHOLDS) * 58)
        panel.align(lv.ALIGN.CENTER, 0, 0)
        panel.set_style_bg_color(lv.color_hex(0x101418), 0)
        panel.set_style_bg_opa(lv.OPA.COVER, 0)
        panel.set_style_border_width(2, 0)
        panel.set_style_border_color(lv.color_hex(COL_SEP), 0)
        panel.set_style_radius(14, 0)
        panel.set_style_pad_all(12, 0)
        panel.clear_flag(lv.obj.FLAG.SCROLLABLE)
        panel.add_flag(lv.obj.FLAG.CLICKABLE)   # swallow clicks instead of closing

        title = lv.label(panel)
        title.set_text("Detection threshold")
        title.set_style_text_color(lv.color_hex(0xE6EAF0), 0)
        title.align(lv.ALIGN.TOP_MID, 0, 2)

        hint = lv.label(panel)
        hint.set_text("tap a row to apply, tap outside to close")
        hint.set_style_text_color(lv.color_hex(0x8A97A3), 0)
        hint.align(lv.ALIGN.TOP_MID, 0, 24)

        font = _font("font_montserrat_16", "font_montserrat_14")
        y = 48
        for idx, (name, conf) in enumerate(THRESHOLDS):
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
            lab.set_text("{}{}  (conf {:.2f})".format(mark, name, conf))
            lab.set_style_text_color(
                lv.color_hex(0x00C853 if idx == self.th_idx else 0xE6EAF0), 0)
            if font is not None:
                lab.set_style_text_font(font, 0)
            lab.center()
            lab.clear_flag(lv.obj.FLAG.CLICKABLE)   # let the click land on the row
            beep.on_click(row, lambda e, i=idx: self._pick(i))
            y += 58

        self.picker = backdrop

    def _pick(self, idx):
        self._close_picker()
        if idx != self.th_idx:
            self.th_idx = idx
            print("[Faces] threshold -> {}".format(THRESHOLDS[idx][0]))
            self._update_stat()

    # ---------- main loop ----------
    def step(self):
        if self.sensor is None:
            return

        # OK key: close the overlay if open, else open the presets (fallback entry
        # point when touch fails)
        if self.ctx.take_ok():
            if self.picker is not None:
                self._close_picker()
            else:
                self._open_picker()

        if self.frozen:
            return

        try:
            ai_img = self.sensor.snapshot(chn=CAM_CHN_ID_1)
            img = self.sensor.snapshot(chn=CAM_CHN_ID_2)
        except BaseException as error:
            self._show_error("snapshot error")
            # consecutive failures reach the threshold -> the camera is gone, let
            # the launcher raise its panel (Cancel returns to the desktop)
            self._no_signal.on_fail(self.ctx)
            return
        self._no_signal.on_ok()

        res = None
        try:
            res = self._infer(ai_img)
        except BaseException as error:
            print("[Faces] infer error:", error)
            sys.print_exception(error)

        # preview composite (keep showing frames even if inference failed)
        try:
            img.as_lvgl_img_src(self.img_widget)
            self.img_widget.invalidate()
        except BaseException as error:
            self._show_error("display error")
            return

        # The count must match the boxes drawn: the pool holds only MAX_FACES and
        # degenerate boxes are skipped, so showing the raw detection count looks
        # like boxes went missing (measured: "53 faces" with 6 drawn).
        n_drawn, more = self._show_faces(res)
        self.last_faces = n_drawn
        self._more_faces = more
        self._print_faces(res)

        # LVGL img sources and sensor frames are cyclic garbage that reference
        # counting never frees. Without a periodic collect the contiguous heap
        # fills up and to_numpy starts failing every few dozen frames.
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
        print("[Faces] destroy begin")
        self._close_picker()
        self._release_model()
        print("[Faces]   model released")
        if self.sensor is not None:
            for name in ("stop", "deinit"):
                try:
                    getattr(self.sensor, name)()
                    print("[Faces]   sensor.{} ok".format(name))
                except BaseException as e:
                    print("[Faces]   sensor.{} error: {}".format(name, e))
            self.sensor = None
        gc.collect()
        print("[Faces] destroy done | mem_free={}".format(gc.mem_free()))


def create_app(content, ctx):
    return FaceDetApp(content, ctx)
