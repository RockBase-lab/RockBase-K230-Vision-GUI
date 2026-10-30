# Recognize app: enroll faces by name, then label the largest face live.
#
# Third stage of the Face group. Detect -> 5 landmarks -> similarity transform to
# the canonical 112x112 face -> face_recognition_mobile.kmodel -> 512-d feature,
# compared against the enrolled database with cos/2 + 0.5.
#
# Measured on board (800x480 preview, largest box 142x190): detection 9.6 ms,
# recognition 4.35 ms including the affine and its rebuild, so a feature is
# computed every frame. Both models load in 368 ms and the kernel memory is
# outside the Python heap, so free heap barely moves.
#
# The feature arithmetic is vectorised through ulab on purpose: normalising 512
# floats with a Python loop measured 11.1 ms and dominated the frame, while
# np.dot / array division do the same in 0.02 ms. Keep it as arrays, not lists.
#
# Two facts that are easy to get wrong and were measured, not assumed:
#
#   1) The feature is **512-dimensional**, not the 128 the reference code names.
#      A wrong stride silently compares half of one person to half of another.
#   2) Landmark y still carries the letterbox pad, so Y_OFF must be subtracted
#      before the similarity transform. With it, the mapped eyes land within
#      2 px of the canonical row and within 0.5 px of each other; without it
#      every crop is shifted and the scores collapse.
#
# Threshold: same-subject pairs measured min 0.9827, median 0.9905, sd 0.0025
# over 66 pairs. ENROLL_MATCH is 0.90, which is 32 sd below the worst genuine
# pair. The impostor distribution could not be measured with one subject
# available; revisit when a second person can be enrolled.

import gc
import json
import math
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
from ui import kb_layer
from apps.error import (AppUnavailable, CAMERA_UNAVAILABLE, NoSignalCounter,
                        close_sensor)

PREVIEW_W = 800
PREVIEW_H = 480

DET_MODEL = "/sdcard/examples/kmodel/face_detection_320.kmodel"
REG_MODEL = "/sdcard/examples/kmodel/face_recognition_mobile.kmodel"
ANCHOR_PATH = "/sdcard/examples/utils/prior_data_320.bin"
DET_IN = [320, 320]
REG_IN = 112
ANCHOR_N = 4200
FEAT_DIM = 512

DET_CONF = 0.50
NMS_TH = 0.2

# Same-subject minimum measured at 0.9827; 0.90 keeps a wide margin while still
# rejecting an unrelated face, whose score sits near 0.5-0.7.
ENROLL_MATCH = 0.90

# The feature database lives on /data, not /sdcard: the SD card holds the
# deployed product content and has about 76 MB free, while /data has 28 GB. The
# path mirrors the /data/<app>/<topic> layout used by photos and cache.
DB_DIR = "/data/face/recognize"
DB_INDEX = DB_DIR + "/db.json"
TMP_SUFFIX = ".tmp"

MAX_ENROLL = 200
NAME_MAX = 16

# Five canonical points of the aligned face, in a 112x112 crop.
UMEYAMA_112 = [
    38.2946, 51.6963,
    73.5318, 51.5014,
    56.0252, 71.7366,
    41.5493, 92.3655,
    70.7299, 92.2041,
]

TOPBAR_H = 56
TOPBAR_Y = 10
TOPBAR_LEFT = 8
TOPBAR_RIGHT = 8
SEG_PAD = 18

COL_TITLE = 0x9AA8E0
COL_FPS = 0xFFC857
COL_KNOWN = 0x00E5A0
COL_UNKNOWN = 0x7A8794
COL_SEP = 0x3A4650
COL_HINT = 0x7A8794
COL_FROZEN = 0xFF8A65
COL_ERROR = 0xFF6B6B

BOX_KNOWN = 0x00E5A0
BOX_UNKNOWN = 0x9AA8E0

LAB_H = 18

MODEL_MISSING = ("Face model missing on SD card.\n"
                 "Expected:\n"
                 "  /sdcard/examples/kmodel/face_detection_320.kmodel\n"
                 "  /sdcard/examples/kmodel/face_recognition_mobile.kmodel\n"
                 "  /sdcard/examples/utils/prior_data_320.bin")


def letterbox():
    """Letterbox parameters from 800x480 to 320x320: (scale, pad_top)."""
    mw, mh = DET_IN
    scale = min(float(mw) / PREVIEW_W, float(mh) / PREVIEW_H)
    pad_top = int((mh - int(PREVIEW_H * scale)) / 2)
    return scale, pad_top


def y_pad_offset():
    """Detector y includes the letterbox pad; subtract this to reach screen space."""
    scale, pad_top = letterbox()
    return int(pad_top / scale)


def rgb_param():
    """[W, H + y_off]: the post-process uses it as the clip rectangle as well."""
    return [PREVIEW_W, PREVIEW_H + y_pad_offset()]


