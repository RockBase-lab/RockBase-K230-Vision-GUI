# Shared shell for anchor-based YOLOv5 detection apps (Person, Fall, Gesture).
#
# These three models share one pipeline that differs from the yolov8 family in
# app_vision: the kmodel emits three output tensors (strides 8/16/32) and the
# decode is aicube.anchorbasedet_post_process, not an aidemo yolov8 helper. The
# class here owns the sensor, ai2d, box overlay and step loop; each app file
# supplies only its model path, labels, anchors and thresholds.
#
# Coordinate space: anchorbasedet_post_process is handed both the model input
# size and the AI frame size, so it reverses the letterbox itself and returns
# boxes already in the AI-frame domain. Because the AI frame equals the screen
# (800x480), no extra offset is applied here -- unlike app_vision, whose aidemo
# yolov8 path leaves the letterbox top padding in y and needs a manual y_off.
#
# Each detection is [cls_id, score, x1, y1, x2, y2]; the score is already a
# probability (the anchor decode applies sigmoid), so no display-time sigmoid.
#
# Native tensor discipline: every kpu output tensor is release()d right after
# to_numpy(), matching the rest of the GUI (a missed release leaks native memory
# and drops the serial link).

import gc
import sys
import time

import lvgl as lv
import ulab.numpy as np
import nncase_runtime as nn
import aicube
from libs.AI2D import Ai2d
from media.sensor import Sensor, CAM_CHN_ID_1, CAM_CHN_ID_2
from ui import beep
from core.errors import (AppUnavailable, CAMERA_UNAVAILABLE, NoSignalCounter,
                       close_sensor)
from hardware.camera_fov import set_preview
from core import model_pool

PREVIEW_W = 800               # capture size shared by preview and AI (= screen)
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

CLASS_COLORS = [0x7FB6D9, 0xC99AAE, 0x9C92C9, 0x6FB8A4,
                0xC9AE7A, 0x7FA6CC, 0xA9BE7C, 0xCC8A92]


def _font(*names):
    for name in names:
        font = getattr(lv, name, None)
        if font:
            return font
    return None


def label_dy(box_y):
    """Label offset above the box, or pushed inside when it would clip the top."""
    return -LAB_H if box_y >= LAB_H else 1


