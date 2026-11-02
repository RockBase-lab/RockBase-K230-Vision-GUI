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
# Threshold: the same subject frames apart measured min 0.9827 in one session,
# but a photo-enrolled face matched after a cold restart measures 0.936-0.961,
# because the camera's auto exposure and white balance start from different
# defaults than in the enrollment session. Different real subjects top out at
# 0.7054. ENROLL_MATCH is 0.85, inside that gap, so a restart does not turn a
# known face into "unknown" while an unrelated face still cannot pass.
#
# An identity owns up to SAMPLES_PER_NAME feature vectors, one per viewing
# angle, and the match takes the best score over all of them. Names may repeat
# in the database: that is the whole mechanism, so the matcher needs no extra
# branch. Three samples cost three dot products (about 0.03 ms) on a 39 ms
# frame, which is why the limit is set by recognition accuracy, not speed.
#
# Enrollment runs face_liveness_rgb.kmodel **once** to tag each entry as
# "live" or "photo" (a printed photo held up to the camera). The steady loop
# never runs the liveness model, so recognition fps is unchanged. The PHOTO
# switch in the top bar decides whether photo-tagged entries take part in
# matching; the setting persists in the database index.

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
from audio import greet
from apps.error import (AppUnavailable, CAMERA_UNAVAILABLE, NoSignalCounter,
                        close_sensor)

PREVIEW_W = 800
PREVIEW_H = 480

DET_MODEL = "/sdcard/examples/kmodel/face_detection_320.kmodel"
REG_MODEL = "/sdcard/examples/kmodel/face_recognition_mobile.kmodel"
LIVE_MODEL = "/sdcard/examples/kmodel/face_liveness_rgb.kmodel"
ANCHOR_PATH = "/sdcard/examples/utils/prior_data_320.bin"
DET_IN = [320, 320]
REG_IN = 112
LIVE_IN = 112
ANCHOR_N = 4200
FEAT_DIM = 512

DET_CONF = 0.50
NMS_TH = 0.2

# Same-subject scores fall to 0.936 after a cold restart, while different
# enrolled subjects measured at most 0.7054. 0.85 sits in that gap.
ENROLL_MATCH = 0.85

# Enrollment-time liveness gate. Index 1 of the model output is the live
# probability: a live face measured 0.9989-0.9997, a screen photo 0.0001-0.0004,
# so 0.50 sits in an empty band. The crop must be **expanded** before the
# resize; a tight crop leaves live and photo scores indistinguishable.
CROP_PAD_W = 224
CROP_PAD_H = 192
PAD_GREY = [128, 128, 128]
LIVE_REAL = 0.50

# The feature database lives on /data, not /sdcard: the SD card holds the
# deployed product content and has about 76 MB free, while /data has 28 GB. The
# path mirrors the /data/<app>/<topic> layout used by photos and cache.
DB_DIR = "/data/face/recognize"
DB_INDEX = DB_DIR + "/db.json"
# Greeting WAVs live beside the features but in their own directory, because the
# feature files are renumbered on every save while these are keyed by name.
VOICE_DIR = DB_DIR + "/voice"
TMP_SUFFIX = ".tmp"

# Feature vectors per identity, captured at different viewing angles. The
# match takes the maximum over them, so extra angles raise the score for the
# right person; they also give an impostor more chances, which is why the
# threshold was re-measured rather than reused.
SAMPLES_PER_NAME = 3

# A sample session ends when this many milliseconds pass without a new sample.
# Repositioning the head counts as activity, so the timer only runs out when
# the user walks away.
ADD_WINDOW_MS = 20000

# Total feature vectors across all identities, i.e. 200 / 3 = 66 people.
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
                 "  /sdcard/examples/kmodel/face_liveness_rgb.kmodel\n"
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


def pad_param(src_w, src_h, dst_w, dst_h):
    """Centre a src rectangle in dst: (top, bottom, left, right, scale)."""
    ratio = min(float(dst_w) / src_w, float(dst_h) / src_h)
    new_w = int(src_w * ratio)
    new_h = int(src_h * ratio)
    pad_w = (dst_w - new_w) // 2
    pad_h = (dst_h - new_h) // 2
    return pad_h, dst_h - new_h - pad_h, pad_w, dst_w - new_w - pad_w, ratio


