# Vision app: single-page live object detection (yolov8n_320, COCO 80 classes).
#
# Display path matches Camera: chn2 frames are composited into an LVGL img widget
# via as_lvgl_img_src, and detection boxes are overlaid as LVGL objects (never
# drawn into the shared buffer, which would corrupt the next frame).
#
# Preview resolution is 800x480 (= the screen) because this is the one verified
# path: the chn2 frame size must equal the img widget size, since as_lvgl_img_src
# only attaches the buffer without scaling; when source != widget the stride
# mismatches and the picture shatters into wrapped bands (measured). The ISP
# hardware crop to 5:3 costs nothing.
# The 320x320 AI input goes through letterbox (scale 0.4 -> 320x192, 64 px of
# padding top and bottom) to keep the aspect ratio. Note that aidemo's coordinate
# mapping does not include that top padding (measured 160 px offset), so
# _show_boxes must subtract the pad offset itself or every box lands too low.
#
# Score scale (corrected twice after measurement): this kmodel's score column
# holds raw values (measured to go negative, so not probabilities). On a blank
# sheet every anchor scores about +-0.005..0.02 of dequantisation noise.
# An early attempt added a sigmoid before handing values to aidemo: sigmoid(0) =
# 0.5 already exceeds any 0..1 threshold, so a blank sheet filled with boxes (on
# board). The working approach feeds to_numpy values straight into aidemo and
# filters by the raw-value threshold; the sigmoid is applied only when drawing
# labels, for at most BOX_POOL boxes, purely for readability.
#
# Native tensor discipline: every kpu output tensor converted to numpy must be
# release()d, otherwise native memory leaks and the board crashes with USB
# dropping after a few dozen frames (measured).
#
# Keys: CANCEL(GPIO0) exits (launcher default); OK(GPIO53) freezes/resumes.
# There is no exit button: the whole screen is the viewfinder and the top only
# holds one model info bar (tap = switch model). A mis-tap on an exit button
# would interrupt monitoring, so exiting stays on the physical CANCEL key.

import gc
import math
import sys
import time

import lvgl as lv
import ulab.numpy as np
import nncase_runtime as nn
import aidemo
from libs.AI2D import Ai2d
from media.sensor import Sensor, CAM_CHN_ID_0, CAM_CHN_ID_1, CAM_CHN_ID_2
from ui import beep
from apps.error import (AppUnavailable, CAMERA_UNAVAILABLE, NoSignalCounter,
                       close_sensor)

PREVIEW_W = 800               # capture size shared by preview and AI (= screen, source == widget)
PREVIEW_H = 480
MAX_BOXES = 12                # max boxes per frame (also the box pool size)
BOX_POOL = MAX_BOXES
# Top model bar: spans the full width (8 px margin each side), 56 px high,
# shifted down 10 px. 56 px is the smallest height a finger reliably hits;
# tapping a 20 px tall text strip misses often.
TOPBAR_H = 56
TOPBAR_Y = 10
TOPBAR_LEFT = 8               # same margin as the right side; the bar spans the screen width
TOPBAR_RIGHT = 8

# The bar is colour-coded by segment: model name (most important) highlighted,
# runtime metrics next, hints dimmest. With one colour the eye has to read the
# whole line to find the model name; segments make it locatable at a glance.
COL_MODEL = 0x00E5A0          # teal: current model name
COL_FPS = 0xFFC857            # amber: frame rate
COL_OBJS = 0x64B5F6           # sky blue: current object count (when non-zero)
COL_OBJS_IDLE = 0x6B7885      # grey: demoted obj when idle so "obj 0" is not eye-catching
COL_SEP = 0x3A4650            # dim grey: segment separator
COL_HINT = 0x7A8794           # grey: hint text
COL_FROZEN = 0xFF8A65         # orange red: frozen state
COL_ERROR = 0xFF6B6B          # red: error message
SEG_PAD = 18                  # horizontal gap between segments (px)

# Reserved height of a detection label: montserrat_14 is 14 px tall plus 1 px
# pad_ver on each side = 16, rounded up to 18 for margin. Used to decide whether
# a label placed above the box would be clipped by the screen top. The label
# height cannot be measured at runtime: widgets are not laid out yet while the
# app is being constructed, so get_height() is always 0 (measured).
LAB_H = 18