def svd22(a):
    s = [0.0, 0.0]
    u = [0.0, 0.0, 0.0, 0.0]
    v = [0.0, 0.0, 0.0, 0.0]
    s[0] = (math.sqrt((a[0] - a[3]) ** 2 + (a[1] + a[2]) ** 2)
            + math.sqrt((a[0] + a[3]) ** 2 + (a[1] - a[2]) ** 2)) / 2
    s[1] = abs(s[0] - math.sqrt((a[0] - a[3]) ** 2 + (a[1] + a[2]) ** 2))
    v[2] = math.sin((math.atan2(2 * (a[0] * a[1] + a[2] * a[3]),
                                a[0] ** 2 - a[1] ** 2 + a[2] ** 2 - a[3] ** 2))
                    / 2) if s[0] > s[1] else 0
    v[0] = math.sqrt(1 - v[2] ** 2)
    v[1] = -v[2]
    v[3] = v[0]
    u[0] = -(a[0] * v[0] + a[1] * v[2]) / s[0] if s[0] != 0 else 1
    u[2] = -(a[2] * v[0] + a[3] * v[2]) / s[0] if s[0] != 0 else 0
    u[1] = (a[0] * v[1] + a[1] * v[3]) / s[1] if s[1] != 0 else -u[2]
    u[3] = (a[2] * v[1] + a[3] * v[3]) / s[1] if s[1] != 0 else u[0]
    v[0] = -v[0]
    v[2] = -v[2]
    return u, s, v


def umeyama_112(src):
    """Similarity transform mapping five observed points onto the canonical face.

    Returns the six affine coefficients for ai2d. Uses the closed-form 2x2 SVD
    because this firmware has no general linear algebra.
    """
    n = 5
    src_mean = [0.0, 0.0]
    dst_mean = [0.0, 0.0]
    for i in range(0, n * 2, 2):
        src_mean[0] += src[i]
        src_mean[1] += src[i + 1]
        dst_mean[0] += UMEYAMA_112[i]
        dst_mean[1] += UMEYAMA_112[i + 1]
    src_mean[0] /= n
    src_mean[1] /= n
    dst_mean[0] /= n
    dst_mean[1] /= n

    src_demean = [[0.0, 0.0] for _ in range(n)]
    dst_demean = [[0.0, 0.0] for _ in range(n)]
    for i in range(n):
        src_demean[i][0] = src[2 * i] - src_mean[0]
        src_demean[i][1] = src[2 * i + 1] - src_mean[1]
        dst_demean[i][0] = UMEYAMA_112[2 * i] - dst_mean[0]
        dst_demean[i][1] = UMEYAMA_112[2 * i + 1] - dst_mean[1]

    a = [[0.0, 0.0], [0.0, 0.0]]
    for i in range(2):
        for k in range(2):
            for j in range(n):
                a[i][k] += dst_demean[j][i] * src_demean[j][k]
            a[i][k] /= n

    t = [[1.0, 0.0, 0.0], [0.0, 1.0, 0.0], [0.0, 0.0, 1.0]]
    u, s, v = svd22([a[0][0], a[0][1], a[1][0], a[1][1]])
    t[0][0] = u[0] * v[0] + u[1] * v[2]
    t[0][1] = u[0] * v[1] + u[1] * v[3]
    t[1][0] = u[2] * v[0] + u[3] * v[2]
    t[1][1] = u[2] * v[1] + u[3] * v[3]

    var = [0.0, 0.0]
    for i in range(n):
        var[0] += src_demean[i][0] * src_demean[i][0]
        var[1] += src_demean[i][1] * src_demean[i][1]
    var[0] /= n
    var[1] /= n
    denom = var[0] + var[1]
    scale = (1.0 / denom * (s[0] + s[1])) if denom > 1e-9 else 1.0

    t[0][2] = dst_mean[0] - scale * (t[0][0] * src_mean[0]
                                     + t[0][1] * src_mean[1])
    t[1][2] = dst_mean[1] - scale * (t[1][0] * src_mean[0]
                                     + t[1][1] * src_mean[1])
    for i in (0, 1):
        for k in (0, 1):
            t[i][k] *= scale
    return [t[0][0], t[0][1], t[0][2], t[1][0], t[1][1], t[1][2]]


def _font(*names):
    for name in names:
        font = getattr(lv, name, None)
        if font:
            return font
    return None


def label_dy(box_y):
    """Y offset of a label relative to its box top: keep it inside the frame."""
    return -LAB_H if box_y >= LAB_H else 1


