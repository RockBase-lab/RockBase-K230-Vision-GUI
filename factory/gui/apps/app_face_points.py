# Points app: full-screen live 106-point face landmark mesh (det -> landmark).
#
# Second stage of the Face group: Faces answers where the faces are, Points draws
# the facial structure of the largest one. One model beyond the detector
# (face_landmark.kmodel, 192x192) fed by an affine crop of the detected box.
#
# Drawing is nine lv.line objects, one per anatomical group, never one object per
# point. Measured at 800x480 under FULL render: one line 12.0 ms, five 17.3 ms,
# eight 20.1 ms, ten 25.2 ms, while set_points for all 100 points costs 1.83 ms,
# so the cost is rasterisation, not geometry. The pupil pair is not a group: two
# points in one line draw a stroke across the nose.
#
# The affine rebuild costs 5.78 ms per call, so it runs every other frame and the
# previous points are reused in between; the landmark inference itself is 1.32 ms.

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

PREVIEW_W = 800
PREVIEW_H = 480

DET_MODEL = "/sdcard/examples/kmodel/face_detection_320.kmodel"
LM_MODEL = "/sdcard/examples/kmodel/face_landmark.kmodel"
ANCHOR_PATH = "/sdcard/examples/utils/prior_data_320.bin"
DET_IN = [320, 320]
LM_IN = 192
ANCHOR_N = 4200
N_POINTS = 106

# Confidence threshold for picking the face to follow. Fixed: this app shows
# structure, not detection tuning, which is what the Faces app's presets are for.
DET_CONF = 0.50
NMS_TH = 0.2

# The affine crop must be rebuilt for a new box, and one rebuild costs 5.78 ms.
# Rebuilding every other frame keeps the mesh one frame behind while saving about
# half that cost; the face does not move far in 40 ms.
REBUILD_EVERY = 2

# Anatomical groups. Points index the model's 106-point output; a closed group
# repeats its first point so the outline joins up.
GROUPS = [
    ("basin", [1, 9, 10, 11, 12, 13, 14, 15, 16, 2, 3, 4, 5, 6, 7, 8, 0, 24,
               23, 22, 21, 20, 19, 18, 32, 31, 30, 29, 28, 27, 26, 25, 17],
     True, 0x00E5A0),
    ("lbrow", [43, 44, 45, 47, 46, 50, 51, 49, 48], False, 0xFFC857),
    ("rbrow", [97, 98, 99, 100, 101, 105, 104, 103, 102], False, 0xFFC857),
    ("leye", [35, 36, 33, 37, 39, 42, 40, 41], True, 0x64B5F6),
    ("reye", [89, 90, 87, 91, 93, 96, 94, 95], True, 0x64B5F6),
    ("bridge", [72, 73, 74, 86], False, 0xFF8A65),
    ("wing", [77, 78, 79, 80, 85, 84, 83], False, 0xFF8A65),
    ("outlip", [52, 55, 56, 53, 59, 58, 61, 68, 67, 71, 63, 64], True, 0xE88BA8),
    ("inlip", [65, 54, 60, 57, 69, 70, 62, 66], True, 0xC2708A),
]
LINE_W = 2

# View tiers: the index into VIEWS is what the top bar cycles. "Contour" keeps the
# outer silhouette plus brows and lips, which is the readable subset at a glance.
CONTOUR = {"basin", "lbrow", "rbrow", "outlip", "inlip"}
VIEWS = [
    ("All", None),
    ("Contour", CONTOUR),
    ("Off", set()),
]
DEFAULT_VIEW = 0

TOPBAR_H = 56
TOPBAR_Y = 10
TOPBAR_LEFT = 8
TOPBAR_RIGHT = 8
SEG_PAD = 18

COL_TITLE = 0x00E5A0
COL_FPS = 0xFFC857
COL_CNT = 0x64B5F6
COL_CNT_IDLE = 0x6B7885
COL_SEP = 0x3A4650
COL_HINT = 0x7A8794
COL_FROZEN = 0xFF8A65
COL_ERROR = 0xFF6B6B

MODEL_MISSING = ("Face model or anchors missing on SD card.\n"
                 "Expected:\n"
                 "  /sdcard/examples/kmodel/face_detection_320.kmodel\n"
                 "  /sdcard/examples/kmodel/face_landmark.kmodel\n"
                 "  /sdcard/examples/utils/prior_data_320.bin")


def letterbox():
    """Letterbox parameters from 800x480 to 320x320: (scale, pad_top)."""
    mw, mh = DET_IN
    scale = min(float(mw) / PREVIEW_W, float(mh) / PREVIEW_H)
    pad_top = int((mh - int(PREVIEW_H * scale)) / 2)
    return scale, pad_top


