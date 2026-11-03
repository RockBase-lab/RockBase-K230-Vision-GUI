# Gesture: two-stage static hand-gesture recognition over a live preview.
#
#   stage 1  hand_det.kmodel (yolov5 anchor, 512x512) -> hand boxes
#   stage 2  handkp_det.kmodel (256x256) -> 21 keypoints on the largest hand
#   classify with pure angle rules over the finger joints -> one of nine names
#
# The overlay draws the hand box, a 21-point skeleton (palm + five four-joint
# fingers), and the recognised gesture in the top bar. Layout and interaction
# follow Pose: immersive screen, one 56 px info bar, CANCEL(GPIO0) exits,
# OK(GPIO53) freezes/resumes.
#
# Measured on board (800x480 preview, one hand):
#   handkp_det load 1.36 s (weights live in native memory, Python heap flat);
#   output shape (1,42) = 21 points x (x,y) normalised to the crop;
#   a full step (hand_det + crop + keypoint inference) is 78-83 ms, about 9 fps.
#   handkp_det is NOT in BOOT_MODELS: it loads on demand through model_pool,
#   which caches it so reopening the app is instant. Seventeen resident kpus
#   (16 boot + handkp_det) are safe when the Sensor is built before the load.
#
# Contract, measured -- do not copy the Pose pipeline:
#   1) The keypoint crop is built per frame from the det box: length =
#      max(w,h)/2, r = 1.26*length around the box centre, clamped to the frame.
#      ai2d.crop then resize then build([1,3,H,W],[1,3,256,256]) every frame.
#   2) Decode: flat = out.reshape(42); x = flat[0::2]*crop_w + crop_x,
#      y = flat[1::2]*crop_h + crop_y. Point 0 is the palm; finger i holds
#      points 1+i*4..4+i*4 (four joints). The classifier reads each finger's
#      second joint at value i*8+4, i.e. point i*4+2.
#   3) Classification compares the palm->first-joint vector against the
#      first->third-joint vector per finger; a degenerate vector returns 65535
#      and that frame is unclassifiable, not fatal.
#   4) Every output tensor must be released right after to_numpy(); a leak
#      crashes the board and drops the serial link.

import gc
import math
import sys
import time

import lvgl as lv
import ulab.numpy as np
import nncase_runtime as nn
import aicube
from libs.AI2D import Ai2d
from media.sensor import Sensor, CAM_CHN_ID_1, CAM_CHN_ID_2
from ui import beep
from apps.error import (AppUnavailable, CAMERA_UNAVAILABLE, NoSignalCounter,
                        close_sensor)
from camera_fov import set_preview
from model_pool import get as model_get

PREVIEW_W = 800
PREVIEW_H = 480
DET_PATH = "/sdcard/examples/kmodel/hand_det.kmodel"
KP_PATH = "/sdcard/examples/kmodel/handkp_det.kmodel"
DET_IN = [512, 512]
KP_IN = [256, 256]
ANCHORS = [26, 27, 53, 52, 75, 71, 80, 99, 106, 82, 99, 134,
           140, 113, 161, 172, 245, 276]
STRIDES = [8, 16, 32]
DET_CONF = 0.20
DET_NMS = 0.50
MIN_H_FRAC = 0.08              # ignore hands shorter than 8% of the frame height

# Angle thresholds (degrees). THR_A = a straight finger, THR_T = the thumb's
# wider range, THR_S = a clearly bent finger.
THR_A = 65.0
THR_T = 53.0
THR_S = 49.0
DEGENERATE = 65535.0

TOPBAR_H = 56
TOPBAR_Y = 10
TOPBAR_LEFT = 8
TOPBAR_RIGHT = 8
SEG_PAD = 16

COL_TITLE = 0xFFB74D           # amber: app name
COL_FPS = 0xFFC857
COL_GESTURE = 0x00E5A0         # teal: recognised gesture
COL_GESTURE_IDLE = 0x6B7885    # grey: no gesture yet
COL_SEP = 0x3A4650
COL_HINT = 0x7A8794
COL_FROZEN = 0xFF8A65
COL_ERROR = 0xFF6B6B

