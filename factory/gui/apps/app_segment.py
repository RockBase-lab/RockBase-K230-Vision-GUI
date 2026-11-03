# Segment: live instance segmentation (yolov8n_seg_320, COCO 80 classes).
#
# aidemo.segment_postprocess fills a caller-owned 800x480 ARGB8888 mask and
# returns boxes, class ids and scores. Output 0 is (1,116,2100): four box rows,
# 80 class rows and 32 mask coefficients; pass every output untransposed.
#
# chn1 and chn2 must both use the 800x480 preview crop. The 800x480 input is
# letterboxed to 320x320 by ai2d; segment_postprocess removes that padding.
# A square sensor channel uses a different crop and misaligns masks by ~2.8x.
# The persistent mask costs 1.5 MB; see docs/pitfalls.md before snapshot probes.

import gc
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
import model_pool

PREVIEW_W = 800
PREVIEW_H = 480
MAX_BOXES = 12
BOX_POOL = MAX_BOXES
TOPBAR_H = 56
TOPBAR_Y = 10
TOPBAR_LEFT = 8
TOPBAR_RIGHT = 8
LAB_H = 18
DET_PRINT_MS = 200

COL_MODEL = 0x00E5A0
COL_FPS = 0xFFC857
COL_OBJS = 0x64B5F6
COL_OBJS_IDLE = 0x6B7885
COL_SEP = 0x3A4650
COL_HINT = 0x7A8794
COL_FROZEN = 0xFF8A65
COL_ERROR = 0xFF6B6B
SEG_PAD = 18

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

