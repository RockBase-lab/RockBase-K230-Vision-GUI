# Orient: rotated-box detection (yolo11n-obb, 320x320, DOTA 15 classes).
#
# Same capture shell as the anchor-based apps, but the decode is
# aidemo.yolo_obb_postprocess and each box comes back as four corners, so the
# overlay is a closed lv.line polyline (same pattern as the plate outline)
# rather than a rectangular widget.
#
# Output tensor: (1, 20, 2100) = [x, y, w, h, score, 15 classes] per anchor;
# transpose to (2100, 20) and .copy() before handing it to aidemo, exactly like
# the yolov8 path in app_vision (a strided view makes the C layer read garbage).
#
# Letterbox: the ai2d pad follows the same rule as app_vision (pad acts in the
# output space after scaling), and yolo_obb_postprocess keeps the pad in its y
# output, so boxes need the same y_off correction app_vision measures (160 px
# at 800x480 preview). Verify per frame with _probe_orient_boxes.py.

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
from core import i18n
from core.errors import (AppUnavailable, CAMERA_UNAVAILABLE, NoSignalCounter,
                       close_sensor)
from hardware.camera_fov import set_preview
from core import model_pool

PREVIEW_W = 800
PREVIEW_H = 480
MAX_BOXES = 8
BOX_POOL = MAX_BOXES
QUAD_POINTS = 5             # closed polyline: 4 corners + first repeated
LINE_W = 3
TOPBAR_H = 56
TOPBAR_Y = 10
TOPBAR_LEFT = 8
TOPBAR_RIGHT = 8
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

DOTA = ["plane", "ship", "storage tank", "baseball diamond", "tennis court",
        "basketball court", "ground track field", "harbor", "bridge",
        "large vehicle", "small vehicle", "helicopter", "roundabout",
        "soccer ball field", "swimming pool"]

CLASS_COLORS = [0x7FB6D9, 0xC99AAE, 0x9C92C9, 0x6FB8A4,
                0xC9AE7A, 0x7FA6CC, 0xA9BE7C, 0xCC8A92]

MODEL_PATH = "/sdcard/examples/kmodel/yolo11n-obb.kmodel"
MODEL_INPUT = [320, 320]
CONF = 0.10                 # official tuning; DOTA scenes are cluttered, a
NMS = 0.60                  # tighter threshold hides most true hits


def _font(*names):
    for name in names:
        font = getattr(lv, name, None)
        if font:
            return font
    return None