COL_BOX = 0xFFB74D
COL_PALM = 0x00E5A0
COL_DOT = 0x5CE8C0
FINGER_COLORS = [0xE57373, 0xF4A941, 0xD4E157, 0x66BB6A, 0x64B5F6]
LINE_W = 3
DOT_SIZE = 7
PALM_SIZE = 11

DET_PRINT_MS = 500


def _font(*names):
    for name in names:
        font = getattr(lv, name, None)
        if font:
            return font
    return None


def _vector_angle(v1, v2):
    """Angle between two 2-vectors in degrees; DEGENERATE for a zero-length one."""
    n1 = math.sqrt(v1[0] * v1[0] + v1[1] * v1[1])
    n2 = math.sqrt(v2[0] * v2[0] + v2[1] * v2[1])
    if n1 * n2 <= 1e-6:
        return DEGENERATE
    cos_a = (v1[0] * v2[0] + v1[1] * v2[1]) / (n1 * n2)
    cos_a = max(-1.0, min(1.0, cos_a))
    return math.acos(cos_a) * 180.0 / math.pi


def classify(r):
    """Gesture name from the 42 flat screen-space values, or None."""
    a = []
    for i in range(5):
        a.append(_vector_angle(
            [r[0] - r[i * 8 + 4], r[1] - r[i * 8 + 5]],
            [r[i * 8 + 6] - r[i * 8 + 8], r[i * 8 + 7] - r[i * 8 + 9]]))
    if DEGENERATE in a:
        return None
    if (a[0] > THR_T and a[1] > THR_A and a[2] > THR_A
            and a[3] > THR_A and a[4] > THR_A):
        return "fist"
    if (a[0] < THR_S and a[1] < THR_S and a[2] < THR_S
            and a[3] < THR_S and a[4] < THR_S):
        return "five"
    if (a[0] < THR_S and a[1] < THR_S and a[2] > THR_A
            and a[3] > THR_A and a[4] > THR_A):
        return "gun"
    if (a[0] < THR_S and a[1] < THR_S and a[2] > THR_A
            and a[3] > THR_A and a[4] < THR_S):
        return "love"
    if (a[0] > 5 and a[1] < THR_S and a[2] > THR_A
            and a[3] > THR_A and a[4] > THR_A):
        return "one"
    if (a[0] < THR_S and a[1] > THR_A and a[2] > THR_A
            and a[3] > THR_A and a[4] < THR_S):
        return "six"
    if (a[0] > THR_T and a[1] < THR_S and a[2] < THR_S
            and a[3] < THR_S and a[4] > THR_A):
        return "three"
    if (a[0] < THR_S and a[1] > THR_A and a[2] > THR_A
            and a[3] > THR_A and a[4] > THR_A):
        return "thumbUp"
    if (a[0] > THR_T and a[1] < THR_S and a[2] < THR_S
            and a[3] > THR_A and a[4] > THR_A):
        return "yeah"
    return None