def expand_box(box):
    """Grow the box by the crop padding, clamped to the frame.

    Clamping matters at the borders: a crop rectangle reading past the buffer
    is rejected by ai2d or produces garbage.
    """
    x, y, w, h = box
    cw = w + CROP_PAD_W
    ch = h + CROP_PAD_H
    cx = x - (cw - w) // 2
    cy = y - (ch - h) // 2
    if cx < 0:
        cw += cx
        cx = 0
    if cy < 0:
        ch += cy
        cy = 0
    if cx + cw > PREVIEW_W:
        cw = PREVIEW_W - cx
    if cy + ch > PREVIEW_H:
        ch = PREVIEW_H - cy
    if cw <= 1 or ch <= 1:
        return None
    return cx, cy, cw, ch


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
        self.kpu_live = None
        self.a2d_det = None
        self.a2d_reg = None
        self.a2d_live = None
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
        self._last_box = None        # screen-space box of the last detection
        self._name = None            # matched name, None = unknown
        self._score = 0.0
        self._enroll_note = ""
        self._note_ms = 0
        # Last values pushed to the bar, so it is only rebuilt when its text
        # would actually change.
        self._shown_name = None
        self._shown_score = -1
        self._shown_face = None
        self._shown_people = -1

        self.kb = None               # naming keyboard (None = closed)
        self._pending_feat = None
        self._pending_src = "live"   # source tag of the enrollment in progress
        # Multi-sample session: while _add_name is set, OK appends another
        # viewing angle to that identity instead of starting a new enrollment.
        self._add_name = None
        self._add_src = "live"
        self._add_n = 0
        self._add_ms = 0
        self._manage = None          # manage overlay (None = closed)
        self._empty_panel = None     # shown while the database is empty

        # Database: names and features loaded once, features normalised on load so
        # the per-frame compare is a plain dot product. db_srcs[i] is "live" or
        # "photo", parallel to db_names / db_feats.
        self.db_names = []
        self.db_feats = []
        self.db_srcs = []
        self._db_next = 1
        # When False, entries enrolled from a photo are skipped by the matcher.
        # Persisted in the index so the choice survives a restart.
        self.photos_allowed = True
        self._switch = None       # top-bar PHOTO switch (None = not built)

        # Greeting state. voice_on is persisted with photos_allowed; the greeter
        # only exists while a WAV is playing, so an app that never speaks holds
        # no audio stream.
        self.voice_on = True
        self.greeter = greet.Greeter(VOICE_DIR)
        # Greeting synthesis queue. The three TTS models cost about 6 s to load,
        # so one instance is kept alive across the whole batch and a single name
        # is built per step: the UI advances between names instead of freezing
        # for the full run.
        self._synth = None
        self._voice_queue = []
        self._voice_done = 0
        self._voice_total = 0

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
        # The index may carry photos_allowed=False; re-drive the switch from it.
        self._sync_switch()
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

        # PHOTO switch: whether photo-enrolled entries take part in matching.
        # The switch is clickable itself, so a tap here does not fall through to
        # stat_btn and open the manage overlay.
        self.seg_photo = seg("PHOTO", COL_HINT)
        self._switch = lv.switch(self.stat_btn)
        self._switch.set_size(46, 24)
        self._switch.clear_flag(lv.obj.FLAG.SCROLLABLE)
        # CHECKABLE makes LVGL toggle the state itself on click; the callback
        # then mirrors that into photos_allowed.
        self._switch.add_flag(lv.obj.FLAG.CHECKABLE)
        self._switch.set_style_bg_color(lv.color_hex(0x1E262E), 0)
        beep.on_click(self._switch, lambda e: self._toggle_photos())

        self.seg_hint = seg("OK: enroll", COL_HINT)
        self._sync_switch()

    def _sync_switch(self):
        """Push photos_allowed onto the switch and its label colour."""
        if self._switch is None:
            return
        if self.photos_allowed:
            self._switch.add_state(lv.STATE.CHECKED)
            self.seg_photo.set_style_text_color(lv.color_hex(COL_KNOWN), 0)
        else:
            self._switch.clear_state(lv.STATE.CHECKED)
            self.seg_photo.set_style_text_color(lv.color_hex(COL_UNKNOWN), 0)

    def _toggle_photos(self):
        # CHECKABLE already flipped the widget state on click, so read it back
        # rather than inverting the old value (inverting would undo it).
        # has_state verified present on this binding.
        self.photos_allowed = bool(self._switch.has_state(lv.STATE.CHECKED))
        self._sync_switch()
        # Persist immediately: the setting is user intent and must survive a
        # restart even if no enrollment happens afterwards.
        try:
            self._db_save_index()
        except BaseException as e:
            print("[Recognize] toggle save failed:", e)
        self._note("photos {}".format("allowed" if self.photos_allowed
                                      else "excluded"))
        # The next match pass re-runs with the new filter; a name that just got
        # excluded drops out on the following frame.
        self._update_stat()

    def _update_stat(self):
        # Counting identities is O(samples) and this runs several times a second
        # while a face is in view, so only rebuild the title when the count moves.
        people = len(self._db_names_unique()) if self.db_names else 0
        if people != self._shown_people:
            self._shown_people = people
            if people:
                self.seg_title.set_text("Recognize {}".format(people))
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
        elif self._add_name is not None:
            self.seg_hint.set_text("OK: add {}/{}".format(
                self._add_n, SAMPLES_PER_NAME))
            self.seg_hint.set_style_text_color(lv.color_hex(COL_KNOWN), 0)
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
        # An index written before the photo feature has no key: default on, so
        # upgrading never silently narrows an existing database.
        self.photos_allowed = bool(data.get("photos_allowed", True))
        self.voice_on = bool(data.get("voice_on", True))
        self.db_names = []
        self.db_feats = []
        self.db_srcs = []
        items = data.get("items", [])
        for item in items:
            path = DB_DIR + "/" + item["file"]
            feat = self._read_feat(path)
            if feat is None:
                print("[Recognize] skip unreadable", item["file"])
                continue
            self.db_names.append(item.get("name", "?"))
            self.db_feats.append(feat)
            self.db_srcs.append("photo" if item.get("src") == "photo"
                                else "live")
        print("[Recognize] db loaded: {} face(s) / {} sample(s), "
              "photos_allowed={}".format(
                  len(self._db_names_unique()), len(self.db_names),
                  self.photos_allowed))

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
                          "name": self.db_names[i],
                          "src": self.db_srcs[i]})
        out = {"next": self._db_next, "items": items,
               "photos_allowed": self.photos_allowed,
               "voice_on": self.voice_on}
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

    def _db_count(self, name):
        """Number of samples stored for one identity."""
        n = 0
        for i in range(len(self.db_names)):
            if self.db_names[i] == name:
                n += 1
        return n

    def _db_names_unique(self):
        """Identity names in first-seen order, without repeating a name."""
        out = []
        for name in self.db_names:
            if name not in out:
                out.append(name)
        return out

    def _db_drop_name(self, name):
        """Remove every sample of one identity from memory. No disk write."""
        removed = 0
        i = len(self.db_names) - 1
        while i >= 0:
            if self.db_names[i] == name:
                del self.db_names[i]
                del self.db_feats[i]
                del self.db_srcs[i]
                removed += 1
            i -= 1
        return removed

    def _db_append(self, name, feat, src):
        """Append one sample and persist. Returns its 1-based index, or -1.

        Duplicate names are the multi-angle mechanism, so nothing here replaces
        an existing feature. Re-enrolling an identity drops its old samples
        first, which the caller does.
        """
        if len(self.db_names) >= MAX_ENROLL:
            return -1
        self.db_names.append(name)
        self.db_feats.append(feat)
        self.db_srcs.append(src)
        self._db_next += 1
        self._db_write_all()
        return self._db_count(name)

    def _db_delete_name(self, name):
        """Delete one identity, all of its samples and its greeting."""
        removed = self._db_drop_name(name)
        if removed:
            print("[Recognize] delete '{}' ({} sample(s))".format(name, removed))
            self._db_write_all()
        # The greeting belongs to the identity, so it goes with it; otherwise a
        # later enrollment of the same name would silently reuse stale speech.
        try:
            os.remove(self._voice_path(name))
        except BaseException:
            pass
        return removed

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

    def _db_match(self, feat):
        """Best (name, score), or (None, best score) below the threshold.

        Identity samples share a name, so the global maximum over the flat list
        is already the best score over every angle of every identity. Photo
        samples are skipped while the PHOTO switch is off: that test is one list
        lookup, so the hot loop cost is unchanged.
        """
        best = -1.0
        best_i = -1
        for i in range(len(self.db_feats)):
            if (not self.photos_allowed and self.db_srcs[i] == "photo"):
                continue
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
        print("[Recognize] ready: load={}ms dim={} faces={} samples={} "
              "mem_free={}".format(
                  time.ticks_diff(time.ticks_ms(), t0), FEAT_DIM,
                  len(self._db_names_unique()), len(self.db_names),
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

        # The liveness model only runs once per enrollment, never in the steady
        # loop. It is optional: without it every enrollment is tagged "live" and
        # the photo gate simply reports real.
        if self._exists(LIVE_MODEL):
            try:
                kpu = nn.kpu()
                kpu.load_kmodel(LIVE_MODEL)
                self.kpu_live = kpu
                self.a2d_live = Ai2d()
                self.a2d_live.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT,
                                             nn.ai2d_format.NCHW_FMT,
                                             np.uint8, np.uint8)
            except BaseException as error:
                print("[Recognize] liveness model load failed:", error)
                self.kpu_live = None
        else:
            print("[Recognize] liveness kmodel absent, enroll tags all live")

    def _release_models(self):
        for name in ("kpu_det", "kpu_reg", "kpu_live"):
            kpu = getattr(self, name)
            if kpu is not None:
                try:
                    kpu.release()
                except BaseException as e:
                    print("[Recognize]   {}.release error: {}".format(name, e))
                setattr(self, name, None)
        for name in ("a2d_det", "a2d_reg", "a2d_live"):
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

    def _liveness(self, img, box):
        """One-shot live probability for enrollment tagging, or None on failure.

        Order follows the model contract: crop the expanded box, resize, then
        pad with mid grey. Padding before the resize changes the scores.
        Index 1 of the [1, 2] softmax output is the live class.
        """
        if self.kpu_live is None or self.a2d_live is None:
            return None
        crop = expand_box(box)
        if crop is None:
            return None
        cx, cy, cw, ch = crop
        top, bottom, left, right, _scale = pad_param(cw, ch, LIVE_IN, LIVE_IN)
        self.a2d_live.crop(cx, cy, cw, ch)
        self.a2d_live.resize(nn.interp_method.tf_bilinear,
                             nn.interp_mode.half_pixel)
        self.a2d_live.pad([0, 0, 0, 0, top, bottom, left, right], 0, PAD_GREY)
        self.a2d_live.build([1, 3, PREVIEW_H, PREVIEW_W],
                            [1, 3, LIVE_IN, LIVE_IN])
        tin = self.a2d_live.run(img.to_numpy_ref())
        self.kpu_live.set_input_tensor(0, tin)
        self.kpu_live.run()
        flat = None
        for i in range(self.kpu_live.outputs_size()):
            ot = self.kpu_live.get_output_tensor(i)
            try:
                arr = ot.to_numpy()
                if flat is None:
                    flat = arr.flatten()
            finally:
                ot.release()
        del tin
        if flat is None or len(flat) == 0:
            return None
        score = float(flat[1]) if len(flat) > 1 else float(flat[0])
        del flat
        return score

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
        # One-shot liveness on the current frame decides the source tag. A face
        # that fails it is a printed photo held to the camera: allowed and
        # tagged "photo" while the switch is on, refused while it is off.
        self._pending_src = "live"
        try:
            ai_img = self.sensor.snapshot(chn=CAM_CHN_ID_1)
            score = self._liveness(ai_img, self._last_box)
        except BaseException as error:
            print("[Recognize] liveness check failed:", error)
            score = None
        if score is not None and score < LIVE_REAL:
            if not self.photos_allowed:
                self._note("photo blocked: enable PHOTO")
                self._update_stat()
                return
            self._pending_src = "photo"
            self._note("photo face detected")
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
        # Typing an existing name is a deliberate re-enrollment: drop the old
        # samples so the identity is rebuilt from the new angles instead of
        # keeping stale ones mixed with fresh ones.
        if self._db_count(name):
            print("[Recognize] re-enroll '{}', dropping {} old sample(s)".format(
                name, self._db_drop_name(name)))
        try:
            n = self._db_append(name, feat, self._pending_src)
        except BaseException as e:
            print("[Recognize] save failed:", e)
            sys.print_exception(e)
            self._note("save failed")
            self._update_stat()
            return
        if n < 0:
            self._note("database full")
            self._update_stat()
            return
        print("[Recognize] enrolled '{}' [{}] sample {}/{}".format(
            name, self._pending_src, n, SAMPLES_PER_NAME))
        self._begin_add_session(name, self._pending_src, n)

    # ---------- multi-angle sample session ----------
    def _begin_add_session(self, name, src, n):
        """Keep collecting angles: OK appends a sample without asking the name."""
        self._add_name = name
        self._add_src = src
        self._add_n = n
        self._add_ms = time.ticks_ms()
        self._note("{} {}/{} - turn head".format(name, n, SAMPLES_PER_NAME))

    def _end_add_session(self):
        self._add_name = None
        self._add_src = "live"
        self._add_n = 0

    def _add_sample(self):
        """Save the current frame as the next angle of the open session."""
        name = self._add_name
        if name is None:
            return
        if not self.has_face or self._pending_feat is None:
            # No face right now: say so and restart the timer, because the user
            # is probably repositioning rather than walking away.
            self._add_ms = time.ticks_ms()
            self._note("no face - {}".format(name))
            return
        try:
            n = self._db_append(name, self._pending_feat, self._add_src)
        except BaseException as e:
            print("[Recognize] add sample failed:", e)
            sys.print_exception(e)
            self._end_add_session()
            self._note("save failed")
            return
        if n < 0:
            self._end_add_session()
            self._note("database full")
            return
        print("[Recognize] '{}' sample {}/{}".format(name, n, SAMPLES_PER_NAME))
        self._add_n = n
        if n >= SAMPLES_PER_NAME:
            self._end_add_session()
            self._note("{} {}/{} done".format(name, n, SAMPLES_PER_NAME))
            # All angles are in: queue the greeting. It is built on a later
            # frame, not here, so this frame still renders the note.
            if self.voice_on:
                self._queue_voices([name])
        else:
            self._add_ms = time.ticks_ms()
            self._note("{} {}/{} - turn".format(name, n, SAMPLES_PER_NAME))

    # ---------- greeting voice ----------
    def _voice_path(self, name):
        return VOICE_DIR + "/" + name + ".wav"

    def _has_voice(self, name):
        try:
            return os.stat(self._voice_path(name))[6] > 44
        except BaseException:
            return False

    def _make_greeting(self, name):
        """Synthesise the greeting WAV for `name` with the shared Synth.

        Returns the PCM byte count, or -1 when the model is unavailable.
        """
        from audio import tts
        if self._synth is None:
            self._synth = tts.Synth()
        return self._synth.text_to_wav(tts.speak_text(name),
                                       self._voice_path(name))

    def _missing_voices(self):
        """Identities with no greeting on disk, in database order."""
        return [n for n in self._db_names_unique() if not self._has_voice(n)]

    def _queue_voices(self, names):
        """Queue names for greeting synthesis; already-voiced ones are skipped."""
        todo = [n for n in names if not self._has_voice(n)]
        if not todo:
            return
        self._voice_queue = todo
        self._voice_done = 0
        self._voice_total = len(todo)
        self._note("voice 0/{}".format(self._voice_total))

    def _step_voice(self):
        """Build one queued greeting. Returns True when it did work.

        Called once per frame so the preview keeps advancing between names. The
        first call also loads the three models (about 6 s) and shows its own
        note so the stall is explained rather than looking like a hang.
        """
        if not self._voice_queue:
            return False
        name = self._voice_queue.pop(0)
        try:
            t0 = time.ticks_ms()
            size = self._make_greeting(name)
            print("[Recognize] greeting '{}' {} B in {} ms".format(
                name, size, time.ticks_diff(time.ticks_ms(), t0)))
        except BaseException as e:
            print("[Recognize] greeting for '{}' failed: {}".format(name, e))
            sys.print_exception(e)
            size = 0
        self._voice_done += 1
        if self._voice_queue:
            self._note("voice {}/{}".format(self._voice_done,
                                            self._voice_total))
        else:
            self._close_synth()
            self._note("voices ready {}/{}".format(self._voice_done,
                                                   self._voice_total))
        return True

    def _close_synth(self):
        """Release the three TTS models. A missed release leaks native memory."""
        if self._synth is None:
            return
        try:
            self._synth.close()
        except BaseException as e:
            print("[Recognize] synth close failed:", e)
        self._synth = None

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

        # One extra row for the VOICE toggle above the identity list, plus one
        # more when there are greetings to rebuild and the switch is on.
        rows = min(max(len(self._db_names_unique()), 1), 6)
        missing = self._missing_voices() if self.voice_on else []
        extra = 152 + (38 if missing else 0)
        panel = lv.obj(backdrop)
        panel.set_size(560, extra + rows * 56)
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
        title.set_text("Enrolled faces ({})".format(
            len(self._db_names_unique())))
        title.set_style_text_color(lv.color_hex(0xE6EAF0), 0)
        title.align(lv.ALIGN.TOP_MID, 0, 2)

        hint = lv.label(panel)
        hint.set_text("tap a row to delete, tap outside to close")
        hint.set_style_text_color(lv.color_hex(0x8A97A3), 0)
        hint.align(lv.ALIGN.TOP_MID, 0, 26)

        # VOICE row: speak the matched name. Kept here rather than in the top
        # bar, which is already full at 800 px.
        voice_row = lv.btn(panel)
        voice_row.set_size(524, 44)
        voice_row.align(lv.ALIGN.TOP_MID, 0, 48)
        voice_row.set_style_radius(10, 0)
        voice_row.set_style_bg_color(lv.color_hex(0x16202A), 0)
        voice_row.set_style_bg_opa(lv.OPA.COVER, 0)
        voice_row.set_style_border_width(0, 0)
        voice_row.set_style_pad_all(0, 0)
        voice_row.clear_flag(lv.obj.FLAG.SCROLLABLE)
        voice_row.add_flag(lv.obj.FLAG.CLICKABLE)
        vlab = lv.label(voice_row)
        vlab.set_text("VOICE  say the name")
        vlab.set_style_text_color(
            lv.color_hex(COL_KNOWN if self.voice_on else COL_UNKNOWN), 0)
        if self.bar_font is not None:
            vlab.set_style_text_font(self.bar_font, 0)
        vlab.align(lv.ALIGN.LEFT_MID, 12, 0)
        vlab.clear_flag(lv.obj.FLAG.CLICKABLE)
        voice_sw = lv.switch(voice_row)
        voice_sw.set_size(46, 24)
        voice_sw.align(lv.ALIGN.RIGHT_MID, -12, 0)
        voice_sw.clear_flag(lv.obj.FLAG.SCROLLABLE)
        voice_sw.add_flag(lv.obj.FLAG.CHECKABLE)
        voice_sw.set_style_bg_color(lv.color_hex(0x1E262E), 0)
        if self.voice_on:
            voice_sw.add_state(lv.STATE.CHECKED)
        beep.on_click(voice_sw, lambda e: self._toggle_voice(voice_sw))

        # Identities enrolled before the greeting feature have no WAV, so give
        # the user an explicit rebuild instead of silently greeting nobody.
        if missing:
            fix = lv.btn(panel)
            fix.set_size(524, 34)
            fix.align(lv.ALIGN.TOP_MID, 0, 96)
            fix.set_style_radius(8, 0)
            fix.set_style_bg_color(lv.color_hex(0x20303C), 0)
            fix.set_style_bg_opa(lv.OPA.COVER, 0)
            fix.set_style_border_width(0, 0)
            fix.set_style_pad_all(0, 0)
            fix.clear_flag(lv.obj.FLAG.SCROLLABLE)
            fix.add_flag(lv.obj.FLAG.CLICKABLE)
            flab = lv.label(fix)
            flab.set_text("make {} missing voice(s)".format(len(missing)))
            flab.set_style_text_color(lv.color_hex(COL_FPS), 0)
            flab.center()
            flab.clear_flag(lv.obj.FLAG.CLICKABLE)
            beep.on_click(fix, lambda e: self._rebuild_voices(missing))

        names = self._db_names_unique()
        if not names:
            empty = lv.label(panel)
            empty.set_text("none yet - press OK to enroll")
            empty.set_style_text_color(lv.color_hex(COL_HINT), 0)
            empty.align(lv.ALIGN.TOP_MID, 0, 104)
        else:
            y = 100 if not missing else 138
            for name in names[:6]:
                cnt = self._db_count(name)
                # The tag describes the identity, so it warns whenever any of
                # its samples came from a photo.
                has_photo = False
                has_live = False
                for i in range(len(self.db_names)):
                    if self.db_names[i] != name:
                        continue
                    if self.db_srcs[i] == "photo":
                        has_photo = True
                    else:
                        has_live = True
                tag = " [photo]" if has_photo else ""
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
                lab.set_text("{}{} x{}    (tap to delete)".format(
                    name, tag, cnt))
                # Dim an identity only when every sample is excluded, i.e. it
                # has no live angle to fall back on.
                dimmed = has_photo and not has_live and not self.photos_allowed
                lab.set_style_text_color(
                    lv.color_hex(COL_UNKNOWN if dimmed else 0xE6EAF0), 0)
                if self.bar_font is not None:
                    lab.set_style_text_font(self.bar_font, 0)
                lab.center()
                lab.clear_flag(lv.obj.FLAG.CLICKABLE)
                beep.on_click(row,
                              lambda e, nm=name: self._delete_from_manage(nm))
                y += 56

        self._manage = backdrop

    def _toggle_voice(self, sw):
        """VOICE switch handler: persist, and stop talking immediately if off."""
        self.voice_on = bool(sw.has_state(lv.STATE.CHECKED))
        if not self.voice_on:
            try:
                self.greeter.stop()
            except BaseException as e:
                print("[Recognize] greeter stop failed:", e)
        try:
            self._db_save_index()
        except BaseException as e:
            print("[Recognize] voice setting save failed:", e)
        self._close_manage()
        self._note("voice {}".format("on" if self.voice_on else "off"))

    def _rebuild_voices(self, names):
        """Start the batch from the manage panel, then get out of the way."""
        self._close_manage()
        self._queue_voices(names)

    def _delete_from_manage(self, name):
        self._close_manage()
        try:
            self._db_delete_name(name)
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
            # An open sample session turns OK into "one more angle" instead of
            # starting a new enrollment, so the name is only typed once.
            if self._add_name is not None:
                self._add_sample()
            else:
                self._start_enroll()
            return

        if (self._add_name is not None
                and time.ticks_diff(time.ticks_ms(),
                                    self._add_ms) > ADD_WINDOW_MS):
            print("[Recognize] sample session for '{}' timed out".format(
                self._add_name))
            name = self._add_name
            n = self._add_n
            self._end_add_session()
            self._note("{} {}/{} keep".format(name, n, SAMPLES_PER_NAME))

        if self.frozen:
            return

        # Deferred greeting synthesis: one frame of UI feedback has been shown,
        # so blocking here is a visible pause rather than a silent freeze.
        if self._step_voice():
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
        self._last_box = box
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

        # Greeting: one spoken name per arrival, never a repeat while the person
        # stays in view. pump() keeps the stream paced; the WAV's own length ends
        # it. Disabled while the keyboard or the manage overlay owns the screen,
        # which frozen already covers.
        if self.greeter.update(self._name, self.voice_on and not self.frozen):
            print("[Recognize] greeting '{}'".format(self._name))
        self.greeter.pump()

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
        # Both the greeting stream and the TTS models own native handles;
        # stopping them here is what keeps an app exit from leaking one.
        try:
            self.greeter.stop()
        except BaseException as e:
            print("[Recognize]   greeter stop error: {}".format(e))
        try:
            self._close_synth()
        except BaseException as e:
            print("[Recognize]   synth close error: {}".format(e))
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
