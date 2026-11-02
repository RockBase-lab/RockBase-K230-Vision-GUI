# Pose app: full-screen live 17-keypoint human skeleton (yolo11n-pose 320).
#
# Layout and interaction follow Vision's Detect: immersive screen, the preview
# fills it, one 56 px info bar across the top, and the only widgets on top of the
# picture are pre-built overlay objects. Tapping the bar opens a chooser; the
# physical CANCEL key exits, so a stray tap can never interrupt monitoring.
#
# Measured on board (800x480 preview, one person, 320x320 input):
#   ai2d 1.4 ms + kpu 19.1 ms + to_numpy 0.4 ms ~ 21 ms per inference, which
#   makes inference the frame-rate limit. yolov8n-pose has the same output shape
#   and costs 22.6 ms; yolo26n-pose is 20.9 ms but returns (1,300,57) and needs a
#   different post-process call.
#   Skeleton draw: 6 polylines 5.5 ms, 17 joint dots 4.8 ms, both together
#   5.9 ms -- the dots are nearly free once the lines are drawn.
#
# Contract, measured -- do not copy the detection pipeline:
#   1) kpu.outputs_size() == 1, shape (1, 56, 2100): 4 box + 1 score + 51
#      keypoint values per anchor. Feed raw[0] straight in; transposing it makes
#      the post-process compare an int against an ndarray and raise.
#   2) aidemo.person_kp_postprocess(raw, [H, W], [mw, mh], conf, nms) -- five
#      positional arguments, no display-size parameter.
#   3) It returns [boxes, kps, scores]:
#        boxes  (N,4) x, y, w, h as int16
#        kps    (N,17,3) x, y, score, already in preview coordinates
#        scores (N,)
#      A person is index 0 of `kps`, not a flattened 51-float run: len() on the
#      ulab array gives 17, and hasattr(x, "__len__") is False for ulab arrays.
#   4) **y needs no letterbox correction.** Measured: box y=85 h=399 with the
#      nose at 120, i.e. delta 35 = 0.09*h. The detection models sit 160 px low on
#      the same frame; this one does not.
#   5) Every output tensor must be released right after to_numpy(); a leak
#      crashes the board and drops the serial link.
#
# False positives are background shapes with a plausible box but no usable
# joints (measured: two 0.75-score boxes on a wall). Candidates are therefore
# filtered by how many confident joints they actually carry, not by box score.
#
# Keys: CANCEL(GPIO0) exits (launcher default); OK(GPIO53) opens the chooser, or
# freezes/resumes when no chooser is open.

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

PREVIEW_W = 800                # capture size shared by preview and AI (= screen)
PREVIEW_H = 480
MODEL_PATH = "/sdcard/examples/kmodel/yolo11n-pose.kmodel"
MODEL_IN = [320, 320]
KP_NUM = 17

# Pose scores are already probabilities (measured 0.65-0.99 on real joints), so a
# plain threshold works. A joint below it is not drawn, and neither is a limb
# that ends there: a line to a guessed position reads as a real finding.
CONF = 0.35                    # person score
NMS = 0.45
KP_SCORE = 0.30
MIN_JOINTS = 5                 # a real person shows at least this many joints

TOPBAR_H = 56
TOPBAR_Y = 10
TOPBAR_LEFT = 8
TOPBAR_RIGHT = 8
SEG_PAD = 18

COL_TITLE = 0x00E5A0           # teal: model name
COL_FPS = 0xFFC857             # amber: frame rate
COL_CNT = 0x64B5F6             # sky blue: person count when non-zero
COL_CNT_IDLE = 0x6B7885        # grey: demoted count so "0 person" is not eye-catching
COL_SEP = 0x3A4650
COL_HINT = 0x7A8794
COL_FROZEN = 0xFF8A65
COL_ERROR = 0xFF6B6B

# Skeleton ink. Left and right limbs get different hues so a mirrored pose is
# readable at a glance; the torso is neutral because it carries no side
# information.
COL_LEFT = 0x7FB6D9
COL_RIGHT = 0xCC8A92
COL_TORSO = 0xE6EAF0
COL_HEAD = 0xC9AE7A
COL_DOT = 0x5CE8C0
LINE_W = 3
DOT_SIZE = 8