# Serial print throttle for detections (ms). Detection runs at ~13 fps, so
# printing every frame floods the log and slows the main loop (CDC writes cost
# time too); 200 ms shows every change without drowning other logs.
DET_PRINT_MS = 200


def label_dy(box_y):
    """Y offset of a detection label relative to its box top.

    The offset used to be a fixed -16 (label hovering above the box). When a box
    touches the screen top (y < LAB_H) the upper half of the label was clipped
    and the text unreadable (measured on board). Now a label that does not fit
    moves to the box's inner top-left corner, overlapping the border by 1 px but
    keeping every character.
    """
    return -LAB_H if box_y >= LAB_H else 1

COCO = ["person", "bicycle", "car", "motorcycle", "airplane", "bus", "train",
"truck", "boat", "traffic light", "fire hydrant", "stop sign", "parking meter",
"bench", "bird", "cat", "dog", "horse", "sheep", "cow", "elephant", "bear",
"zebra", "giraffe", "backpack", "umbrella", "handbag", "tie", "suitcase",
"frisbee", "skis", "snowboard", "sports ball", "kite", "baseball bat",
"baseball glove", "skateboard", "surfboard", "tennis racket", "bottle",
"wine glass", "cup", "fork", "knife", "spoon", "bowl", "banana", "apple",
"sandwich", "orange", "broccoli", "carrot", "hot dog", "pizza", "donut",
"cake", "chair", "couch", "potted plant", "bed", "dining table", "toilet",
"tv", "laptop", "mouse", "remote", "keyboard", "cell phone", "microwave",
"oven", "toaster", "sink", "refrigerator", "book", "clock", "vase",
"scissors", "teddy bear", "hair drier", "toothbrush"]

# Switchable model table. Switching only changes an index into this table:
# kpu/ai2d are rebuilt for the input size, post-processing is dispatched on the
# post field, and both threshold and labels follow the table.
#   input  : model input [w, h] (every kmodel here is square)
#   post   : aidemo post-processing family (yolov8 / yolo26 / yolov5)
#   conf   : score-column threshold. The scales differ per family: a real person
#            measures ~0.88 with yolov8 COCO, while person_detect_yolov5n uses
#            only 0.2, so the same value cannot be shared.
MODELS = [
    {"id": "v8n320", "name": "YOLOv8n 320",
     "path": "/sdcard/examples/kmodel/yolov8n_320.kmodel",
     "input": [320, 320], "labels": COCO, "post": "yolov8",
     "conf": 0.30, "nms": 0.45},
    {"id": "v8n224", "name": "YOLOv8n 224",
     "path": "/sdcard/examples/kmodel/yolov8n_224.kmodel",
     "input": [224, 224], "labels": COCO, "post": "yolov8",
     "conf": 0.30, "nms": 0.45},
    {"id": "fruitv8", "name": "Fruit YOLOv8n",
     "path": "/sdcard/examples/kmodel/fruit_det_yolov8n_320.kmodel",
     "input": [320, 320], "labels": ["apple", "banana", "orange"],
     "post": "yolov8", "conf": 0.30, "nms": 0.45},
    {"id": "yolo26n", "name": "YOLO26n 320",
     "path": "/sdcard/examples/kmodel/yolo26n.kmodel",
     "input": [320, 320], "labels": COCO, "post": "yolo26",
     "conf": 0.30, "nms": 0.45},
]
DEFAULT_MODEL = 0

# Fixed 8-colour palette rotated by class id (soft and low saturation, tuned to
# the desktop palette)
CLASS_COLORS = [0x7FB6D9, 0xC99AAE, 0x9C92C9, 0x6FB8A4,
                0xC9AE7A, 0x7FA6CC, 0xA9BE7C, 0xCC8A92]