class GestureApp:
    def __init__(self, content, ctx):
        self.ctx = ctx
        self.content = content
        self.sensor = None
        self.kpu_det = None
        self.kpu_kp = None
        self.a2d_det = None
        self.a2d_kp = None
        self.frozen = False
        self.frame_count = 0
        self.last_fps_ms = time.ticks_ms()
        self.fps_text = "-"
        self.infer_ms = 0.0
        self.last_gesture = None
        self._gc_tick = 0
        self._print_ms = 0
        self._no_signal = NoSignalCounter()

        self.img_w = ctx.width
        self.img_h = ctx.height

        # ---- preview ----
        self.img_widget = lv.img(content)
        self.img_widget.set_size(self.img_w, self.img_h)
        self.img_widget.align(lv.ALIGN.CENTER, 0, 0)

        # ---- hand box outline (closed 5-point polyline) ----
        self.box_line = lv.line(content)
        self.box_line.set_points([{"x": 0, "y": 0}] * 5, 5)
        self.box_line.set_style_line_width(LINE_W, 0)
        self.box_line.set_style_line_color(lv.color_hex(COL_BOX), 0)
        try:
            self.box_line.set_style_line_rounded(True, 0)
        except BaseException:
            pass
        self.box_line.add_flag(lv.obj.FLAG.HIDDEN)

        # ---- skeleton pool: palm dot + five finger polylines + 20 joint dots ----
        self.palm_dot = self._make_dot(PALM_SIZE, COL_PALM)
        self.finger_lines = []
        for color in FINGER_COLORS:
            ln = lv.line(content)
            ln.set_points([{"x": 0, "y": 0}] * 5, 5)   # palm + 4 joints
            ln.set_style_line_width(LINE_W, 0)
            ln.set_style_line_color(lv.color_hex(color), 0)
            try:
                ln.set_style_line_rounded(True, 0)
            except BaseException:
                pass
            ln.add_flag(lv.obj.FLAG.HIDDEN)
            self.finger_lines.append(ln)
        # points 1..20 (point 0 is the palm, drawn separately)
        self.dots = [self._make_dot(DOT_SIZE, COL_DOT) for _ in range(20)]

        # ---- top bar ----
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
            lab.clear_flag(lv.obj.FLAG.CLICKABLE)
            return lab

        self.stat_title = _seg("Gesture", COL_TITLE)
        self.stat_sep1 = _seg("|", COL_SEP)
        self.stat_gesture = _seg("--", COL_GESTURE_IDLE)
        self.stat_sep2 = _seg("|", COL_SEP)
        self.stat_fps = _seg("FPS -", COL_FPS)
        self.stat_hint = _seg("show a hand", COL_HINT)

        self._init_ai()

    def _make_dot(self, size, color):
        dot = lv.obj(self.content)
        dot.set_size(size, size)
        dot.set_style_radius(size // 2, 0)
        dot.set_style_bg_color(lv.color_hex(color), 0)
        dot.set_style_bg_opa(lv.OPA.COVER, 0)
        dot.set_style_border_width(0, 0)
        dot.set_style_pad_all(0, 0)
        dot.clear_flag(lv.obj.FLAG.SCROLLABLE)
        dot.clear_flag(lv.obj.FLAG.CLICKABLE)
        dot.add_flag(lv.obj.FLAG.HIDDEN)
        return dot

    # ---------------- AI pipeline ----------------
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
            print("[Gesture] sensor init failed: {}".format(error))
            sys.print_exception(error)
            close_sensor(sensor)
            raise AppUnavailable(CAMERA_UNAVAILABLE)
        self.sensor = sensor

        try:
            self._load_models()
        except AppUnavailable:
            raise
        except BaseException as error:
            print("[Gesture] model load failed: {}".format(error))
            sys.print_exception(error)
            self._release_models()
            close_sensor(self.sensor)
            self.sensor = None
            raise AppUnavailable("Gesture models missing on SD card.\n"
                                 "Expected hand_det.kmodel and handkp_det.kmodel "
                                 "under /sdcard/examples/kmodel/.")

        print("[Gesture] ready: load={}ms mem_free={}".format(
            time.ticks_diff(time.ticks_ms(), t0), gc.mem_free()))
        self._update_stat()

    def _load_models(self):
        mw, mh = DET_IN
        # Letterbox in output space: the 5:3 preview into a square model input
        # pads vertically only.
        self.pad_top = int((mh - int(PREVIEW_H * min(
            float(mw) / PREVIEW_W, float(mh) / PREVIEW_H))) / 2)

        # Both kpu instances come from the shared pool; the pool owns them and
        # this app must not release them. hand_det is preloaded at boot,
        # handkp_det loads on demand here and stays cached for reopening.
        self.kpu_det = model_get(DET_PATH)
        self.kpu_kp = model_get(KP_PATH)

        a2d_det = Ai2d()
        a2d_det.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                               np.uint8, np.uint8)
        a2d_det.pad([0, 0, 0, 0, self.pad_top, self.pad_top, 0, 0], 0,
                    [114, 114, 114])
        a2d_det.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
        a2d_det.build([1, 3, PREVIEW_H, PREVIEW_W], [1, 3, mh, mw])
        self.a2d_det = a2d_det

        # The keypoint ai2d is rebuilt per frame from the det box, so it starts
        # unbuilt; _keypoints configures crop+resize+build each frame.
        self.a2d_kp = Ai2d()
        self.a2d_kp.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT,
                                   nn.ai2d_format.NCHW_FMT, np.uint8, np.uint8)
        print("[Gesture] models loaded pad_top={} mem_free={}".format(
            self.pad_top, gc.mem_free()))

    def _release_models(self):
        # The kpu instances belong to the shared pool; only per-app ai2d go.
        self.kpu_det = None
        self.kpu_kp = None
        for name in ("a2d_det", "a2d_kp"):
            obj = getattr(self, name)
            if obj is not None:
                try:
                    obj.deinit()
                except BaseException as error:
                    print("[Gesture] {}.deinit error:".format(name), error)
                setattr(self, name, None)
        gc.collect()

    def _detect(self, img):
        """Largest hand box [cls,score,x1,y1,x2,y2] in preview coords, or None."""
        tin = self.a2d_det.run(img.to_numpy_ref())
        self.kpu_det.set_input_tensor(0, tin)
        self.kpu_det.run()
        res = []
        for i in range(self.kpu_det.outputs_size()):
            ot = self.kpu_det.get_output_tensor(i)
            try:
                res.append(ot.to_numpy())
            finally:
                ot.release()
        del tin
        dets = aicube.anchorbasedet_post_process(
            res[0], res[1], res[2], DET_IN, [PREVIEW_W, PREVIEW_H], STRIDES,
            1, DET_CONF, DET_NMS, ANCHORS, False)
        del res
        best = None
        best_area = -1.0
        min_h = MIN_H_FRAC * PREVIEW_H
        for d in dets:
            h = d[5] - d[3]
            if h < min_h:
                continue
            area = (d[4] - d[2]) * h
            if area > best_area:
                best_area = area
                best = d
        return best

    def _crop_param(self, box):
        """Square keypoint crop around the box centre, clamped to the frame."""
        x1, y1, x2, y2 = box[2], box[3], box[4], box[5]
        length = max(x2 - x1, y2 - y1) / 2.0
        cx = (x1 + x2) / 2.0
        cy = (y1 + y2) / 2.0
        r = 1.26 * length
        ax = int(max(0, cx - r))
        ay = int(max(0, cy - r))
        bx = int(min(PREVIEW_W - 1, cx + r))
        by = int(min(PREVIEW_H - 1, cy + r))
        return [ax, ay, bx - ax + 1, by - ay + 1]

    def _keypoints(self, img, crop):
        """21 points in preview coords as a flat list of 42 ints, or None."""
        self.a2d_kp.crop(crop[0], crop[1], crop[2], crop[3])
        self.a2d_kp.resize(nn.interp_method.tf_bilinear,
                           nn.interp_mode.half_pixel)
        self.a2d_kp.build([1, 3, PREVIEW_H, PREVIEW_W],
                          [1, 3, KP_IN[1], KP_IN[0]])
        tin = self.a2d_kp.run(img.to_numpy_ref())
        self.kpu_kp.set_input_tensor(0, tin)
        self.kpu_kp.run()
        ot = self.kpu_kp.get_output_tensor(0)
        try:
            out = ot.to_numpy()
        finally:
            ot.release()
        del tin
        if out.shape[-1] != 42:
            return None
        flat = out.reshape(42)
        pts = [0] * 42
        for k in range(21):
            pts[k * 2] = int(flat[k * 2] * crop[2] + crop[0])
            pts[k * 2 + 1] = int(flat[k * 2 + 1] * crop[3] + crop[1])
        return pts

    # ---------------- drawing ----------------
    def _hide_overlay(self):
        self.box_line.add_flag(lv.obj.FLAG.HIDDEN)
        self.palm_dot.add_flag(lv.obj.FLAG.HIDDEN)
        for ln in self.finger_lines:
            ln.add_flag(lv.obj.FLAG.HIDDEN)
        for dot in self.dots:
            dot.add_flag(lv.obj.FLAG.HIDDEN)

    def _show_box(self, box):
        x1, y1, x2, y2 = int(box[2]), int(box[3]), int(box[4]), int(box[5])
        pts = [{"x": x1, "y": y1}, {"x": x2, "y": y1}, {"x": x2, "y": y2},
               {"x": x1, "y": y2}, {"x": x1, "y": y1}]
        self.box_line.set_points(pts, 5)
        self.box_line.clear_flag(lv.obj.FLAG.HIDDEN)

    def _show_skeleton(self, pts):
        px, py = pts[0], pts[1]
        self.palm_dot.set_pos(px - PALM_SIZE // 2, py - PALM_SIZE // 2)
        self.palm_dot.clear_flag(lv.obj.FLAG.HIDDEN)

        # Finger i: palm + its four joints (points 1+i*4 .. 4+i*4; point 0 is
        # the palm). The classifier's i*8+4 value is each finger's second joint.
        for i in range(5):
            base = i * 4 + 1
            fp = [{"x": px, "y": py}]
            for j in range(4):
                fp.append({"x": pts[(base + j) * 2],
                           "y": pts[(base + j) * 2 + 1]})
            self.finger_lines[i].set_points(fp, 5)
            self.finger_lines[i].clear_flag(lv.obj.FLAG.HIDDEN)

        for k in range(20):
            dot = self.dots[k]
            dot.set_pos(pts[(k + 1) * 2] - DOT_SIZE // 2,
                        pts[(k + 1) * 2 + 1] - DOT_SIZE // 2)
            dot.clear_flag(lv.obj.FLAG.HIDDEN)

    # ---------------- status bar ----------------
    def _update_stat(self):
        self.stat_fps.set_text("FPS {}".format(self.fps_text))
        if self.last_gesture:
            self.stat_gesture.set_text(self.last_gesture)
            self.stat_gesture.set_style_text_color(lv.color_hex(COL_GESTURE), 0)
        else:
            self.stat_gesture.set_text("--")
            self.stat_gesture.set_style_text_color(
                lv.color_hex(COL_GESTURE_IDLE), 0)
        if self.frozen:
            self.stat_hint.set_text("FROZEN")
            self.stat_hint.set_style_text_color(lv.color_hex(COL_FROZEN), 0)
        else:
            self.stat_hint.set_text("show a hand")
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

        if self.ctx.take_ok():
            self._toggle_freeze()

        if self.frozen:
            return

        try:
            ai_img = self.sensor.snapshot(chn=CAM_CHN_ID_1)
            img = self.sensor.snapshot(chn=CAM_CHN_ID_2)
        except BaseException as error:
            print("[Gesture] snapshot error:", error)
            self._show_error("snapshot error")
            self._no_signal.on_fail(self.ctx)
            return
        self._no_signal.on_ok()

        t0 = time.ticks_ms()
        box = None
        pts = None
        gesture = None
        try:
            box = self._detect(ai_img)
            if box is not None:
                pts = self._keypoints(ai_img, self._crop_param(box))
                if pts is not None:
                    gesture = classify(pts)
        except BaseException as error:
            print("[Gesture] infer error:", error)
            sys.print_exception(error)
        self.infer_ms = time.ticks_diff(time.ticks_ms(), t0)

        try:
            img.as_lvgl_img_src(self.img_widget)
            self.img_widget.invalidate()
        except BaseException:
            self._show_error("display error")
            return

        if pts is not None:
            if box is not None:
                self._show_box(box)
            self._show_skeleton(pts)
            self.last_gesture = gesture
            self._print_state(box, gesture)
        else:
            self._hide_overlay()
            self.last_gesture = None

        del box, pts

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

    def _print_state(self, box, gesture):
        now = time.ticks_ms()
        if time.ticks_diff(now, self._print_ms) < DET_PRINT_MS:
            return
        self._print_ms = now
        print("[Gesture] gesture={} infer={:.1f}ms box={}".format(
            gesture, self.infer_ms,
            [int(v) for v in box[2:6]] if box else None))

    def destroy(self):
        print("[Gesture] destroy begin")
        self._release_models()
        if self.sensor is not None:
            try:
                self.sensor.stop()
            except BaseException as error:
                print("[Gesture] sensor.stop error:", error)
            try:
                self.sensor.deinit()
            except BaseException as error:
                print("[Gesture] sensor.deinit error:", error)
            self.sensor = None
        gc.collect()
        print("[Gesture] destroy done | mem_free={}".format(gc.mem_free()))


def create_app(content, ctx):
    return GestureApp(content, ctx)