class OrientApp:
    def __init__(self, content, ctx):
        self.ctx = ctx
        self.content = content
        self.sensor = None
        self.kpu = None
        self.ai2d = None
        self.y_off = 0
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

        # ---- quad outline pool (closed 5-point polyline) + label ----
        self.box_lines = []
        self.box_labels = []
        label_font = _font("font_montserrat_14", "font_montserrat_12")
        for _ in range(BOX_POOL):
            line = lv.line(content)
            line.set_points([{"x": 0, "y": 0}] * QUAD_POINTS, QUAD_POINTS)
            line.set_style_line_width(LINE_W, 0)
            line.set_style_line_opa(lv.OPA.COVER, 0)
            try:
                line.set_style_line_rounded(True, 0)
            except BaseException:
                pass
            line.add_flag(lv.obj.FLAG.HIDDEN)
            self.box_lines.append(line)
            lab = lv.label(content)
            lab.add_flag(lv.obj.FLAG.HIDDEN)
            lab.set_style_bg_opa(lv.OPA._80, 0)
            lab.set_style_bg_color(lv.color_hex(0x000000), 0)
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
            f = i18n.font_for(text, bar_font)
            if f is not None:
                lab.set_style_text_font(f, 0)
            lab.clear_flag(lv.obj.FLAG.CLICKABLE)
            return lab

        self.stat_model = _seg(i18n.t("ai.loading"), COL_MODEL)
        self.stat_sep1 = _seg("|", COL_SEP)
        self.stat_fps = _seg("FPS -", COL_FPS)
        self.stat_sep2 = _seg("|", COL_SEP)
        self.stat_objs = _seg(i18n.t("ai.objs").format("-"), COL_OBJS_IDLE)
        self.stat_hint = _seg(i18n.t("ai.tap_freeze"), COL_HINT)
        self.stat_label = self.stat_model

        self._init_ai()

    def _init_ai(self):
        t0 = time.ticks_ms()
        sensor = None
        try:
            sensor = Sensor(fps=30)
            sensor.reset()
            set_preview(sensor, CAM_CHN_ID_1, PREVIEW_W, PREVIEW_H)
            sensor.set_pixformat(Sensor.RGBP888, chn=CAM_CHN_ID_1)
            set_preview(sensor, CAM_CHN_ID_2, PREVIEW_W, PREVIEW_H)
            sensor.set_pixformat(Sensor.RGB888, chn=CAM_CHN_ID_2)
            sensor.run()
        except BaseException as error:
            print("[Orient] sensor init failed: {}".format(error))
            sys.print_exception(error)
            close_sensor(sensor)
            raise AppUnavailable(CAMERA_UNAVAILABLE)
        self.sensor = sensor

        mw, mh = MODEL_INPUT
        self.kpu = model_pool.get(MODEL_PATH)
        scale = min(float(mw) / PREVIEW_W, float(mh) / PREVIEW_H)
        pad_top = int((mh - int(PREVIEW_H * scale)) / 2)
        # yolo_obb_postprocess leaves the letterbox pad in y (same trap as the
        # yolov8 path), so subtract pad_top * W / mw from every corner.
        self.y_off = int(pad_top * PREVIEW_W / mw)
        ai2d = Ai2d()
        ai2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                           np.uint8, np.uint8)
        ai2d.pad([0, 0, 0, 0, pad_top, pad_top, 0, 0], 0, [128, 128, 128])
        ai2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
        ai2d.build([1, 3, PREVIEW_H, PREVIEW_W], [1, 3, mh, mw])
        self.ai2d = ai2d

        load_ms = time.ticks_diff(time.ticks_ms(), t0)
        print("[Orient] ready: load={}ms y_off={} mem_free={}".format(
            load_ms, self.y_off, gc.mem_free()))
        self._update_stat()

    def _infer(self, img):
        mw, mh = MODEL_INPUT
        tin = self.ai2d.run(img.to_numpy_ref())
        self.kpu.set_input_tensor(0, tin)
        self.kpu.run()
        ot = self.kpu.get_output_tensor(0)
        try:
            raw = ot.to_numpy()
        finally:
            ot.release()
        # (1,20,2100) -> (2100,20); .copy() is mandatory for the C layer.
        grid = raw[0].transpose().copy()
        score_max = float(np.max(grid[:, 4]))
        del raw
        if score_max < CONF:
            del grid
            return None
        res = aidemo.yolo_obb_postprocess(
            grid, [PREVIEW_H, PREVIEW_W], [mh, mw], [PREVIEW_H, PREVIEW_W],
            len(DOTA), CONF, NMS, MAX_BOXES)
        del grid
        return res

    def _show_boxes(self, dets):
        n = 0
        if dets and dets[0]:
            n = min(len(dets[0]), BOX_POOL)
        for i in range(BOX_POOL):
            line = self.box_lines[i]
            lab = self.box_labels[i]
            if i >= n:
                line.add_flag(lv.obj.FLAG.HIDDEN)
                lab.add_flag(lv.obj.FLAG.HIDDEN)
                continue
            corners = dets[0][i]
            pts = []
            top_y = 10000
            for k in range(4):
                x = int(corners[k * 2])
                y = int(corners[k * 2 + 1]) - self.y_off
                pts.append({"x": x, "y": y})
                if y < top_y:
                    top_y = y
            pts.append(pts[0])
            color = lv.color_hex(CLASS_COLORS[int(dets[1][i]) % len(CLASS_COLORS)])
            line.set_points(pts, QUAD_POINTS)
            line.set_style_line_color(color, 0)
            line.clear_flag(lv.obj.FLAG.HIDDEN)

            cls_id = int(dets[1][i])
            name = DOTA[cls_id] if cls_id < len(DOTA) else str(cls_id)
            lab.set_text("{} {:.2f}".format(name, float(dets[2][i])))
            lab.set_style_text_color(color, 0)
            lab.clear_flag(lv.obj.FLAG.HIDDEN)
            lab.set_pos(max(0, pts[0]["x"]), max(0, top_y - 18))
        return n

    def _print_dets(self, dets, n):
        now = time.ticks_ms()
        if time.ticks_diff(now, self._det_print_ms) < DET_PRINT_MS:
            return
        self._det_print_ms = now
        if not dets or n == 0:
            return
        parts = []
        for i in range(n):
            cls_id = int(dets[1][i])
            name = DOTA[cls_id] if cls_id < len(DOTA) else str(cls_id)
            c = dets[0][i]
            parts.append("{} {:.2f} [({},{})]".format(
                name, float(dets[2][i]), int(c[0]), int(c[1]) - self.y_off))
        print("[Orient] {}".format(" | ".join(parts)))

    def _update_stat(self):
        self.stat_model.set_text("OBB Y11n")
        self.stat_model.set_style_text_color(lv.color_hex(COL_MODEL), 0)
        self.stat_fps.set_text("FPS {}".format(self.fps_text))
        self.stat_objs.set_text(i18n.t("ai.objs").format(self.last_objs))
        self.stat_objs.set_style_text_color(
            lv.color_hex(COL_OBJS if self.last_objs > 0 else COL_OBJS_IDLE), 0)
        if self.frozen:
            self.stat_hint.set_text(i18n.t("ai.frozen"))
            self.stat_hint.set_style_text_color(lv.color_hex(COL_FROZEN), 0)
        else:
            self.stat_hint.set_text(i18n.t("ai.tap_freeze"))
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
            self._show_error(i18n.t("ai.snapshot_error"))
            self._no_signal.on_fail(self.ctx)
            return
        self._no_signal.on_ok()

        dets = None
        try:
            dets = self._infer(ai_img)
        except BaseException as error:
            print("[Orient] infer error:", error)
            sys.print_exception(error)

        try:
            img.as_lvgl_img_src(self.img_widget)
            self.img_widget.invalidate()
        except BaseException as error:
            self._show_error(i18n.t("ai.display_error"))
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
        print("[Orient] destroy begin")
        self.kpu = None      # pooled: never release
        if self.ai2d is not None:
            try:
                self.ai2d.deinit()
            except BaseException as e:
                print("[Orient]   ai2d.deinit error:", e)
            self.ai2d = None
        if self.sensor is not None:
            try:
                self.sensor.stop()
            except BaseException as e:
                print("[Orient]   sensor.stop error:", e)
            try:
                self.sensor.deinit()
            except BaseException as e:
                print("[Orient]   sensor.deinit error:", e)
            self.sensor = None
        gc.collect()
        print("[Orient] destroy done | mem_free={}".format(gc.mem_free()))


def create_app(content, ctx):
    return OrientApp(content, ctx)