def y_pad_offset():
    """The detector returns y including the letterbox top pad, so subtract this
    before the coordinate is in screen space. pad_top / scale = 160 for 800x480."""
    scale, pad_top = letterbox()
    return int(pad_top / scale)


def rgb_param():
    """Fifth argument of face_det_post_process: [W, H + y_off] = [800, 640].
    The rectangle is also the clip bound, so the height must cover the pad."""
    return [PREVIEW_W, PREVIEW_H + y_pad_offset()]


def _font(*names):
    for name in names:
        font = getattr(lv, name, None)
        if font:
            return font
    return None


class FacePointsApp:
    def __init__(self, content, ctx):
        self.ctx = ctx
        self.content = content
        self.sensor = None
        self.kpu_det = None
        self.kpu_lm = None
        self.a2d_det = None
        self.a2d_lm = None
        self.anchors = None

        self.frozen = False
        self.frame_count = 0
        self.last_fps_ms = time.ticks_ms()
        self.fps_text = "-"
        self.has_face = False
        self._gc_tick = 0
        self._frame_i = 0
        self.view_idx = DEFAULT_VIEW
        self.y_off = y_pad_offset()
        self._no_signal = NoSignalCounter()

        # Last successfully mapped points, reused on frames without a rebuild.
        self._kx = [0.0] * N_POINTS
        self._ky = [0.0] * N_POINTS
        self._have_pts = False
        self._last_box = None

        self.img_w = ctx.width
        self.img_h = ctx.height

        self.img_widget = lv.img(content)
        self.img_widget.set_size(self.img_w, self.img_h)
        self.img_widget.align(lv.ALIGN.CENTER, 0, 0)

        self.lines = []
        for name, idx, closed, color in GROUPS:
            ln = lv.line(content)
            ln.set_points([{"x": 0, "y": 0}] * 2, 2)
            ln.set_style_line_width(LINE_W, 0)
            ln.set_style_line_color(lv.color_hex(color), 0)
            ln.set_style_line_opa(lv.OPA.COVER, 0)
            try:
                ln.set_style_line_rounded(False, 0)
            except BaseException:
                pass
            ln.add_flag(lv.obj.FLAG.HIDDEN)
            self.lines.append(ln)

        self._build_stat_bar()
        self._init_ai()

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
        beep.on_click(self.stat_btn, lambda e: self._cycle_view())
        self.stat_btn.set_flex_flow(lv.FLEX_FLOW.ROW)
        self.stat_btn.set_flex_align(lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER,
                                     lv.FLEX_ALIGN.CENTER)
        try:
            self.stat_btn.set_style_pad_column(SEG_PAD, 0)
        except BaseException:
            pass

        bar_font = _font("font_montserrat_20", "font_montserrat_16",
                         "font_montserrat_14")

        def seg(text, color):
            lab = lv.label(self.stat_btn)
            lab.set_text(text)
            lab.set_width(lv.SIZE_CONTENT)
            lab.set_style_text_color(lv.color_hex(color), 0)
            if bar_font is not None:
                lab.set_style_text_font(bar_font, 0)
            lab.clear_flag(lv.obj.FLAG.CLICKABLE)
            return lab

        self.seg_title = seg("Points", COL_TITLE)
        self.seg_sep1 = seg("|", COL_SEP)
        self.seg_count = seg("no face", COL_CNT_IDLE)
        self.seg_sep2 = seg("|", COL_SEP)
        self.seg_fps = seg("FPS -", COL_FPS)
        self.seg_hint = seg("TAP: view", COL_HINT)

    def _update_stat(self):
        view = VIEWS[self.view_idx][0]
        self.seg_title.set_text("Points {}".format(view))
        self.seg_title.set_style_text_color(lv.color_hex(COL_TITLE), 0)
        if self.has_face:
            self.seg_count.set_text("{} pts".format(N_POINTS))
            self.seg_count.set_style_text_color(lv.color_hex(COL_CNT), 0)
        else:
            self.seg_count.set_text("no face")
            self.seg_count.set_style_text_color(lv.color_hex(COL_CNT_IDLE), 0)
        self.seg_fps.set_text("FPS {}".format(self.fps_text))
        if self.frozen:
            self.seg_hint.set_text("FROZEN")
            self.seg_hint.set_style_text_color(lv.color_hex(COL_FROZEN), 0)
        else:
            self.seg_hint.set_text("TAP: view")
            self.seg_hint.set_style_text_color(lv.color_hex(COL_HINT), 0)

    def _show_error(self, text):
        self.seg_hint.set_text(text)
        self.seg_hint.set_style_text_color(lv.color_hex(COL_ERROR), 0)

    def _cycle_view(self):
        self.view_idx = (self.view_idx + 1) % len(VIEWS)
        print("[Points] view -> {}".format(VIEWS[self.view_idx][0]))
        self._update_stat()
        self._apply_lines()

    # ---------- AI pipeline ----------
    def _init_ai(self):
        t0 = time.ticks_ms()
        try:
            if (not self._exists(DET_MODEL) or not self._exists(LM_MODEL)
                    or not self._exists(ANCHOR_PATH)):
                raise AppUnavailable(MODEL_MISSING)
        except AppUnavailable:
            raise
        except BaseException as error:
            print("[Points] file check error:", error)

        try:
            f = open(ANCHOR_PATH, "rb")
            raw = f.read()
            f.close()
            del f
            self.anchors = np.frombuffer(raw, dtype=np.float).reshape(
                (ANCHOR_N, 4))
            del raw
        except BaseException as error:
            print("[Points] anchor load failed:", error)
            raise AppUnavailable(MODEL_MISSING)

        sensor = None
        try:
            sensor = Sensor(fps=30)
            sensor.reset()
            # chn1 = AI input, planar RGBP888 because ai2d declares NCHW_FMT.
            sensor.set_framesize(w=PREVIEW_W, h=PREVIEW_H, chn=CAM_CHN_ID_1,
                                 crop=True)
            sensor.set_pixformat(Sensor.RGBP888, chn=CAM_CHN_ID_1)
            # chn2 = preview, packed RGB888; as_lvgl_img_src only takes packed.
            sensor.set_framesize(w=PREVIEW_W, h=PREVIEW_H, chn=CAM_CHN_ID_2,
                                 crop=True)
            sensor.set_pixformat(Sensor.RGB888, chn=CAM_CHN_ID_2)
            sensor.run()
        except BaseException as error:
            print("[Points] sensor init failed:", error)
            sys.print_exception(error)
            close_sensor(sensor)
            raise AppUnavailable(CAMERA_UNAVAILABLE)
        self.sensor = sensor

        self._load_models()
        print("[Points] ready: load={}ms y_off={} mem_free={}".format(
            time.ticks_diff(time.ticks_ms(), t0), self.y_off, gc.mem_free()))
        self._update_stat()

    @staticmethod
    def _exists(path):
        try:
            return os.stat(path)[6] > 0
        except BaseException:
            return False

    def _load_models(self):
        kpu = nn.kpu()
        kpu.load_kmodel(DET_MODEL)
        self.kpu_det = kpu

        kpu = nn.kpu()
        kpu.load_kmodel(LM_MODEL)
        self.kpu_lm = kpu

        _scale, pad_top = letterbox()
        a2d = Ai2d()
        a2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                           np.uint8, np.uint8)
        a2d.pad([0, 0, 0, 0, pad_top, pad_top, 0, 0], 0, [104, 117, 123])
        a2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
        a2d.build([1, 3, PREVIEW_H, PREVIEW_W],
                  [1, 3, DET_IN[1], DET_IN[0]])
        self.a2d_det = a2d

        # The landmark crop is an affine transform rebuilt per box, so only the
        # dtype is set here; affine + build happen on the first real box.
        self.a2d_lm = Ai2d()
        self.a2d_lm.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT,
                                   nn.ai2d_format.NCHW_FMT, np.uint8, np.uint8)

        print("[Points] models loaded | det={} lm={} mem_free={}".format(
            DET_IN[0], LM_IN, gc.mem_free()))

    def _release_models(self):
        for name in ("kpu_det", "kpu_lm"):
            kpu = getattr(self, name)
            if kpu is not None:
                try:
                    kpu.release()
                except BaseException as e:
                    print("[Points]   {}.release error: {}".format(name, e))
                setattr(self, name, None)
        for name in ("a2d_det", "a2d_lm"):
            a2d = getattr(self, name)
            if a2d is not None:
                try:
                    a2d.deinit()
                except BaseException as e:
                    print("[Points]   {}.deinit error: {}".format(name, e))
                setattr(self, name, None)
        gc.collect()

    def _detect(self, img):
        """Largest face box in screen coordinates, or None.

        Every output tensor is to_numpy()d and released immediately: a missed
        release leaks native memory and the board drops its serial link.
        """
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

    def _affine_matrix(self, box):
        """Map the box into the 192 model square with a 1.5x margin, centred."""
        x, y, w, h = box
        scale = float(LM_IN) / (max(w, h) * 1.5)
        half = LM_IN / 2.0
        cx = (x + w / 2.0) * scale
        cy = (y + h / 2.0) * scale
        m = np.zeros((2, 3), dtype=np.float)
        m[0, 0] = scale
        m[0, 1] = 0.0
        m[0, 2] = half - cx
        m[1, 0] = 0.0
        m[1, 1] = scale
        m[1, 2] = half - cy
        return m

    def _run_landmark(self, img, box):
        """Run the landmark model on one box and store screen-space points.

        The output is normalised to -1..1, so (p + 1) * LM_IN / 2 yields model
        pixels; the inverse affine then brings them back to screen space. Adding
        instead of assigning here pushes every point far off screen.
        """
        m = self._affine_matrix(box)
        self.a2d_lm.affine(nn.interp_method.cv2_bilinear, 0, 0, 127, 1,
                           [m[0, 0], m[0, 1], m[0, 2],
                            m[1, 0], m[1, 1], m[1, 2]])
        self.a2d_lm.build([1, 3, PREVIEW_H, PREVIEW_W],
                          [1, 3, LM_IN, LM_IN])

        tin = self.a2d_lm.run(img.to_numpy_ref())
        self.kpu_lm.set_input_tensor(0, tin)
        self.kpu_lm.run()
        pred = None
        for i in range(self.kpu_lm.outputs_size()):
            ot = self.kpu_lm.get_output_tensor(i)
            try:
                arr = ot.to_numpy()
                if pred is None:
                    pred = arr.flatten()
            finally:
                ot.release()
        del tin
        if pred is None:
            return False

        half = LM_IN / 2.0
        inv = aidemo.invert_affine_transform(m).flatten()
        n = min(len(pred) // 2, N_POINTS)
        for k in range(n):
            ox = (pred[k * 2] + 1.0) * half
            oy = (pred[k * 2 + 1] + 1.0) * half
            self._kx[k] = ox * inv[0] + oy * inv[1] + inv[2]
            self._ky[k] = ox * inv[3] + oy * inv[4] + inv[5]
        self._have_pts = True
        self._last_box = box
        del pred
        return True

    # ---------- drawing ----------
    def _apply_lines(self):
        """Push the stored points into the visible groups only.

        A hidden group must also drop its points to two, otherwise the stale
        geometry stays allocated and keeps the object sized for a full outline.
        """
        active = VIEWS[self.view_idx][1]
        if not self._have_pts:
            for ln in self.lines:
                ln.add_flag(lv.obj.FLAG.HIDDEN)
            return
        for gi, (name, idx, closed, color) in enumerate(GROUPS):
            ln = self.lines[gi]
            if active is not None and name not in active:
                ln.add_flag(lv.obj.FLAG.HIDDEN)
                continue
            pts = []
            for i in idx:
                pts.append({"x": int(self._kx[i]), "y": int(self._ky[i])})
            if closed and len(pts) > 2:
                pts.append({"x": pts[0]["x"], "y": pts[0]["y"]})
            ln.set_points(pts, len(pts))
            ln.clear_flag(lv.obj.FLAG.HIDDEN)

    # ---------- main loop ----------
    def step(self):
        if self.sensor is None:
            return

        if self.ctx.take_ok():
            self.frozen = not self.frozen
            self._update_stat()

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
            print("[Points] detect error:", error)
            sys.print_exception(error)

        self._frame_i += 1
        if box is None:
            self.has_face = False
            self._have_pts = False
        else:
            self.has_face = True
            rebuild = (self._last_box is None
                       or self._frame_i % REBUILD_EVERY == 0
                       or abs(box[0] - self._last_box[0]) > 8
                       or abs(box[1] - self._last_box[1]) > 8)
            if rebuild:
                try:
                    self._run_landmark(ai_img, box)
                except BaseException as error:
                    print("[Points] landmark error:", error)
                    sys.print_exception(error)
                    self._have_pts = False

        try:
            img.as_lvgl_img_src(self.img_widget)
            self.img_widget.invalidate()
        except BaseException as error:
            self._show_error("display error")
            return

        self._apply_lines()

        # lvgl image sources and sensor frames are cyclic garbage; without a
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
        print("[Points] destroy begin")
        self._release_models()
        if self.sensor is not None:
            for name in ("stop", "deinit"):
                try:
                    getattr(self.sensor, name)()
                    print("[Points]   sensor.{} ok".format(name))
                except BaseException as e:
                    print("[Points]   sensor.{} error: {}".format(name, e))
            self.sensor = None
        gc.collect()
        print("[Points] destroy done | mem_free={}".format(gc.mem_free()))


def create_app(content, ctx):
    return FacePointsApp(content, ctx)