# COCO-17 chains, 0-based. Each chain becomes a polyline, so six line objects
# instead of sixteen; cost in this binding tracks object count, not point count.
# A chain that loses a joint is split into runs instead of drawing through a
# guessed position.
CHAINS = [
    ([5, 7, 9], COL_LEFT),
    ([6, 8, 10], COL_RIGHT),
    ([11, 13, 15], COL_LEFT),
    ([12, 14, 16], COL_RIGHT),
    ([5, 6, 12, 11, 5], COL_TORSO),
    ([3, 1, 0, 2, 4], COL_HEAD),
]
# Upper bound on runs per chain: a 5-point closed chain alternates at most three
# times, the 3-point chains twice.
RUNS_PER_CHAIN = 3

VIEWS = [
    ("Skeleton", "bones + joints"),
    ("Bones", "no joint dots"),
    ("Off", "preview only"),
]
VIEW_SKELETON = 0
VIEW_BONES = 1
VIEW_OFF = 2
DEFAULT_VIEW = VIEW_SKELETON

MODEL_MISSING = ("Pose model missing on SD card.\n"
                 "Expected:\n"
                 "  /sdcard/examples/kmodel/yolo11n-pose.kmodel\n"
                 "yolov8n-pose works too (same shape, 3.5 ms slower).")

DET_PRINT_MS = 500             # serial throttle: one line per half second


def seq_len(obj):
    """len() of a sequence, or -1 for a scalar.

    hasattr(x, "__len__") is unusable on this firmware: ulab ndarrays answer
    len() but do not expose __len__ as an attribute (measured), so every array
    looked like a scalar and the keypoints were dropped.
    """
    try:
        return len(obj)
    except BaseException:
        return -1


def _font(*names):
    for name in names:
        font = getattr(lv, name, None)
        if font:
            return font
    return None