class AnchorDetApp:
    """Live anchor-based detection. cfg keys: name, path, input [w,h], labels,
    anchors, strides, conf, nms, and optional min_h_frac to drop tiny boxes."""

    def __init__(self, content, ctx, cfg, tag):
        self.ctx = ctx
        self.content = content
        self.cfg = cfg
        self.tag = tag
        self.sensor = None
        self.kpu = None
        self.ai2d = None
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

        self.img_widget = lv.img(content)
        self.img_widget.set_size(self.img_w, self.img_h)
        self.img_widget.align(lv.ALIGN.CENTER, 0, 0)

        self.box_objs = []
        self.box_labels = []
        label_font = _font("font_montserrat_14", "font_montserrat_12")
        for _ in range(BOX_POOL):
            box = lv.obj(content)
            box.set_style_bg_opa(lv.OPA.TRANSP, 0)
            box.set_style_border_width(3, 0)
            box.set_style_radius(4, 0)
            box.clear_flag(lv.obj.FLAG.SCROLLABLE)
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
            # chn1 = AI input, RGBP888 (planar CHW) to match ai2d's NCHW declaration.
            set_preview(sensor, CAM_CHN_ID_1, PREVIEW_W, PREVIEW_H)
            sensor.set_pixformat(Sensor.RGBP888, chn=CAM_CHN_ID_1)
            # chn2 = preview display, packed RGB888 for as_lvgl_img_src.
            set_preview(sensor, CAM_CHN_ID_2, PREVIEW_W, PREVIEW_H)
            sensor.set_pixformat(Sensor.RGB888, chn=CAM_CHN_ID_2)
            sensor.run()
        except BaseException as error:
            print("[{}] sensor init failed: {}".format(self.tag, error))
            sys.print_exception(error)
            close_sensor(sensor)
            raise AppUnavailable(CAMERA_UNAVAILABLE)
        self.sensor = sensor

        mw, mh = self.cfg["input"]
        self.kpu = model_pool.get(self.cfg["path"])
        scale = min(float(mw) / PREVIEW_W, float(mh) / PREVIEW_H)
        pad_top = int((mh - int(PREVIEW_H * scale)) / 2)
        ai2d = Ai2d()
        ai2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                           np.uint8, np.uint8)
        # Centre pad: the 5:3 frame is wider than the square model, so only
        # top/bottom pad. anchorbasedet_post_process reverses this internally.
        ai2d.pad([0, 0, 0, 0, pad_top, pad_top, 0, 0], 0, [0, 0, 0])
        ai2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
        ai2d.build([1, 3, PREVIEW_H, PREVIEW_W], [1, 3, mh, mw])
        self.ai2d = ai2d

        load_ms = time.ticks_diff(time.ticks_ms(), t0)
        print("[{}] ready: model={} load={}ms mem_free={}".format(
            self.tag, self.cfg["name"], load_ms, gc.mem_free()))
        self._update_stat()

    def _infer(self, img):
        """ai2d -> kpu (3 outputs) -> aicube.anchorbasedet_post_process."""
        cfg = self.cfg
        tin = self.ai2d.run(img.to_numpy_ref())
        self.kpu.set_input_tensor(0, tin)
        self.kpu.run()
        n_out = self.kpu.outputs_size()
        results = []
        for i in range(n_out):
            ot = self.kpu.get_output_tensor(i)
            try:
                results.append(ot.to_numpy())
            finally:
                ot.release()
        dets = aicube.anchorbasedet_post_process(
            results[0], results[1], results[2], cfg["input"],
            [PREVIEW_W, PREVIEW_H], cfg["strides"], len(cfg["labels"]),
            cfg["conf"], cfg["nms"], cfg["anchors"], False)
        return dets

    def _keep(self, det):
        """Drop boxes too small to be the target (edge slivers, far-away specks)."""
        min_h_frac = self.cfg.get("min_h_frac")
        if min_h_frac is None:
            return True
        y1, y2 = det[3], det[5]
        return (y2 - y1) >= min_h_frac * PREVIEW_H

    def _show_boxes(self, dets):
        labels = self.cfg["labels"]
        kept = []
        if dets:
            for det in dets:
                if self._keep(det):
                    kept.append(det)
                if len(kept) >= BOX_POOL:
                    break
        n = len(kept)
        for i in range(BOX_POOL):
            box = self.box_objs[i]
            lab = self.box_labels[i]
            if i >= n:
                box.add_flag(lv.obj.FLAG.HIDDEN)
                lab.add_flag(lv.obj.FLAG.HIDDEN)
                continue
            det = kept[i]
            x1, y1, x2, y2 = int(det[2]), int(det[3]), int(det[4]), int(det[5])
            w = x2 - x1
            h = y2 - y1
            if w <= 0 or h <= 0:
                box.add_flag(lv.obj.FLAG.HIDDEN)
                lab.add_flag(lv.obj.FLAG.HIDDEN)
                continue
            cls_id = int(det[0])
            color = lv.color_hex(CLASS_COLORS[cls_id % len(CLASS_COLORS)])
            box.clear_flag(lv.obj.FLAG.HIDDEN)
            box.set_pos(x1, y1)
            box.set_size(w, h)
            box.set_style_border_color(color, 0)
            name = labels[cls_id] if cls_id < len(labels) else str(cls_id)
            lab.set_text("{} {:.2f}".format(name, float(det[1])))
            lab.set_style_text_color(color, 0)
            lab.set_style_bg_color(lv.color_hex(0x000000), 0)
            lab.clear_flag(lv.obj.FLAG.HIDDEN)
            lab.align_to(box, lv.ALIGN.TOP_LEFT, 0, label_dy(y1))
        return n

    def _print_dets(self, dets, n):
        now = time.ticks_ms()
        if time.ticks_diff(now, self._det_print_ms) < DET_PRINT_MS:
            return
        self._det_print_ms = now
        if not dets or n == 0:
            return
        labels = self.cfg["labels"]
        parts = []
        for det in dets[:n]:
            name = labels[int(det[0])] if int(det[0]) < len(labels) else str(int(det[0]))
            parts.append("{} {:.2f} [{},{},{}x{}]".format(
                name, float(det[1]), int(det[2]), int(det[3]),
                int(det[4]) - int(det[2]), int(det[5]) - int(det[3])))
        print("[{}] {}".format(self.tag, " | ".join(parts)))

    def _update_stat(self):
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

        dets = None
        try:
            dets = self._infer(ai_img)
        except BaseException as error:
            print("[{}] infer error:".format(self.tag), error)
            sys.print_exception(error)

        try:
            img.as_lvgl_img_src(self.img_widget)
            self.img_widget.invalidate()
        except BaseException as error:
            self._show_error("display error")
            return

        n = self._show_boxes(dets)
        self.last_objs = n
        self._print_dets(dets, n)

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
        print("[{}] destroy begin".format(self.tag))
        # The kpu is pooled and shared: never release it, only drop the reference.
        self.kpu = None
        if self.ai2d is not None:
            try:
                self.ai2d.deinit()
            except BaseException as e:
                print("[{}]   ai2d.deinit error:".format(self.tag), e)
            self.ai2d = None
        if self.sensor is not None:
            try:
                self.sensor.stop()
            except BaseException as e:
                print("[{}]   sensor.stop error:".format(self.tag), e)
            try:
                self.sensor.deinit()
            except BaseException as e:
                print("[{}]   sensor.deinit error:".format(self.tag), e)
            self.sensor = None
        gc.collect()
        print("[{}] destroy done | mem_free={}".format(self.tag, gc.mem_free()))