MODEL_PATH = "/sdcard/examples/kmodel/yolov8n_seg_320.kmodel"
MODEL_INPUT = [320, 320]
# 800x480 -> 320x192 at scale 0.4, so the letterbox pad is vertical only.
PAD_Y = (MODEL_INPUT[1] - PREVIEW_H * MODEL_INPUT[0] // PREVIEW_W) // 2
CONF = 0.70
NMS = 0.45
MASK_TH = 0.50


def _font(*names):
    for name in names:
        font = getattr(lv, name, None)
        if font:
            return font
    return None


def label_dy(box_y):
    return -LAB_H if box_y >= LAB_H else 1


class SegmentApp:
    def __init__(self, content, ctx):
        self.ctx = ctx
        self.content = content
        self.sensor = None
        self.kpu = None
        self.ai2d = None
        self.masks = None          # numpy view over _mask_buf, aidemo writes here
        self._mask_buf = None      # bytearray backing the ARGB pixels
        self._mask_dsc = None      # persistent lv.img_dsc_t over _mask_buf
        self.frozen = False
        self.frame_count = 0
        self.last_fps_ms = time.ticks_ms()
        self.fps_text = "-"
        self.last_objs = 0
        self._gc_tick = 0
        self._det_print_ms = 0
        self._no_signal = NoSignalCounter()

        self.img_w = ctx.width
        self.img_h = ctx.height
        # preview + mask are PREVIEW_W x PREVIEW_H, centred on the screen.
        self.off_x = (self.img_w - PREVIEW_W) // 2
        self.off_y = (self.img_h - PREVIEW_H) // 2

        self.img_widget = lv.img(content)
        self.img_widget.set_size(PREVIEW_W, PREVIEW_H)
        self.img_widget.align(lv.ALIGN.TOP_LEFT, self.off_x, self.off_y)

        # mask overlay: same rect, stacked above the preview. Hidden until the
        # first frame fills it.
        self.mask_widget = lv.img(content)
        self.mask_widget.set_size(PREVIEW_W, PREVIEW_H)
        self.mask_widget.align(lv.ALIGN.TOP_LEFT, self.off_x, self.off_y)
        self.mask_widget.add_flag(lv.obj.FLAG.HIDDEN)

        self.box_labels = []
        label_font = _font("font_montserrat_14", "font_montserrat_12")
        for _ in range(BOX_POOL):
            lab = lv.label(content)
            lab.add_flag(lv.obj.FLAG.HIDDEN)
            lab.set_style_bg_opa(lv.OPA._80, 0)
            lab.set_style_bg_color(lv.color_hex(0x000000), 0)
            lab.set_style_text_color(lv.color_hex(COL_MODEL), 0)
            lab.set_style_pad_hor(4, 0)
            lab.set_style_pad_ver(1, 0)
            lab.set_style_radius(3, 0)
            if label_font is not None:
                lab.set_style_text_font(label_font, 0)
            self.box_labels.append(lab)

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
        beep.on_click(self.stat_btn, lambda e: self._toggle_freeze())
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
            lab.clear_flag(lv.obj.FLAG.CLICKABLE)
            return lab

        self.stat_model = _seg("loading", COL_MODEL)
        self.stat_sep1 = _seg("|", COL_SEP)
        self.stat_fps = _seg("FPS -", COL_FPS)
        self.stat_sep2 = _seg("|", COL_SEP)
        self.stat_objs = _seg("obj -", COL_OBJS_IDLE)
        self.stat_hint = _seg("TAP to freeze", COL_HINT)
        self.stat_label = self.stat_model

        self._init_ai()

    def _init_ai(self):
        t0 = time.ticks_ms()
        sensor = None
        try:
            sensor = Sensor(fps=30)
            sensor.reset()
            # Both channels share the crop window so overlays map by output
            # size alone; chn1 is RGBP888 because ai2d declares NCHW input.
            set_preview(sensor, CAM_CHN_ID_1, PREVIEW_W, PREVIEW_H)
            sensor.set_pixformat(Sensor.RGBP888, chn=CAM_CHN_ID_1)
            set_preview(sensor, CAM_CHN_ID_2, PREVIEW_W, PREVIEW_H)
            sensor.set_pixformat(Sensor.RGB888, chn=CAM_CHN_ID_2)
            sensor.run()
        except BaseException as error:
            print("[Segment] sensor init failed: {}".format(error))
            sys.print_exception(error)
            close_sensor(sensor)
            raise AppUnavailable(CAMERA_UNAVAILABLE)
        self.sensor = sensor

        mw, mh = MODEL_INPUT
        self.kpu = model_pool.get(MODEL_PATH)
        ai2d = Ai2d()
        ai2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                           np.uint8, np.uint8)
        ai2d.pad([0, 0, 0, 0, PAD_Y, PAD_Y, 0, 0], 0, [114, 114, 114])
        ai2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
        ai2d.build([1, 3, PREVIEW_H, PREVIEW_W], [1, 3, mh, mw])
        self.ai2d = ai2d

        # masks buffer: display-sized ARGB, reused every frame; aidemo fills it.
        # A bytearray backs it so one persistent lv.img_dsc_t can point straight
        # at the pixels (as_lvgl_img_src rejects ARGB, and a dsc built per frame
        # would copy 1.5 MB). The numpy view is what aidemo writes through; both
        # share the same memory, and the buffer is never resized so the dsc's
        # data pointer stays valid across frames (measured: frombuffer view is
        # writable and a bytearray is accepted as dsc "data", an ndarray is not).
        try:
            self._mask_buf = bytearray(PREVIEW_W * PREVIEW_H * 4)
            self.masks = np.frombuffer(self._mask_buf, dtype=np.uint8).reshape(
                (1, PREVIEW_H, PREVIEW_W, 4))
        except BaseException as error:
            print("[Segment] masks alloc failed: {}".format(error))
            close_sensor(sensor)
            self.sensor = None
            raise AppUnavailable("Segmentation buffer too large for this build.")
        self._mask_dsc = lv.img_dsc_t({
            "header": {"cf": lv.COLOR_FORMAT.ARGB8888,
                       "w": PREVIEW_W, "h": PREVIEW_H},
            "data_size": len(self._mask_buf), "data": self._mask_buf})

        load_ms = time.ticks_diff(time.ticks_ms(), t0)
        print("[Segment] ready: load={}ms mem_free={}".format(
            load_ms, gc.mem_free()))
        self._update_stat()

    def _infer(self, img):
        mw, mh = MODEL_INPUT
        tin = self.ai2d.run(img.to_numpy_ref())
        self.kpu.set_input_tensor(0, tin)
        self.kpu.run()
        # This kmodel has TWO outputs: (1,116,2100) box/score/coeff plus the
        # mask prototypes. segment_postprocess takes the whole results list --
        # passing only [out0] fails with "list is too short" (measured).
        outs = []
        for i in range(self.kpu.outputs_size()):
            ot = self.kpu.get_output_tensor(i)
            try:
                outs.append(ot.to_numpy())
            finally:
                ot.release()
        # Rows 4..83 of output 0 are the 80 class scores (yolov8 has no
        # objectness row); the pre-filter skips postprocess on empty frames.
        score_max = float(np.max(outs[0][0][4:84]))
        if score_max < CONF:
            del outs
            return None
        # rgb_size is the chn1 frame; the helper removes the ai2d letterbox
        # pad and maps boxes and masks into the display rect.
        res = aidemo.segment_postprocess(
            outs, [PREVIEW_H, PREVIEW_W], MODEL_INPUT,
            [PREVIEW_H, PREVIEW_W], CONF, NMS, MASK_TH, self.masks)
        del outs
        return res

    def _show(self, res):
        # res = (boxes, class_ids, scores); segment_postprocess already un-padded
        # and mapped boxes to [x,y,w,h] in the preview rect.
        n = 0
        if res and res[0]:
            n = min(len(res[0]), BOX_POOL)
        if n > 0:
            self.mask_widget.clear_flag(lv.obj.FLAG.HIDDEN)
            self.mask_widget.set_src(self._mask_dsc)
            self.mask_widget.invalidate()
        else:
            self.mask_widget.add_flag(lv.obj.FLAG.HIDDEN)

        for i in range(BOX_POOL):
            lab = self.box_labels[i]
            if i >= n:
                lab.add_flag(lv.obj.FLAG.HIDDEN)
                continue
            x, y, w, h = res[0][i]
            x = int(x) + self.off_x
            y = int(y) + self.off_y
            cls_id = int(res[1][i])
            name = COCO[cls_id] if cls_id < len(COCO) else str(cls_id)
            lab.set_text("{} {:.2f}".format(name, float(res[2][i])))
            lab.clear_flag(lv.obj.FLAG.HIDDEN)
            lab.set_pos(max(0, x), max(0, y + label_dy(y)))
        return n

    def _print_dets(self, res, n):
        now = time.ticks_ms()
        if time.ticks_diff(now, self._det_print_ms) < DET_PRINT_MS:
            return
        self._det_print_ms = now
        if not res or n == 0:
            return
        parts = []
        for i in range(n):
            cls_id = int(res[1][i])
            name = COCO[cls_id] if cls_id < len(COCO) else str(cls_id)
            x, y, w, h = res[0][i]
            parts.append("{} {:.2f} [{},{},{}x{}]".format(
                name, float(res[2][i]), int(x), int(y), int(w), int(h)))
        print("[Segment] {}".format(" | ".join(parts)))

    def _update_stat(self):
        self.stat_model.set_text("Seg v8n")
        self.stat_model.set_style_text_color(lv.color_hex(COL_MODEL), 0)
        self.stat_fps.set_text("FPS {}".format(self.fps_text))
        self.stat_objs.set_text("obj {}".format(self.last_objs))
        self.stat_objs.set_style_text_color(
            lv.color_hex(COL_OBJS if self.last_objs > 0 else COL_OBJS_IDLE), 0)
        if self.frozen:
            self.stat_hint.set_text("FROZEN")
            self.stat_hint.set_style_text_color(lv.color_hex(COL_FROZEN), 0)
        else:
            self.stat_hint.set_text("TAP to freeze")
            self.stat_hint.set_style_text_color(lv.color_hex(COL_HINT), 0)

    def _show_error(self, text):
        self.stat_hint.set_text(text)
        self.stat_hint.set_style_text_color(lv.color_hex(COL_ERROR), 0)

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
            self._show_error("snapshot error")
            self._no_signal.on_fail(self.ctx)
            return
        self._no_signal.on_ok()

        res = None
        try:
            res = self._infer(ai_img)
        except BaseException as error:
            print("[Segment] infer error:", error)
            sys.print_exception(error)

        try:
            img.as_lvgl_img_src(self.img_widget)
            self.img_widget.invalidate()
        except BaseException as error:
            self._show_error("display error")
            return

        n = self._show(res)
        self.last_objs = n
        self._print_dets(res, n)

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
        print("[Segment] destroy begin")
        self.kpu = None      # pooled: never release
        if self.ai2d is not None:
            try:
                self.ai2d.deinit()
            except BaseException as e:
                print("[Segment]   ai2d.deinit error:", e)
            self.ai2d = None
        self.masks = None
        self._mask_buf = None
        self._mask_dsc = None
        if self.sensor is not None:
            try:
                self.sensor.stop()
            except BaseException as e:
                print("[Segment]   sensor.stop error:", e)
            try:
                self.sensor.deinit()
            except BaseException as e:
                print("[Segment]   sensor.deinit error:", e)
            self.sensor = None
        gc.collect()
        print("[Segment] destroy done | mem_free={}".format(gc.mem_free()))


def create_app(content, ctx):
    return SegmentApp(content, ctx)