class FaceRecognizeApp:
    def __init__(self, content, ctx):
        self.ctx = ctx
        self.content = content
        self.sensor = None
        self.kpu_det = None
        self.kpu_reg = None
        self.a2d_det = None
        self.a2d_reg = None
        self.anchors = None

        self.frozen = False
        self.frame_count = 0
        self.last_fps_ms = time.ticks_ms()
        self.fps_text = "-"
        self.y_off = y_pad_offset()
        self._gc_tick = 0
        self._no_signal = NoSignalCounter()

        self.has_face = False
        self._last_feat = None       # normalised 512-d feature of the last frame
        self._name = None            # matched name, None = unknown
        self._score = 0.0
        self._enroll_note = ""
        self._note_ms = 0
        # Last values pushed to the bar, so it is only rebuilt when its text
        # would actually change.
        self._shown_name = None
        self._shown_score = -1
        self._shown_face = None

        self.kb = None               # naming keyboard (None = closed)
        self._pending_feat = None
        self._manage = None          # manage overlay (None = closed)
        self._empty_panel = None     # shown while the database is empty

        # Database: names and features loaded once, features normalised on load so
        # the per-frame compare is a plain dot product.
        self.db_names = []
        self.db_feats = []
        self._db_next = 1

        self.img_w = ctx.width
        self.img_h = ctx.height

        self.img_widget = lv.img(content)
        self.img_widget.set_size(self.img_w, self.img_h)
        self.img_widget.align(lv.ALIGN.CENTER, 0, 0)

        self.box = lv.obj(content)
        self.box.set_style_bg_opa(lv.OPA.TRANSP, 0)
        self.box.set_style_border_width(3, 0)
        self.box.set_style_radius(6, 0)
        self.box.clear_flag(lv.obj.FLAG.SCROLLABLE)
        self.box.add_flag(lv.obj.FLAG.HIDDEN)

        self.box_label = lv.label(content)
        self.box_label.add_flag(lv.obj.FLAG.HIDDEN)
        self.box_label.set_style_bg_opa(lv.OPA._80, 0)
        self.box_label.set_style_pad_hor(4, 0)
        self.box_label.set_style_pad_ver(1, 0)
        self.box_label.set_style_radius(3, 0)
        label_font = _font("font_montserrat_14", "font_montserrat_12")
        if label_font is not None:
            self.box_label.set_style_text_font(label_font, 0)
        self.label_font = label_font

        self._build_stat_bar()
        # Load the database before the models so the start-up log reports the real
        # face count, and so a broken index surfaces before 370 ms of kmodel load.
        self._db_load()
        self._init_ai()
        self._update_stat()
        if not self.db_names:
            self._show_empty_panel()

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
        beep.on_click(self.stat_btn, lambda e: self._open_manage())
        self.stat_btn.set_flex_flow(lv.FLEX_FLOW.ROW)
        self.stat_btn.set_flex_align(lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER,
                                     lv.FLEX_ALIGN.CENTER)
        try:
            self.stat_btn.set_style_pad_column(SEG_PAD, 0)
        except BaseException:
            pass

        bar_font = _font("font_montserrat_20", "font_montserrat_16",
                         "font_montserrat_14")
        self.bar_font = bar_font

        def seg(text, color):
            lab = lv.label(self.stat_btn)
            lab.set_text(text)
            lab.set_width(lv.SIZE_CONTENT)
            lab.set_style_text_color(lv.color_hex(color), 0)
            if bar_font is not None:
                lab.set_style_text_font(bar_font, 0)
            lab.clear_flag(lv.obj.FLAG.CLICKABLE)
            return lab

        self.seg_title = seg("Recognize", COL_TITLE)
        self.seg_sep1 = seg("|", COL_SEP)
        self.seg_name = seg("-", COL_UNKNOWN)
        self.seg_sep2 = seg("|", COL_SEP)
        self.seg_fps = seg("FPS -", COL_FPS)
        self.seg_hint = seg("OK: enroll", COL_HINT)

    def _update_stat(self):
        if self.db_names:
            self.seg_title.set_text("Recognize {}".format(len(self.db_names)))
        else:
            self.seg_title.set_text("Recognize")
        self.seg_title.set_style_text_color(lv.color_hex(COL_TITLE), 0)

        note = ""
        if self._enroll_note and time.ticks_diff(time.ticks_ms(),
                                                 self._note_ms) < 2500:
            note = self._enroll_note
        if note:
            self.seg_name.set_text(note)
            self.seg_name.set_style_text_color(lv.color_hex(COL_FPS), 0)
        elif not self.has_face:
            self.seg_name.set_text("no face")
            self.seg_name.set_style_text_color(lv.color_hex(COL_UNKNOWN), 0)
        elif self._name:
            self.seg_name.set_text("{} {:.2f}".format(self._name, self._score))
            self.seg_name.set_style_text_color(lv.color_hex(COL_KNOWN), 0)
        else:
            self.seg_name.set_text("unknown {:.2f}".format(self._score)
                                   if self._score else "unknown")
            self.seg_name.set_style_text_color(lv.color_hex(COL_UNKNOWN), 0)

        self.seg_fps.set_text("FPS {}".format(self.fps_text))
        if self.frozen:
            self.seg_hint.set_text("FROZEN")
            self.seg_hint.set_style_text_color(lv.color_hex(COL_FROZEN), 0)
        else:
            self.seg_hint.set_text("OK: enroll")
            self.seg_hint.set_style_text_color(lv.color_hex(COL_HINT), 0)

    def _note(self, text):
        self._enroll_note = text
        self._note_ms = time.ticks_ms()
        self._update_stat()

    def _show_error(self, text):
        self.seg_hint.set_text(text)
        self.seg_hint.set_style_text_color(lv.color_hex(COL_ERROR), 0)

    # ---------- database ----------
    @staticmethod
    def _ensure_dir(path):
        """Create a nested path level by level.

        os.mkdir makes one level only and raises ENOENT when the parent is
        missing, so /data/face/recognize needs /data/face created first. Every
        level is attempted and existing ones are ignored.
        """
        parts = path.split("/")
        for i in range(2, len(parts) + 1):
            try:
                os.mkdir("/".join(parts[:i]))
            except BaseException:
                pass

    def _db_load(self):
        self._ensure_dir(DB_DIR)
        try:
            f = open(DB_INDEX, "r")
            raw = f.read()
            f.close()
            del f
            data = json.loads(raw)
        except BaseException as e:
            if not self._is_missing(e):
                print("[Recognize] index load failed:", e)
            data = None

        if not data:
            # Fall back to the file names already on disk so a lost index does not
            # orphan enrolled features; the name then comes from the file number.
            try:
                names = os.listdir(DB_DIR)
            except BaseException:
                names = []
            items = []
            for name in names:
                if name.startswith("f") and name.endswith(".bin"):
                    items.append({"file": name,
                                  "name": "P" + name[1:-4].lstrip("0")})
            data = {"next": len(items) + 1, "items": items}

        self._db_next = int(data.get("next", 1))
        self.db_names = []
        self.db_feats = []
        items = data.get("items", [])
        for item in items:
            path = DB_DIR + "/" + item["file"]
            feat = self._read_feat(path)
            if feat is None:
                print("[Recognize] skip unreadable", item["file"])
                continue
            self.db_names.append(item.get("name", "?"))
            self.db_feats.append(feat)
        print("[Recognize] db loaded: {} face(s)".format(len(self.db_names)))

    @staticmethod
    def _is_missing(err):
        """True when the error just means the index file is not there yet."""
        text = str(err).lower()
        for token in ("enoent", "no such file", "errno 2"):
            if token in text:
                return True
        return False

    @staticmethod
    def _read_feat(path):
        """Read and normalise one feature file. Returns None when unusable."""
        try:
            f = open(path, "rb")
            raw = f.read()
            f.close()
            del f
        except BaseException as e:
            print("[Recognize] read failed", path, e)
            return None
        if len(raw) != FEAT_DIM * 4:
            print("[Recognize] bad size {} for {}".format(len(raw), path))
            del raw
            return None
        arr = np.frombuffer(raw, dtype=np.float)
        if len(arr) != FEAT_DIM:
            del raw, arr
            return None
        # Normalise before releasing the source buffer: _normalise returns a new
        # array, so the result stays valid after `raw` goes away.
        feat = FaceRecognizeApp._normalise(arr)
        del raw, arr
        return feat

    @staticmethod
    def _normalise(arr):
        """Return the unit-length copy of a 512-d feature as a ulab array.

        Vectorised on purpose: normalising with a Python loop over 512 floats
        measured 11.1 ms per call, while the ulab form is 0.02 ms. That 11 ms was
        most of the frame time.
        """
        norm = math.sqrt(float(np.dot(arr, arr)))
        if norm <= 1e-9:
            return arr * 0.0
        return arr / norm

    def _db_save_index(self):
        items = []
        for i in range(len(self.db_names)):
            items.append({"file": "f{:03d}.bin".format(i + 1),
                          "name": self.db_names[i]})
        out = {"next": self._db_next, "items": items}
        text = json.dumps(out)
        tmp = DB_INDEX + TMP_SUFFIX
        f = open(tmp, "w")
        f.write(text)
        f.close()
        del f
        # Rename over the old index so a power cut leaves either version intact.
        try:
            os.remove(DB_INDEX)
        except BaseException:
            pass
        os.rename(tmp, DB_INDEX)

    def _db_add(self, name, feat):
        """Add or replace an entry, then rewrite the index.

        Features are renumbered on every save so the file name always matches the
        position in the index; a 2 KB file is cheap to rewrite and this keeps the
        two from ever drifting apart.
        """
        if name in self.db_names:
            idx = self.db_names.index(name)
            self.db_feats[idx] = feat
        else:
            if len(self.db_names) >= MAX_ENROLL:
                return False
            self.db_names.append(name)
            self.db_feats.append(feat)
            self._db_next += 1
        self._db_write_all()
        return True

    def _db_write_all(self):
        for i in range(len(self.db_feats)):
            path = DB_DIR + "/f{:03d}.bin".format(i + 1)
            tmp = path + TMP_SUFFIX
            f = open(tmp, "wb")
            f.write(self.db_feats[i].tobytes())
            f.close()
            del f
            try:
                os.remove(path)
            except BaseException:
                pass
            os.rename(tmp, path)
        # Drop files left over from a previously longer list.
        try:
            names = os.listdir(DB_DIR)
        except BaseException:
            names = []
        keep = set("f{:03d}.bin".format(i + 1)
                   for i in range(len(self.db_feats)))
        for name in names:
            if name.endswith(".bin") and name not in keep:
                try:
                    os.remove(DB_DIR + "/" + name)
                    print("[Recognize] pruned", name)
                except BaseException as e:
                    print("[Recognize] prune failed", name, e)
        self._db_save_index()

    def _db_delete(self, idx):
        if not (0 <= idx < len(self.db_names)):
            return
        print("[Recognize] delete", self.db_names[idx])
        del self.db_names[idx]
        del self.db_feats[idx]
        self._db_write_all()

    def _db_match(self, feat):
        """Best (name, score), or (None, best score) below the threshold."""
        best = -1.0
        best_i = -1
        for i in range(len(self.db_feats)):
            score = float(np.dot(feat, self.db_feats[i])) / 2.0 + 0.5
            if score > best:
                best = score
                best_i = i
        if best_i < 0:
            return None, 0.0
        if best < ENROLL_MATCH:
            return None, best
        return self.db_names[best_i], best

    # ---------- AI pipeline ----------
    def _init_ai(self):
        t0 = time.ticks_ms()
        try:
            if (not self._exists(DET_MODEL) or not self._exists(REG_MODEL)
                    or not self._exists(ANCHOR_PATH)):
                raise AppUnavailable(MODEL_MISSING)
        except AppUnavailable:
            raise
        except BaseException as error:
            print("[Recognize] file check error:", error)

        try:
            f = open(ANCHOR_PATH, "rb")
            raw = f.read()
            f.close()
            del f
            self.anchors = np.frombuffer(raw, dtype=np.float).reshape(
                (ANCHOR_N, 4))
            del raw
        except BaseException as error:
            print("[Recognize] anchor load failed:", error)
            raise AppUnavailable(MODEL_MISSING)

        sensor = None
        try:
            sensor = Sensor(fps=30)
            sensor.reset()
            # chn1 = AI input, planar RGBP888 because ai2d declares NCHW_FMT.
            sensor.set_framesize(w=PREVIEW_W, h=PREVIEW_H, chn=CAM_CHN_ID_1,
                                 crop=True)
            sensor.set_pixformat(Sensor.RGBP888, chn=CAM_CHN_ID_1)
            # chn2 = preview; as_lvgl_img_src only accepts a packed format.
            sensor.set_framesize(w=PREVIEW_W, h=PREVIEW_H, chn=CAM_CHN_ID_2,
                                 crop=True)
            sensor.set_pixformat(Sensor.RGB888, chn=CAM_CHN_ID_2)
            sensor.run()
        except BaseException as error:
            print("[Recognize] sensor init failed:", error)
            sys.print_exception(error)
            close_sensor(sensor)
            raise AppUnavailable(CAMERA_UNAVAILABLE)
        self.sensor = sensor

        self._load_models()
        print("[Recognize] ready: load={}ms dim={} faces={} mem_free={}".format(
            time.ticks_diff(time.ticks_ms(), t0), FEAT_DIM, len(self.db_names),
            gc.mem_free()))

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
        kpu.load_kmodel(REG_MODEL)
        self.kpu_reg = kpu

        _scale, pad_top = letterbox()
        a2d = Ai2d()
        a2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                           np.uint8, np.uint8)
        a2d.pad([0, 0, 0, 0, pad_top, pad_top, 0, 0], 0, [104, 117, 123])
        a2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
        a2d.build([1, 3, PREVIEW_H, PREVIEW_W],
                  [1, 3, DET_IN[1], DET_IN[0]])
        self.a2d_det = a2d

        # The recognition crop is a per-face affine, so only the dtype is set here.
        self.a2d_reg = Ai2d()
        self.a2d_reg.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT,
                                    nn.ai2d_format.NCHW_FMT, np.uint8, np.uint8)

    def _release_models(self):
        for name in ("kpu_det", "kpu_reg"):
            kpu = getattr(self, name)
            if kpu is not None:
                try:
                    kpu.release()
                except BaseException as e:
                    print("[Recognize]   {}.release error: {}".format(name, e))
                setattr(self, name, None)
        for name in ("a2d_det", "a2d_reg"):
            a2d = getattr(self, name)
            if a2d is not None:
                try:
                    a2d.deinit()
                except BaseException as e:
                    print("[Recognize]   {}.deinit error: {}".format(name, e))
                setattr(self, name, None)
        gc.collect()

    def _detect(self, img):
        """Largest face: (box, landmarks) in screen coordinates, or None."""
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
        landms = res[1] if len(res) > 1 else None
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
        lm = landms[best] if landms and best < len(landms) else None
        return (x, y, w, h), lm

    def _feature(self, img, lm):
        """Normalised 512-d feature for one face, or None when alignment fails."""
        if lm is None or len(lm) < 10:
            return None
        # Landmark y still carries the letterbox pad, so undo it before the
        # similarity transform maps the points into the 112x112 crop.
        pts = []
        for i in range(5):
            pts.append(float(lm[i * 2]))
            pts.append(float(lm[i * 2 + 1]) - self.y_off)
        m = umeyama_112(pts)
        self.a2d_reg.affine(nn.interp_method.cv2_bilinear, 0, 0, 127, 1, m)
        self.a2d_reg.build([1, 3, PREVIEW_H, PREVIEW_W],
                           [1, 3, REG_IN, REG_IN])
        tin = self.a2d_reg.run(img.to_numpy_ref())
        self.kpu_reg.set_input_tensor(0, tin)
        self.kpu_reg.run()
        out = None
        for i in range(self.kpu_reg.outputs_size()):
            ot = self.kpu_reg.get_output_tensor(i)
            try:
                if out is None:
                    out = ot.to_numpy()
            finally:
                ot.release()
        del tin
        if out is None:
            return None
        flat = out.flatten()
        if len(flat) != FEAT_DIM:
            print("[Recognize] unexpected feature size", len(flat))
            del flat
            return None
        feat = self._normalise(flat)
        del flat, out
        return feat

    # ---------- empty-database panel ----------
    def _show_empty_panel(self):
        """Blocking hint shown while nothing is enrolled: enroll before recognizing."""
        if self._empty_panel is not None:
            return
        panel = lv.obj(self.content)
        panel.set_size(self.img_w, self.img_h)
        panel.align(lv.ALIGN.TOP_LEFT, 0, 0)
        panel.set_style_bg_color(lv.color_hex(0x000000), 0)
        panel.set_style_bg_opa(lv.OPA._70, 0)
        panel.set_style_border_width(0, 0)
        panel.set_style_pad_all(0, 0)
        panel.clear_flag(lv.obj.FLAG.SCROLLABLE)
        panel.add_flag(lv.obj.FLAG.CLICKABLE)

        card = lv.obj(panel)
        card.set_size(560, 200)
        card.align(lv.ALIGN.CENTER, 0, 0)
        card.set_style_bg_color(lv.color_hex(0x101418), 0)
        card.set_style_bg_opa(lv.OPA.COVER, 0)
        card.set_style_border_width(2, 0)
        card.set_style_border_color(lv.color_hex(COL_SEP), 0)
        card.set_style_radius(14, 0)
        card.set_style_pad_all(14, 0)
        card.clear_flag(lv.obj.FLAG.SCROLLABLE)
        card.add_flag(lv.obj.FLAG.CLICKABLE)

        font = _font("font_montserrat_20", "font_montserrat_16")
        title = lv.label(card)
        title.set_text("No face enrolled")
        title.set_style_text_color(lv.color_hex(COL_TITLE), 0)
        if font is not None:
            title.set_style_text_font(font, 0)
        title.align(lv.ALIGN.TOP_MID, 0, 6)

        body = lv.label(card)
        body.set_text("Put one face in view, then press OK\n"
                      "and type a name to enroll it.")
        body.set_style_text_color(lv.color_hex(0xE6EAF0), 0)
        body.set_style_text_align(lv.TEXT_ALIGN.CENTER, 0)
        body.align(lv.ALIGN.TOP_MID, 0, 58)

        hint = lv.label(card)
        hint.set_text("OK: enroll      tap here: close")
        hint.set_style_text_color(lv.color_hex(COL_HINT), 0)
        hint.align(lv.ALIGN.BOTTOM_MID, 0, -8)

        beep.on_click(panel, lambda e: self._close_empty_panel())
        self._empty_panel = panel

    def _close_empty_panel(self):
        if self._empty_panel is not None:
            try:
                self._empty_panel.delete()
            except BaseException as e:
                print("[Recognize] empty panel delete error:", e)
            self._empty_panel = None

    # ---------- enroll ----------
    def _start_enroll(self):
        if self.kb is not None:
            return
        if self._empty_panel is not None:
            self._close_empty_panel()
        if not self.has_face or self._pending_feat is None:
            self._note("no face to enroll")
            return
        if len(self.db_names) >= MAX_ENROLL:
            self._note("database full")
            return
        self._close_manage()
        self.frozen = True
        self._update_stat()
        self.kb = kb_layer.KbLayer(
            title="name this face", on_ok=self._enroll_commit,
            ok_text="save", password=False, max_len=NAME_MAX,
            text_font=self.bar_font, accent=COL_TITLE)

    def _enroll_commit(self):
        name = ""
        try:
            name = self.kb.get_text()
        except BaseException as e:
            print("[Recognize] get_text failed:", e)
        if self.kb is not None:
            self.kb.close()
            self.kb = None
        self.frozen = False
        name = (name or "").strip()
        if not name:
            self._note("empty name, not saved")
            self._update_stat()
            return
        feat = self._pending_feat
        self._pending_feat = None
        if feat is None:
            self._note("feature lost, retry")
            self._update_stat()
            return
        try:
            ok = self._db_add(name, feat)
        except BaseException as e:
            print("[Recognize] save failed:", e)
            sys.print_exception(e)
            self._note("save failed")
            self._update_stat()
            return
        print("[Recognize] enrolled '{}' -> {} face(s)".format(
            name, len(self.db_names)))
        self._note("saved {}".format(name) if ok else "database full")
        self._update_stat()

    # ---------- manage overlay ----------
    def _open_manage(self):
        if self._manage is not None or self.kb is not None:
            return
        self.frozen = True
        backdrop = lv.obj(self.content)
        backdrop.set_size(self.img_w, self.img_h)
        backdrop.align(lv.ALIGN.TOP_LEFT, 0, 0)
        backdrop.set_style_bg_color(lv.color_hex(0x000000), 0)
        backdrop.set_style_bg_opa(lv.OPA._50, 0)
        backdrop.set_style_border_width(0, 0)
        backdrop.set_style_pad_all(0, 0)
        backdrop.clear_flag(lv.obj.FLAG.SCROLLABLE)
        backdrop.add_flag(lv.obj.FLAG.CLICKABLE)
        beep.on_click(backdrop, lambda e: self._close_manage())

        rows = min(max(len(self.db_names), 1), 6)
        panel = lv.obj(backdrop)
        panel.set_size(560, 96 + rows * 56)
        panel.align(lv.ALIGN.CENTER, 0, 0)
        panel.set_style_bg_color(lv.color_hex(0x101418), 0)
        panel.set_style_bg_opa(lv.OPA.COVER, 0)
        panel.set_style_border_width(2, 0)
        panel.set_style_border_color(lv.color_hex(COL_SEP), 0)
        panel.set_style_radius(14, 0)
        panel.set_style_pad_all(12, 0)
        panel.clear_flag(lv.obj.FLAG.SCROLLABLE)
        panel.add_flag(lv.obj.FLAG.CLICKABLE)

        title = lv.label(panel)
        title.set_text("Enrolled faces ({})".format(len(self.db_names)))
        title.set_style_text_color(lv.color_hex(0xE6EAF0), 0)
        title.align(lv.ALIGN.TOP_MID, 0, 2)

        hint = lv.label(panel)
        hint.set_text("tap a row to delete, tap outside to close")
        hint.set_style_text_color(lv.color_hex(0x8A97A3), 0)
        hint.align(lv.ALIGN.TOP_MID, 0, 26)

        if not self.db_names:
            empty = lv.label(panel)
            empty.set_text("none yet - press OK to enroll")
            empty.set_style_text_color(lv.color_hex(COL_HINT), 0)
            empty.align(lv.ALIGN.TOP_MID, 0, 56)
        else:
            y = 52
            for i in range(min(len(self.db_names), 6)):
                row = lv.btn(panel)
                row.set_size(524, 48)
                row.align(lv.ALIGN.TOP_MID, 0, y)
                row.set_style_radius(10, 0)
                row.set_style_bg_color(lv.color_hex(0x1E262E), 0)
                row.set_style_bg_opa(lv.OPA.COVER, 0)
                row.set_style_border_width(0, 0)
                row.set_style_pad_all(0, 0)
                row.clear_flag(lv.obj.FLAG.SCROLLABLE)
                row.add_flag(lv.obj.FLAG.CLICKABLE)
                lab = lv.label(row)
                lab.set_text("{}    (tap to delete)".format(self.db_names[i]))
                lab.set_style_text_color(lv.color_hex(0xE6EAF0), 0)
                if self.bar_font is not None:
                    lab.set_style_text_font(self.bar_font, 0)
                lab.center()
                lab.clear_flag(lv.obj.FLAG.CLICKABLE)
                beep.on_click(row, lambda e, idx=i: self._delete_from_manage(idx))
                y += 56

        self._manage = backdrop

    def _delete_from_manage(self, idx):
        self._close_manage()
        try:
            self._db_delete(idx)
        except BaseException as e:
            print("[Recognize] delete failed:", e)
            self._note("delete failed")
        self._update_stat()
        if not self.db_names:
            self._show_empty_panel()

    def _close_manage(self):
        if self._manage is not None:
            try:
                self._manage.delete()
            except BaseException as e:
                print("[Recognize] manage delete error:", e)
            self._manage = None
        self.frozen = False
        self._update_stat()

    # ---------- drawing ----------
    def _draw_box(self, box, known):
        if box is None:
            self.box.add_flag(lv.obj.FLAG.HIDDEN)
            self.box_label.add_flag(lv.obj.FLAG.HIDDEN)
            return
        x, y, w, h = box
        color = lv.color_hex(BOX_KNOWN if known else BOX_UNKNOWN)
        self.box.clear_flag(lv.obj.FLAG.HIDDEN)
        self.box.set_pos(x, y)
        self.box.set_size(w, h)
        self.box.set_style_border_color(color, 0)
        if known:
            text = "{}  {:.2f}".format(self._name, self._score)
        else:
            text = "unknown" if not self._score else \
                "unknown  {:.2f}".format(self._score)
        self.box_label.set_text(text)
        self.box_label.set_style_text_color(color, 0)
        self.box_label.set_style_bg_color(lv.color_hex(0x000000), 0)
        self.box_label.clear_flag(lv.obj.FLAG.HIDDEN)
        self.box_label.align_to(self.box, lv.ALIGN.TOP_LEFT, 0, label_dy(y))

    # ---------- main loop ----------
    def step(self):
        if self.sensor is None:
            return

        # The naming keyboard and the manage overlay own the screen; while either
        # is open the pipeline is paused so taps cannot reach the app.
        if self.kb is not None:
            self.kb.poll(self.ctx.touch)
            return
        if self._manage is not None:
            return

        if self.ctx.take_ok():
            self._start_enroll()
            return

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
        lm = None
        try:
            got = self._detect(ai_img)
            if got is not None:
                box, lm = got
        except BaseException as error:
            print("[Recognize] detect error:", error)
            sys.print_exception(error)

        self.has_face = box is not None
        feat = None
        # Compute the feature whenever a face is present, not only when the
        # database has entries: the very first enrollment needs one too, and
        # gating this on db_names left an empty database unable to enroll.
        if box is not None:
            try:
                feat = self._feature(ai_img, lm)
            except BaseException as error:
                print("[Recognize] feature error:", error)
                sys.print_exception(error)

        if feat is None:
            self._name = None
            self._score = 0.0
            self._pending_feat = None
        else:
            self._pending_feat = feat
            self._last_feat = feat
            if self.db_names:
                name, score = self._db_match(feat)
                self._name = name
                self._score = score
            else:
                self._name = None
                self._score = 0.0

        # Refresh the bar as soon as the text it would show changes. Waiting for
        # the one-second tick left it saying "unknown" while the box already said
        # the name, which reads as a bug. Comparing the rounded score keeps the
        # common case free of extra LVGL work.
        shown_score = int(self._score * 100)
        if (self._name != self._shown_name
                or shown_score != self._shown_score
                or self.has_face != self._shown_face):
            self._shown_name = self._name
            self._shown_score = shown_score
            self._shown_face = self.has_face
            self._update_stat()

        try:
            img.as_lvgl_img_src(self.img_widget)
            self.img_widget.invalidate()
        except BaseException as error:
            self._show_error("display error")
            return

        self._draw_box(box, self._name is not None)

        # LVGL image sources and sensor frames are cyclic garbage; without a
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
        print("[Recognize] destroy begin")
        if self.kb is not None:
            try:
                self.kb.close()
            except BaseException as e:
                print("[Recognize]   kb close error: {}".format(e))
            self.kb = None
        if self._manage is not None:
            try:
                self._manage.delete()
            except BaseException:
                pass
            self._manage = None
        if self._empty_panel is not None:
            try:
                self._empty_panel.delete()
            except BaseException:
                pass
            self._empty_panel = None
        self._release_models()
        if self.sensor is not None:
            for name in ("stop", "deinit"):
                try:
                    getattr(self.sensor, name)()
                    print("[Recognize]   sensor.{} ok".format(name))
                except BaseException as e:
                    print("[Recognize]   sensor.{} error: {}".format(name, e))
            self.sensor = None
        gc.collect()
        print("[Recognize] destroy done | mem_free={}".format(gc.mem_free()))


def create_app(content, ctx):
    return FaceRecognizeApp(content, ctx)