class PoseApp:
    def __init__(self, content, ctx):
        self.ctx = ctx
        self.content = content
        self.sensor = None
        self.kpu = None
        self.ai2d = None
        self.frozen = False
        self.view = DEFAULT_VIEW
        self.picker = None
        self.frame_count = 0
        self.last_fps_ms = time.ticks_ms()
        self.fps_text = "-"
        self.last_persons = 0
        self.last_total = 0
        self.infer_ms = 0.0
        self._gc_tick = 0
        self._print_ms = 0
        self._no_signal = NoSignalCounter()

        self.img_w = ctx.width
        self.img_h = ctx.height

        # ---- preview ----
        self.img_widget = lv.img(content)
        self.img_widget.set_size(self.img_w, self.img_h)
        self.img_widget.align(lv.ALIGN.CENTER, 0, 0)

        # ---- skeleton pool: one group of lines per chain, all pre-built ----
        # Colour is a property of the pool slot, set once at construction, so no
        # style change happens per frame.
        self.chain_lines = []
        for _idx, color in CHAINS:
            group = []
            for _ in range(RUNS_PER_CHAIN):
                ln = lv.line(content)
                ln.set_points([{"x": 0, "y": 0}] * 2, 2)
                ln.set_style_line_width(LINE_W, 0)
                ln.set_style_line_color(lv.color_hex(color), 0)
                ln.set_style_line_opa(lv.OPA.COVER, 0)
                try:
                    ln.set_style_line_rounded(True, 0)
                except BaseException:
                    pass
                ln.add_flag(lv.obj.FLAG.HIDDEN)
                group.append(ln)
            self.chain_lines.append(group)

        self.dots = []
        for _ in range(KP_NUM):
            dot = lv.obj(content)
            dot.set_size(DOT_SIZE, DOT_SIZE)
            dot.set_style_radius(DOT_SIZE // 2, 0)
            dot.set_style_bg_color(lv.color_hex(COL_DOT), 0)
            dot.set_style_bg_opa(lv.OPA.COVER, 0)
            dot.set_style_border_width(0, 0)
            dot.set_style_pad_all(0, 0)
            dot.clear_flag(lv.obj.FLAG.SCROLLABLE)
            dot.clear_flag(lv.obj.FLAG.CLICKABLE)
            dot.add_flag(lv.obj.FLAG.HIDDEN)
            self.dots.append(dot)

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
            # keep the whole 56 px strip as one hit target
            lab.clear_flag(lv.obj.FLAG.CLICKABLE)
            return lab

        self.stat_model = _seg("loading", COL_TITLE)
        self.stat_sep1 = _seg("|", COL_SEP)
        self.stat_fps = _seg("FPS -", COL_FPS)
        self.stat_sep2 = _seg("|", COL_SEP)
        self.stat_count = _seg("0 person", COL_CNT_IDLE)
        self.stat_hint = _seg("TAP to switch", COL_HINT)

        self._init_ai()

    # ---------------- AI pipeline ----------------
    def _init_ai(self):
        t0 = time.ticks_ms()
        sensor = None
        try:
            sensor = Sensor(fps=30)
            sensor.reset()
            # chn1 = AI input: RGBP888 (planar) because ai2d declares NCHW_FMT.
            # A packed channel makes ai2d read HWC memory as CHW.
            set_preview(sensor, CAM_CHN_ID_1, PREVIEW_W, PREVIEW_H)
            sensor.set_pixformat(Sensor.RGBP888, chn=CAM_CHN_ID_1)
            # chn2 = preview: packed RGB888, the only format as_lvgl_img_src takes.
            set_preview(sensor, CAM_CHN_ID_2, PREVIEW_W, PREVIEW_H)
            sensor.set_pixformat(Sensor.RGB888, chn=CAM_CHN_ID_2)
            sensor.run()
        except BaseException as error:
            print("[Pose] sensor init failed: {}".format(error))
            sys.print_exception(error)
            close_sensor(sensor)
            raise AppUnavailable(CAMERA_UNAVAILABLE)
        self.sensor = sensor

        try:
            self._load_model()
        except AppUnavailable:
            raise
        except BaseException as error:
            print("[Pose] model load failed: {}".format(error))
            sys.print_exception(error)
            self._release_model()
            close_sensor(self.sensor)
            self.sensor = None
            raise AppUnavailable(MODEL_MISSING)

        print("[Pose] ready: load={}ms mem_free={}".format(
            time.ticks_diff(time.ticks_ms(), t0), gc.mem_free()))
        self._update_stat()

    def _load_model(self):
        mw, mh = MODEL_IN
        # Letterbox in output space: the preview is 5:3 and the model square, so
        # width saturates first and the bands are vertical only.
        self.pad_top = int((mh - int(PREVIEW_H * min(
            float(mw) / PREVIEW_W, float(mh) / PREVIEW_H))) / 2)
        kpu = nn.kpu()
        kpu.load_kmodel(MODEL_PATH)
        self.kpu = kpu

        ai2d = Ai2d()
        ai2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                            np.uint8, np.uint8)
        ai2d.pad([0, 0, 0, 0, self.pad_top, self.pad_top, 0, 0], 0,
                 [128, 128, 128])
        ai2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
        ai2d.build([1, 3, PREVIEW_H, PREVIEW_W], [1, 3, mh, mw])
        self.ai2d = ai2d
        print("[Pose] model loaded pad_top={} mem_free={}".format(
            self.pad_top, gc.mem_free()))

    def _release_model(self):
        if self.kpu is not None:
            try:
                self.kpu.release()
            except BaseException as error:
                print("[Pose] kpu.release error:", error)
            self.kpu = None
        if self.ai2d is not None:
            try:
                self.ai2d.deinit()
            except BaseException as error:
                print("[Pose] ai2d.deinit error:", error)
            self.ai2d = None
        gc.collect()

    def _infer(self, img):
        """One frame: ai2d -> kpu -> post-process. Returns (boxes, kps, scores)
        or None when nobody is in view."""
        tin = self.ai2d.run(img.to_numpy_ref())
        self.kpu.set_input_tensor(0, tin)
        self.kpu.run()
        ot = self.kpu.get_output_tensor(0)
        try:
            raw = ot.to_numpy()
        finally:
            ot.release()            # native tensors must be released
        del tin

        # raw[0] is (56, 2100) and already the layout the post-process wants; a
        # transpose raises TypeError inside the C layer (measured).
        res = aidemo.person_kp_postprocess(
            raw[0], [PREVIEW_H, PREVIEW_W], MODEL_IN, CONF, NMS)
        del raw

        n = seq_len(res)
        if n < 2:
            return None
        boxes = res[0]
        kps = res[1]
        scores = res[2] if n > 2 else None
        if seq_len(boxes) <= 0 or seq_len(kps) <= 0:
            return None
        return boxes, kps, scores

    def _valid_joints(self, person):
        """Number of joints above KP_SCORE, i.e. how much skeleton is drawable."""
        n = seq_len(person)
        if n <= 0:
            return 0
        count = 0
        for k in range(n):
            row = person[k]
            if seq_len(row) > 2 and float(row[2]) >= KP_SCORE:
                count += 1
        return count

    def _pick(self, boxes, kps):
        """Return (index of the person to draw, number of drawable people).

        A background false positive carries a plausible box but almost no
        confident joints, so joints are ranked before box area and a candidate
        below MIN_JOINTS is never drawn. Index -1 means nothing drawable.
        """
        total = seq_len(boxes)
        available = seq_len(kps)
        best = -1
        best_joints = MIN_JOINTS - 1
        best_area = -1.0
        drawable = 0
        for i in range(total if total < available else available):
            joints = self._valid_joints(kps[i])
            if joints < MIN_JOINTS:
                continue
            drawable += 1
            row = boxes[i]
            area = float(row[2]) * float(row[3])
            if joints > best_joints or (joints == best_joints and area > best_area):
                best_joints = joints
                best_area = area
                best = i
        return best, drawable

    # ---------------- drawing ----------------
    def _hide_skeleton(self):
        for group in self.chain_lines:
            for ln in group:
                ln.add_flag(lv.obj.FLAG.HIDDEN)
        for dot in self.dots:
            dot.add_flag(lv.obj.FLAG.HIDDEN)

    def _show_skeleton(self, kps, idx):
        person = kps[idx]
        count = seq_len(person)
        if count <= 0:
            self._hide_skeleton()
            return

        # Cache the 17 points once; every chain and dot reads from these.
        xs = [0] * count
        ys = [0] * count
        ok = [False] * count
        for k in range(count):
            row = person[k]
            s = float(row[2]) if seq_len(row) > 2 else 1.0
            if s < KP_SCORE:
                continue
            xs[k] = int(row[0])
            ys[k] = int(row[1])
            ok[k] = True

        show_dots = self.view == VIEW_SKELETON
        show_lines = self.view in (VIEW_SKELETON, VIEW_BONES)

        # ---- lines: split every chain into runs of valid joints ----
        for ci in range(len(CHAINS)):
            group = self.chain_lines[ci]
            chain = CHAINS[ci][0]
            slot = 0
            run = []
            for node in chain:
                if node < count and ok[node]:
                    run.append(node)
                else:
                    slot = self._emit_run(group, slot, run, xs, ys, show_lines)
                    run = []
            slot = self._emit_run(group, slot, run, xs, ys, show_lines)
            for i in range(slot, len(group)):
                group[i].add_flag(lv.obj.FLAG.HIDDEN)

        # ---- joint dots ----
        for k in range(len(self.dots)):
            visible = show_dots and k < count and ok[k]
            if visible:
                dot = self.dots[k]
                dot.set_pos(xs[k] - DOT_SIZE // 2, ys[k] - DOT_SIZE // 2)
                dot.clear_flag(lv.obj.FLAG.HIDDEN)
            else:
                self.dots[k].add_flag(lv.obj.FLAG.HIDDEN)

    def _emit_run(self, group, slot, run, xs, ys, visible):
        """Draw one contiguous run of joints as a polyline."""
        if len(run) < 2 or not visible:
            return slot
        if slot >= len(group):
            return slot              # pool exhausted: drop rather than leak objects
        pts = [{"x": xs[n], "y": ys[n]} for n in run]
        ln = group[slot]
        ln.set_points(pts, len(pts))
        ln.clear_flag(lv.obj.FLAG.HIDDEN)
        return slot + 1

    # ---------------- status bar ----------------
    def _update_stat(self):
        self.stat_model.set_text("YOLO11n-pose")
        self.stat_fps.set_text("FPS {}".format(self.fps_text))
        # The bar reports the person being drawn, never the raw candidate count:
        # the model also fires on posters and edge objects, so "1 of 4" made the
        # screen look wrong when one person was in front of the camera.
        self.stat_count.set_text("{} person".format(self.last_persons))
        self.stat_count.set_style_text_color(
            lv.color_hex(COL_CNT if self.last_persons > 0 else COL_CNT_IDLE), 0)
        if self.frozen:
            self.stat_hint.set_text("FROZEN")
            self.stat_hint.set_style_text_color(lv.color_hex(COL_FROZEN), 0)
        else:
            self.stat_hint.set_text("{} · TAP".format(VIEWS[self.view][0]))
            self.stat_hint.set_style_text_color(lv.color_hex(COL_HINT), 0)

    def _show_error(self, text):
        self.stat_hint.set_text(text)
        self.stat_hint.set_style_text_color(lv.color_hex(COL_ERROR), 0)

    def _print_state(self, boxes, idx):
        now = time.ticks_ms()
        if time.ticks_diff(now, self._print_ms) < DET_PRINT_MS:
            return
        self._print_ms = now
        row = boxes[idx]
        print("[Pose] drawable={} infer={:.1f}ms box=({},{},{}x{})".format(
            self.last_total, self.infer_ms, int(row[0]), int(row[1]),
            int(row[2]), int(row[3])))

    # ---------------- view chooser overlay ----------------
    def _close_picker(self):
        """Every close path must unfreeze, otherwise the picture stalls."""
        if self.picker is not None:
            try:
                self.picker.delete()
            except BaseException as error:
                print("[Pose] picker delete error:", error)
            self.picker = None
        self.frozen = False
        self._update_stat()

    def _open_picker(self):
        """Two layers: a full-screen backdrop (tap to close) and a centred panel
        that swallows clicks. A single small panel is easily missed by the hit
        test above the img widget and offers no obvious dismiss area."""
        if self.picker is not None:
            return
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
        panel.set_size(560, 76 + len(VIEWS) * 58)
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
        title.set_text("Select view")
        title.set_style_text_color(lv.color_hex(0xE6EAF0), 0)
        title.align(lv.ALIGN.TOP_MID, 0, 2)

        hint = lv.label(panel)
        hint.set_text("tap a row to switch, tap outside to close")
        hint.set_style_text_color(lv.color_hex(0x8A97A3), 0)
        hint.align(lv.ALIGN.TOP_MID, 0, 24)

        font = _font("font_montserrat_16", "font_montserrat_14")
        y = 48
        for idx in range(len(VIEWS)):
            name, note = VIEWS[idx]
            row = lv.btn(panel)
            row.set_size(524, 50)
            row.align(lv.ALIGN.TOP_MID, 0, y)
            row.set_style_radius(10, 0)
            row.set_style_border_width(2 if idx == self.view else 0, 0)
            row.set_style_border_color(lv.color_hex(0x00C853), 0)
            row.set_style_bg_color(lv.color_hex(0x1E262E), 0)
            row.set_style_bg_opa(lv.OPA.COVER, 0)
            row.set_style_pad_all(0, 0)
            row.clear_flag(lv.obj.FLAG.SCROLLABLE)
            row.add_flag(lv.obj.FLAG.CLICKABLE)

            lab = lv.label(row)
            mark = "> " if idx == self.view else "  "
            lab.set_text("{}{}   {}".format(mark, name, note))
            lab.set_style_text_color(
                lv.color_hex(0x00C853 if idx == self.view else 0xE6EAF0), 0)
            if font is not None:
                lab.set_style_text_font(font, 0)
            lab.center()
            lab.clear_flag(lv.obj.FLAG.CLICKABLE)   # let the click land on the row
            beep.on_click(row, lambda e, i=idx: self._pick_view(i))
            y += 58

        self.picker = backdrop

    def _pick_view(self, idx):
        self._close_picker()
        if idx == self.view:
            return
        self.view = idx
        # Clear the old geometry so the switch is visible on a frozen frame too.
        self._hide_skeleton()
        self._update_stat()

    # ---------------- frame ----------------
    def step(self):
        if self.sensor is None:
            return

        # OK comes before the frozen check: otherwise a chooser opened with OK
        # could never be closed with OK again.
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
            print("[Pose] snapshot error:", error)
            self._show_error("snapshot error")
            self._no_signal.on_fail(self.ctx)
            return
        self._no_signal.on_ok()

        t0 = time.ticks_ms()
        res = None
        try:
            res = self._infer(ai_img)
        except BaseException as error:
            print("[Pose] infer error:", error)
            sys.print_exception(error)
        self.infer_ms = time.ticks_diff(time.ticks_ms(), t0)

        # preview always updates, even when inference failed
        try:
            img.as_lvgl_img_src(self.img_widget)
            self.img_widget.invalidate()
        except BaseException:
            self._show_error("display error")
            return

        if res is not None:
            boxes, kps, _scores = res
            idx, drawable = self._pick(boxes, kps)
            self.last_total = drawable
            if idx >= 0:
                self._show_skeleton(kps, idx)
                self.last_persons = 1
                self._print_state(boxes, idx)
            else:
                self._hide_skeleton()
                self.last_persons = 0
            del boxes, kps
        else:
            self._hide_skeleton()
            self.last_persons = 0
            self.last_total = 0
        del res

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

    def destroy(self):
        print("[Pose] destroy begin")
        self._close_picker()
        self._release_model()
        if self.sensor is not None:
            try:
                self.sensor.stop()
            except BaseException as error:
                print("[Pose] sensor.stop error:", error)
            try:
                self.sensor.deinit()
            except BaseException as error:
                print("[Pose] sensor.deinit error:", error)
            self.sensor = None
        gc.collect()
        print("[Pose] destroy done | mem_free={}".format(gc.mem_free()))


def create_app(content, ctx):
    return PoseApp(content, ctx)