def letterbox(cfg):
    """Letterbox parameters from 800x480 to a square model input.

    The pad acts in the output space after scaling. The preview is 5:3 and the
    model square, so width saturates first and grey bands appear only
    vertically:
      top/bottom = (in_h - 480*scale) / 2, left/right = 0
    Returns (scale, pad_top); together they determine the y offset that aidemo's
    output has to be corrected by.
    """
    mw, mh = cfg["input"]
    scale = min(float(mw) / PREVIEW_W, float(mh) / PREVIEW_H)
    pad_top = int((mh - int(PREVIEW_H * scale)) / 2)
    return scale, pad_top


def y_pad_offset(cfg):
    """aidemo's y coordinates do not subtract the letterbox top padding.

    Both families need the correction (same frame; boxes only line up after
    subtracting 160):
      yolov8n_320 -> tv y=1, person y=41
      yolo26n     -> tv y=2, person y=35
    Uncorrected, both sit 160 too low (= pad_top(64) * scale(2.5)).

    Note the value always equals (PREVIEW_W - PREVIEW_H) / 2 = 160: the preview is
    wider than the model input, so width is always the constraining axis and
    pad_top*W/M simplifies to a model-independent constant. It is still computed
    from the formula so it survives a future preview size change.
    """
    _scale, pad_top = letterbox(cfg)
    return int(pad_top * PREVIEW_W / cfg["input"][0])


def _font(*names):
    for name in names:
        font = getattr(lv, name, None)
        if font:
            return font
    return None


class VisionApp:
    def __init__(self, content, ctx):
        self.ctx = ctx
        self.content = content
        self.sensor = None
        self.kpu = None
        self.ai2d = None
        self.frozen = False
        self.frame_count = 0
        self.last_fps_ms = time.ticks_ms()
        self.fps_text = "-"
        self.last_objs = 0
        self._gc_tick = 0
        self._det_print_ms = 0        # ticks of the last detection print (throttle)
        # current model: table entry plus the letterbox / y offset derived from it
        self.model_idx = DEFAULT_MODEL
        self.cfg = MODELS[DEFAULT_MODEL]
        self.y_off = y_pad_offset(self.cfg)
        self.picker = None          # model picker overlay (None = closed)
        # Runtime disconnect detection (same as Camera/Scan): when the camera is
        # unplugged mid-run, consecutive capture failures reaching the threshold
        # report to the launcher, which raises the unavailable panel.
        self._no_signal = NoSignalCounter()

        self.img_w = ctx.width
        self.img_h = ctx.height

        # ---- preview ----
        self.img_widget = lv.img(content)
        self.img_widget.set_size(self.img_w, self.img_h)
        self.img_widget.align(lv.ALIGN.CENTER, 0, 0)

        # ---- box pool: border object + label, pre-built and reused ----
        self.box_objs = []
        self.box_labels = []
        label_font = _font("font_montserrat_14", "font_montserrat_12")
        for _ in range(BOX_POOL):
            box = lv.obj(content)
            box.set_style_bg_opa(lv.OPA.TRANSP, 0)
            box.set_style_border_width(3, 0)
            box.set_style_radius(4, 0)
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

        # ---- top model bar: the whole strip is clickable and opens the list ----
        # The hit area must be large and the event must sit on the container: with
        # the event on a label the hit area was only the 16 px tall text, so taps
        # on the padding did nothing. Sizing with SIZE_CONTENT also failed: the
        # width is computed before the label exists (no children yet) and comes
        # out as 0, leaving the whole bar unclickable (measured on board). Now the
        # strip is 56 px tall across the full width, so any segment opens the list.
        bar_w = self.img_w - TOPBAR_LEFT - TOPBAR_RIGHT
        self.stat_btn = lv.btn(content)
        self.stat_btn.set_size(bar_w, TOPBAR_H)
        self.stat_btn.align(lv.ALIGN.TOP_LEFT, TOPBAR_LEFT, TOPBAR_Y)
        self.stat_btn.set_style_bg_color(lv.color_hex(0x000000), 0)
        self.stat_btn.set_style_bg_opa(lv.OPA._50, 0)
        self.stat_btn.set_style_radius(TOPBAR_H // 2, 0)
        self.stat_btn.set_style_border_width(1, 0)
        self.stat_btn.set_style_border_color(lv.color_hex(0x3A4650), 0)
        self.stat_btn.set_style_pad_all(0, 0)
        self.stat_btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
        self.stat_btn.add_flag(lv.obj.FLAG.CLICKABLE)
        beep.on_click(self.stat_btn, lambda e: self._open_picker())
        # Segments laid out horizontally: ROW flow and centred. Each segment owns
        # its colour while the strip stays a single click target.
        self.stat_btn.set_flex_flow(lv.FLEX_FLOW.ROW)
        self.stat_btn.set_flex_align(lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER,
                                     lv.FLEX_ALIGN.CENTER)
        try:
            self.stat_btn.set_style_pad_column(SEG_PAD, 0)
        except BaseException:
            pass

        bar_font = _font("font_montserrat_20", "font_montserrat_16", "font_montserrat_14")

        def _seg(text, color):
            lab = lv.label(self.stat_btn)
            lab.set_text(text)
            lab.set_width(lv.SIZE_CONTENT)
            lab.set_style_text_color(lv.color_hex(color), 0)
            if bar_font is not None:
                lab.set_style_text_font(bar_font, 0)
            # clicks must reach the strip container, otherwise taps on the padding
            # outside the small text block are lost
            lab.clear_flag(lv.obj.FLAG.CLICKABLE)
            return lab

        self.stat_model = _seg("loading", COL_MODEL)     # model name
        self.stat_sep1 = _seg("|", COL_SEP)
        self.stat_fps = _seg("FPS -", COL_FPS)           # frame rate
        self.stat_sep2 = _seg("|", COL_SEP)
        self.stat_objs = _seg("obj -", COL_OBJS_IDLE)    # object count
        self.stat_hint = _seg("TAP to switch", COL_HINT)  # hint / frozen / error

        # kept for older references: error paths write straight into this segment
        self.stat_label = self.stat_model

        # ---- AI pipeline (loaded during construction, ~0.6 s) ----
        self._init_ai()

    def _init_ai(self):
        t0 = time.ticks_ms()
        # A missing camera makes Sensor raise during init; convert it to
        # AppUnavailable so the launcher shows the camera-unavailable panel.
        # Model load failures keep the plain exception, so the two stay distinct.
        sensor = None
        try:
            sensor = Sensor(fps=30)
            sensor.reset()
            # chn1 = AI input, must be RGBP888 (planar, to_numpy_ref() yields CHW).
            # ai2d declares NCHW_FMT; feeding packed HWC RGB888 frames makes ai2d
            # read HWC memory as NCHW, so the model sees garbage (measured: scores
            # stuck at ~0.006 where the correct NCHW frame of the same image
            # yields 0.95). Only RGBP888 matches the declaration.
            sensor.set_framesize(w=PREVIEW_W, h=PREVIEW_H, chn=CAM_CHN_ID_1, crop=True)
            sensor.set_pixformat(Sensor.RGBP888, chn=CAM_CHN_ID_1)
            # chn2 = preview display, packed RGB888 (as_lvgl_img_src only takes packed).
            sensor.set_framesize(w=PREVIEW_W, h=PREVIEW_H, chn=CAM_CHN_ID_2, crop=True)
            sensor.set_pixformat(Sensor.RGB888, chn=CAM_CHN_ID_2)
            # chn0 = full-resolution still channel (reserved for V3; configured,
            # not captured)
            sensor.set_framesize(w=1920, h=1080, chn=CAM_CHN_ID_0)
            sensor.set_pixformat(Sensor.RGB888, chn=CAM_CHN_ID_0)
            sensor.run()
        except BaseException as error:
            print("[Vision] sensor init failed: {}".format(error))
            sys.print_exception(error)
            close_sensor(sensor)
            raise AppUnavailable(CAMERA_UNAVAILABLE)
        self.sensor = sensor

        self._load_model(self.model_idx)

        load_ms = time.ticks_diff(time.ticks_ms(), t0)
        print("[Vision] ready: model={} load={}ms mem_free={}".format(
            self.cfg["id"], load_ms, gc.mem_free()))
        self._update_stat()

    def _release_model(self):
        """Release kpu + ai2d. This must happen before a hot switch, otherwise
        native memory piles up and the board dies."""
        if self.kpu is not None:
            try:
                self.kpu.release()
            except BaseException as e:
                print("[Vision]   kpu.release error:", e)
            self.kpu = None
        if self.ai2d is not None:
            try:
                self.ai2d.deinit()
            except BaseException as e:
                print("[Vision]   ai2d.deinit error:", e)
            self.ai2d = None
        gc.collect()

    def _load_model(self, idx):
        """Load model idx from the table: kpu plus the letterbox for its input size.

        A different input size changes both the ai2d pad amount and the build
        shapes, so switching models cannot just reload the kmodel: ai2d has to be
        rebuilt.
        """
        self.model_idx = idx
        self.cfg = MODELS[idx]
        mw, mh = self.cfg["input"]
        _scale, pad_top = letterbox(self.cfg)
        self.y_off = y_pad_offset(self.cfg)

        kpu = nn.kpu()
        kpu.load_kmodel(self.cfg["path"])
        self.kpu = kpu

        ai2d = Ai2d()
        ai2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                           np.uint8, np.uint8)
        # The pad acts in the output space after scaling (verified with a geometry
        # probe); left/right stay 0 because the 5:3 preview is wider than the
        # square model, so width saturates first and grey bands appear only
        # vertically.
        ai2d.pad([0, 0, 0, 0, pad_top, pad_top, 0, 0], 0, [128, 128, 128])
        ai2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
        ai2d.build([1, 3, PREVIEW_H, PREVIEW_W], [1, 3, mh, mw])
        self.ai2d = ai2d
        print("[Vision] model loaded: {} in={}x{} pad_top={} y_off={} mem_free={}".format(
            self.cfg["id"], mw, mh, pad_top, self.y_off, gc.mem_free()))

    def switch_model(self, idx):
        """Hot-switch the model at runtime. On failure fall back to the previous
        model so the picture keeps running."""
        if idx == self.model_idx:
            return
        old = self.model_idx
        t0 = time.ticks_ms()
        # clear the previous model's boxes so nothing stale survives the switch
        for i in range(BOX_POOL):
            self.box_objs[i].add_flag(lv.obj.FLAG.HIDDEN)
            self.box_labels[i].add_flag(lv.obj.FLAG.HIDDEN)
        self.last_objs = 0
        self._release_model()
        try:
            self._load_model(idx)
        except BaseException as error:
            print("[Vision] switch failed:", error)
            sys.print_exception(error)
            self._release_model()
            try:
                self._load_model(old)
            except BaseException as e2:
                print("[Vision] rollback failed:", e2)
                self._show_error("model load failed")
                return
        self.fps_text = "-"
        self.frame_count = 0
        self.last_fps_ms = time.ticks_ms()
        self._update_stat()
        print("[Vision] switched {} -> {} in {}ms".format(
            MODELS[old]["id"], self.cfg["id"],
            time.ticks_diff(time.ticks_ms(), t0)))

    def _infer(self, img):
        """One frame: ai2d -> kpu -> aidemo. img must come from the RGBP888
        channel (CHW).

        The two families have different output layouts and must be handled
        separately:
          yolov8: (1, 4+ncls, anchors) -- has to be transposed to (anchors, 4+ncls)
          yolo26: (1, 300, 6) end to end -- already (N,6)=[x1,y1,x2,y2,score,class];
                  transposing it destroys the layout

        Memory discipline: release the native tensor right after to_numpy().
        aidemo's C layer needs contiguous memory, so .copy() is mandatory (a view
        makes it read the coordinate rows as scores).
        No whole-tensor sigmoid: it lifts noise to 0.5 and fills the screen with
        false positives.
        """
        cfg = self.cfg
        mw, mh = cfg["input"]
        ncls = len(cfg["labels"])
        tin = self.ai2d.run(img.to_numpy_ref())
        self.kpu.set_input_tensor(0, tin)
        self.kpu.run()
        ot = self.kpu.get_output_tensor(0)
        try:
            raw = ot.to_numpy()
        finally:
            ot.release()                 # native tensors must be released (a leak crashes the board)

        if cfg["post"] == "yolo26":
            grid = raw[0].copy()          # (300,6) end to end, do not transpose
            score_max = float(np.max(grid[:, 4]))
        else:
            # .copy() is mandatory: transpose only rewrites strides and the
            # backing memory keeps the original layout, so feeding the view makes
            # the scores read as coordinate rows (measured: junk boxes at
            # score ~377).
            grid = raw[0].transpose().copy()   # (anchors, 4+ncls)
            score_max = float(np.max(grid[:, 4:]))

        # The score column holds raw values (measured to go negative, so not
        # probabilities). aidemo compares the raw value against conf directly:
        # blank-screen noise ~0.02 is filtered out, and a clear portrait measured
        # 0.79~0.88. Never sigmoid first: sigmoid(0)=0.5 turns noise into boxes
        # all over the screen (measured).
        if score_max < cfg["conf"]:
            del raw, grid
            return None
        res = self._postprocess(grid, cfg, mw, mh, ncls)
        del raw, grid
        return res

    def _postprocess(self, grid, cfg, mw, mh, ncls):
        """Dispatch on the table's post field to the matching aidemo family.

        The signatures differ (measured):
          yolov8_det_postprocess(data, rgb, model, disp, ncls, conf, nms, max)
          yolo26_det_postprocess(data, rgb, model, disp, ncls, conf, max)  -- no nms
        """
        post = cfg["post"]
        if post == "yolov8":
            return aidemo.yolov8_det_postprocess(
                grid, [PREVIEW_H, PREVIEW_W], [mw, mh], [PREVIEW_H, PREVIEW_W],
                ncls, cfg["conf"], cfg["nms"], MAX_BOXES)
        if post == "yolo26":
            return aidemo.yolo26_det_postprocess(
                grid, [PREVIEW_H, PREVIEW_W], [mw, mh], [PREVIEW_H, PREVIEW_W],
                ncls, cfg["conf"], MAX_BOXES)
        raise ValueError("unsupported post: {}".format(post))

    def _show_boxes(self, dets):
        """Map detections onto the box pool. aidemo's x/w/h already live in the
        800x480 display domain, but y still contains the letterbox top padding,
        so self.y_off has to be subtracted here."""
        labels = self.cfg["labels"]
        n = 0
        if dets:
            n = min(len(dets[0]), BOX_POOL)
        for i in range(BOX_POOL):
            box = self.box_objs[i]
            lab = self.box_labels[i]
            if i >= n:
                box.add_flag(lv.obj.FLAG.HIDDEN)
                lab.add_flag(lv.obj.FLAG.HIDDEN)
                continue
            x, y, w, h = dets[0][i]
            x = int(x)
            y = int(y) - self.y_off
            w = int(w)
            h = int(h)
            if w <= 0 or h <= 0:
                box.add_flag(lv.obj.FLAG.HIDDEN)
                lab.add_flag(lv.obj.FLAG.HIDDEN)
                continue
            cls_id = int(dets[1][i])
            color = lv.color_hex(CLASS_COLORS[cls_id % len(CLASS_COLORS)])
            box.clear_flag(lv.obj.FLAG.HIDDEN)
            box.set_pos(x, y)
            box.set_size(w, h)
            box.set_style_border_color(color, 0)
            name = labels[cls_id] if cls_id < len(labels) else str(cls_id)
            # aidemo returns raw logits (filtering already used the raw values).
            # Only the kept boxes get a sigmoid for display so the label reads
            # 0..1; the cost is negligible.
            s = float(dets[2][i])
            prob = 1.0 / (1.0 + math.exp(-s)) if s < 30.0 else 1.0
            lab.set_text("{} {:.2f}".format(name, prob))
            lab.set_style_text_color(color, 0)
            lab.set_style_bg_color(lv.color_hex(0x000000), 0)
            lab.clear_flag(lv.obj.FLAG.HIDDEN)
            lab.align_to(box, lv.ALIGN.TOP_LEFT, 0, label_dy(y))

    def _print_dets(self, dets):
        """Print this frame's detections to serial, throttled by DET_PRINT_MS.

        Same data source and same conversion as the screen (y minus y_off, and the
        score sigmoid applied for display exactly like the label) so what is
        printed matches what is seen. Nothing is printed without detections, to
        keep an empty scene from flooding the log. One line per frame, multiple
        objects separated by " | ".
        """
        now = time.ticks_ms()
        if time.ticks_diff(now, self._det_print_ms) < DET_PRINT_MS:
            return
        self._det_print_ms = now
        if not dets:
            return
        labels = self.cfg["labels"]
        n = min(len(dets[0]), BOX_POOL)
        parts = []
        for i in range(n):
            x, y, w, h = dets[0][i]
            cls_id = int(dets[1][i])
            name = labels[cls_id] if cls_id < len(labels) else str(cls_id)
            s = float(dets[2][i])
            prob = 1.0 / (1.0 + math.exp(-s)) if s < 30.0 else 1.0
            parts.append("{} {:.2f} [{},{},{}x{}]".format(
                name, prob, int(x), int(y) - self.y_off, int(w), int(h)))
        print("[Vision] {}".format(" | ".join(parts)))

    def _update_stat(self):
        """Refresh the bar segments; each one owns its own text and colour.

        The obj segment is visually demoted: grey without objects, sky blue once
        something is detected, otherwise "obj 0" looks like a real detection.
        """
        self.stat_model.set_text(self.cfg["name"])
        self.stat_model.set_style_text_color(lv.color_hex(COL_MODEL), 0)
        self.stat_fps.set_text("FPS {}".format(self.fps_text))
        self.stat_objs.set_text("obj {}".format(self.last_objs))
        self.stat_objs.set_style_text_color(
            lv.color_hex(COL_OBJS if self.last_objs > 0 else COL_OBJS_IDLE), 0)
        if self.frozen:
            self.stat_hint.set_text("FROZEN")
            self.stat_hint.set_style_text_color(lv.color_hex(COL_FROZEN), 0)
        else:
            self.stat_hint.set_text("TAP to switch")
            self.stat_hint.set_style_text_color(lv.color_hex(COL_HINT), 0)

    def _show_error(self, text):
        """Errors take over the hint segment in red; never the model name, or it
        looks like the model changed."""
        self.stat_hint.set_text(text)
        self.stat_hint.set_style_text_color(lv.color_hex(COL_ERROR), 0)

    # ---------- model picker overlay ----------
    def _close_picker(self):
        """Close the overlay and unfreeze. Every close path must unfreeze,
        otherwise the picture stalls on the last frame."""
        if self.picker is not None:
            try:
                self.picker.delete()
            except BaseException as e:
                print("[Vision] picker delete error:", e)
            self.picker = None
            print("[Vision] picker closed")
        self.frozen = False
        self._update_stat()

    def _open_picker(self):
        """Open the model list. Idempotent: returns immediately if already open.

        Two layers, a full-screen backdrop plus a centred panel: the backdrop
        covers the screen and is clickable (tap to close), the panel sits on top
        and swallows clicks. This is sturdier than one small panel, whose hit test
        is easily disturbed by the img widget underneath and which offers no
        obvious area to tap for closing.
        """
        if self.picker is not None:
            return
        print("[Vision] picker open")
        self.frozen = True
        self._update_stat()

        # backdrop: covers the whole screen, tap outside to close
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

        # panel
        panel = lv.obj(backdrop)
        panel.set_size(560, 76 + len(MODELS) * 58)
        panel.align(lv.ALIGN.CENTER, 0, 0)
        panel.set_style_bg_color(lv.color_hex(0x101418), 0)
        panel.set_style_bg_opa(lv.OPA.COVER, 0)
        panel.set_style_border_width(2, 0)
        panel.set_style_border_color(lv.color_hex(0x3A4650), 0)
        panel.set_style_radius(14, 0)
        panel.set_style_pad_all(12, 0)
        panel.clear_flag(lv.obj.FLAG.SCROLLABLE)
        panel.add_flag(lv.obj.FLAG.CLICKABLE)   # swallow clicks instead of closing

        title = lv.label(panel)
        title.set_text("Select model")
        title.set_style_text_color(lv.color_hex(0xE6EAF0), 0)
        title.align(lv.ALIGN.TOP_MID, 0, 2)

        hint = lv.label(panel)
        hint.set_text("tap a row to switch, tap outside to close")
        hint.set_style_text_color(lv.color_hex(0x8A97A3), 0)
        hint.align(lv.ALIGN.TOP_MID, 0, 24)

        font = _font("font_montserrat_16", "font_montserrat_14")
        y = 48
        for idx, cfg in enumerate(MODELS):
            row = lv.btn(panel)
            row.set_size(524, 50)
            row.align(lv.ALIGN.TOP_MID, 0, y)
            row.set_style_radius(10, 0)
            row.set_style_border_width(2 if idx == self.model_idx else 0, 0)
            row.set_style_border_color(lv.color_hex(0x00C853), 0)
            row.set_style_bg_color(lv.color_hex(0x1E262E), 0)
            row.set_style_bg_opa(lv.OPA.COVER, 0)
            row.set_style_pad_all(0, 0)
            row.clear_flag(lv.obj.FLAG.SCROLLABLE)
            row.add_flag(lv.obj.FLAG.CLICKABLE)
            lab = lv.label(row)
            mark = "> " if idx == self.model_idx else "  "
            mw, mh = cfg["input"]
            lab.set_text("{}{}  ({}x{}, {} cls)".format(
                mark, cfg["name"], mw, mh, len(cfg["labels"])))
            lab.set_style_text_color(
                lv.color_hex(0x00C853 if idx == self.model_idx else 0xE6EAF0), 0)
            if font is not None:
                lab.set_style_text_font(font, 0)
            lab.center()
            lab.clear_flag(lv.obj.FLAG.CLICKABLE)   # let the click land on the row
            beep.on_click(row, lambda e, i=idx: self._pick(i))
            y += 58

        self.picker = backdrop

    def _pick(self, idx):
        self._close_picker()      # unfreezes and refreshes the bar internally
        if idx != self.model_idx:
            self.switch_model(idx)

    def step(self):
        if self.sensor is None:
            return

        # OK key: close the overlay if open, else open the model list (fallback
        # entry point when touch fails)
        if self.ctx.take_ok():
            if self.picker is not None:
                self._close_picker()
            else:
                self._open_picker()

        if self.frozen:
            return

        try:
            # The AI frame comes from the RGBP888 channel (CHW, matching ai2d's
            # NCHW declaration); the display frame comes from the RGB888 channel
            # (packed, as as_lvgl_img_src requires).
            ai_img = self.sensor.snapshot(chn=CAM_CHN_ID_1)
            img = self.sensor.snapshot(chn=CAM_CHN_ID_2)
        except BaseException as error:
            self._show_error("snapshot error")
            # consecutive failures reach the threshold -> the camera is gone, let
            # the launcher raise its panel (Cancel returns to the desktop)
            self._no_signal.on_fail(self.ctx)
            return
        self._no_signal.on_ok()

        dets = None
        try:
            dets = self._infer(ai_img)
        except BaseException as error:
            print("[Vision] infer error:", error)
            sys.print_exception(error)

        # preview composite (keep showing frames even if inference failed)
        try:
            img.as_lvgl_img_src(self.img_widget)
            self.img_widget.invalidate()
        except BaseException as error:
            self._show_error("display error")
            return

        self._show_boxes(dets)
        self.last_objs = len(dets[0]) if dets else 0
        self._print_dets(dets)

        # LVGL img sources and sensor frames are cyclic garbage that reference
        # counting never frees. Without a periodic collect the contiguous heap
        # fills with unreclaimed buffers and to_numpy starts failing every few
        # dozen frames.
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
        print("[Vision] destroy begin")
        self._close_picker()
        self._release_model()
        print("[Vision]   model released")
        if self.sensor is not None:
            try:
                self.sensor.stop()
                print("[Vision]   sensor.stop ok")
            except BaseException as e:
                print("[Vision]   sensor.stop error:", e)
            try:
                self.sensor.deinit()
                print("[Vision]   sensor.deinit ok")
            except BaseException as e:
                print("[Vision]   sensor.deinit error:", e)
            self.sensor = None
        gc.collect()
        print("[Vision] destroy done | mem_free={}".format(gc.mem_free()))


def create_app(content, ctx):
    return VisionApp(content, ctx)
