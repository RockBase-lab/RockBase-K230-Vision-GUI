# Battery app: pack voltage through the on-board divider on ADC5.
#
# Sampling: one conversion per step() call, median taken, so a single full-scale
# spike cannot move the reading. The batch is closed on elapsed time, not on a
# sample count, because step() runs once per panel frame (measured 33 ms with the
# desktop up) and a count would stretch the window -- and the span with it. One
# sample per call keeps the main loop responsive: every wait here is a deadline,
# never a sleep.
#
# Voltage chain, measured on this board:
#   read_uv() assumes a 1.80 V reference but the rail measures 1.88 V, so raw
#   values are 4.4% low: uv * 47 // 45. The divider is 1.5k over 1k, so the tap
#   is 2/5 of the pack: mV = uv * 5 // 2 // 1000.
#   The percentage comes from a resting Li-ion OCV curve and is an estimate only:
#   the pack sags under load and reads high while charging, so voltage alone
#   cannot measure charge on a busy board.

import time

import lvgl as lv
from machine import FPIOA, Pin

from hardware import battery
from hardware.battery_trace import open_session
from core import i18n
from core.errors import AppUnavailable

BATTERY_UNAVAILABLE = ("Battery ADC does not respond.\n"
                       "The pack voltage cannot be measured.\n"
                       "Restart the board and try again.")

# ---- wiring ----
# Measured on this board: toggling GPIO69 changes the ADC5 reading by 3-4mV, i.e. it
# does not gate the divider (the divider reads the same with it high, low, or after a
# 3s settle). It is still driven here so the pin is never left floating; do not rely
# on it to save power.
ENABLE_PIN = 69             # nominally the divider switch, measured ineffective
ADC_CHANNEL = battery.ADC_CHANNEL   # only channel whose reading matches a divider tap

# ---- conversion ----
# One source for the whole chain: the status bar reads the same pack, so the
# constants live in battery.py rather than being duplicated here.
DIV_NUM = battery.DIV_NUM      # 1.5k + 1k divider, tap at 1k: pack = tap * 5/2
DIV_DEN = battery.DIV_DEN
ADC_FULL_CODE = battery.ADC_FULL_CODE
ADC_FULL_MV = battery.ADC_FULL_MV
MAX_INPUT_UV = battery.MAX_INPUT_UV

# ---- sampling ----
# A batch is a window of elapsed time, not a count: step() is called once per panel
# refresh (measured ~33 ms with the desktop up), so a count would let the loop's own
# period decide the window -- and span grows with the window. Closing on time keeps
# the 100 mV threshold meaning the same thing at any loop rate. Measured on an idle
# board over such a window: peak-to-peak span 3-40 mV.
SETTLE_MS = 500             # settle time after the divider enable pin is driven
SAMPLE_WINDOW_MS = 320      # batch length; span is only comparable over a fixed window
SAMPLE_MIN_COUNT = 5        # a stalled loop must not close a two-sample batch
SAMPLE_COUNT = 31           # hard cap on conversions per batch
SAMPLE_GAP_MS = 10          # enforced only when the loop runs faster than this
REFRESH_MS = 1000
SPAN_LIMIT_MV = 100         # peak-to-peak above this means an unstable input
ABSENT_MAX_MV = battery.ABSENT_MAX_MV
PRESENT_MIN_MV = 3000
# A resting full pack sits at 4.2V, but while charging the charger holds the rail
# above it: measured 4363mV on this board with the cable connected. Anything in the
# charging band is a normal state, not a fault, so it gets its own verdict instead of
# tripping the range check.
CHARGE_MIN_MV = 4250
# Sanity bound only: above this the divider ratio or the Vref constant is wrong.
PRESENT_MAX_MV = 4600
FAIL_LIMIT = 20             # consecutive ADC exceptions = the channel is gone
# Every Nth traced batch skips its repaint. A smaller span there means this app's own
# redraw is the load being measured; an unchanged span means the load is elsewhere.
TRACE_QUIET_EVERY = 8

# ---- UI ----
COL_TEXT = 0xE6EAF0
COL_MUTED = 0x8A94A2
COL_CARD = 0x101418
COL_LINE = 0x3A4650
COL_ACCENT = 0xC9AE7A       # same beige as the desktop icon
COL_OK = 0x4CD97B
COL_WARN = 0xFFC857
COL_ERR = 0xFF6B6B
COL_GAUGE_EDGE = 0x55606E
COL_SEG_OFF = 0x2A3138      # unlit segment: a step darker than the card fill

CARD_Y = 20
CARD_H = 384
LEFT_X = 20
LEFT_W = 392
RIGHT_X = 428
RIGHT_W = 352

GAUGE_W = 280
GAUGE_H = 120
GAUGE_X = (LEFT_W - GAUGE_W) // 2
GAUGE_Y = 140
# The fill is a child of the body, and LVGL insets a child's coordinate space by the
# border width plus the padding. Both are therefore part of the inset: ignoring the
# border left a 7px gap on top against 1px at the bottom (measured in a snapshot).
GAUGE_BORDER = 3
GAUGE_PAD = 4
GAUGE_INSET = GAUGE_BORDER + GAUGE_PAD
GAUGE_INNER_W = GAUGE_W - GAUGE_INSET * 2
GAUGE_INNER_H = GAUGE_H - GAUGE_INSET * 2
NUB_W = 16
NUB_H = 50

SEG_COUNT = 10              # gauge resolution: one segment per 10% of charge
SEG_GAP = 6
SEG_W = (GAUGE_INNER_W - (SEG_COUNT - 1) * SEG_GAP) // SEG_COUNT
SEG_RUN_W = SEG_COUNT * SEG_W + (SEG_COUNT - 1) * SEG_GAP
SEG_X0 = (GAUGE_INNER_W - SEG_RUN_W) // 2   # centre the row inside the body

HISTORY_POINTS = 60         # one reading per second = one minute
SPARK_X = 16
SPARK_Y = 282
SPARK_W = RIGHT_W - 32
SPARK_H = 68
SPARK_MIN_MV = 3000     # just under the D1 anchor, so a low pack is not clipped
SPARK_MAX_MV = 4400     # covers the charging plateau, not just a resting pack

STATUS_MEASURING = "MEASURING"
STATUS_OK = "BATTERY PRESENT"
STATUS_CHARGING = "CHARGING"
STATUS_ABSENT = "NO BATTERY"
STATUS_UNSTABLE = "UNSTABLE INPUT"
STATUS_RANGE = "OUT OF RANGE"
STATUS_ERROR = "ADC ERROR"

STATUS_COLOR = {
    STATUS_MEASURING: COL_MUTED,
    STATUS_OK: COL_OK,
    STATUS_CHARGING: COL_ACCENT,
    STATUS_ABSENT: COL_MUTED,
    STATUS_UNSTABLE: COL_WARN,
    STATUS_RANGE: COL_ERR,
    STATUS_ERROR: COL_ERR,
}

# The constants above are the internal state and the STATUS_COLOR keys; the
# display text goes through these i18n keys.
STATUS_I18N = {
    STATUS_MEASURING: "battery.measuring",
    STATUS_OK: "battery.present",
    STATUS_CHARGING: "battery.charging",
    STATUS_ABSENT: "battery.absent",
    STATUS_UNSTABLE: "battery.unstable",
    STATUS_RANGE: "battery.range",
    STATUS_ERROR: "battery.adc_error",
}


def _font(*names):
    for name in names:
        font = getattr(lv, name, None)
        if font:
            return font
    return None


def _percent(mv):
    """Map a pack voltage to a 0-100 estimate (shared OCV curve)."""
    return battery.percent(mv)


def _seg_color(pct):
    """Colour for the lit segments, from the current charge.

    Level-based, not position-based: a full pack shows ten green segments and a low
    one shows a couple of red, which is how the status icon behaves. A fixed colour
    per position leaves a glowing red block at the left of a full gauge.
    """
    if pct <= 25:
        return COL_ERR
    if pct <= 50:
        return COL_WARN
    return COL_OK


def _classify(mv, span, rejected, sampled):
    """Turn a batch result into (status text, percentage or None)."""
    if sampled == 0:
        return STATUS_ERROR, None
    if span > SPAN_LIMIT_MV or rejected:
        return STATUS_UNSTABLE, None
    if mv <= ABSENT_MAX_MV:
        return STATUS_ABSENT, None
    # Charging comes before the range check: the charger legitimately lifts the rail
    # above a resting full pack, so rejecting that as "out of range" would flag a
    # healthy board on every charge.
    if mv >= CHARGE_MIN_MV:
        if mv > PRESENT_MAX_MV:
            return STATUS_RANGE, None
        return STATUS_CHARGING, 100
    if mv < PRESENT_MIN_MV:
        return STATUS_RANGE, None
    return STATUS_OK, _percent(mv)


def _label(parent, text, x, y, width, color, font=None, align=None):
    lb = lv.label(parent)
    lb.set_text(text)
    if width:
        lb.set_width(width)
        lb.set_style_text_align(align or lv.TEXT_ALIGN.LEFT, 0)
    lb.set_style_text_color(lv.color_hex(color), 0)
    font = i18n.font_for(text, font)
    if font is not None:
        try:
            lb.set_style_text_font(font, 0)
        except BaseException:
            pass
    lb.set_pos(x, y)
    return lb


def _plain(parent, x, y, w, h):
    """Transparent, borderless, non-scrollable container."""
    obj = lv.obj(parent)
    obj.set_pos(x, y)
    obj.set_size(w, h)
    obj.set_style_bg_opa(lv.OPA.TRANSP, 0)
    obj.set_style_border_width(0, 0)
    obj.set_style_radius(0, 0)
    obj.set_style_pad_all(0, 0)
    obj.clear_flag(lv.obj.FLAG.SCROLLABLE)
    return obj


def _card(parent, x, y, w, h):
    obj = lv.obj(parent)
    obj.set_pos(x, y)
    obj.set_size(w, h)
    obj.set_style_bg_color(lv.color_hex(COL_CARD), 0)
    obj.set_style_bg_opa(lv.OPA.COVER, 0)
    obj.set_style_border_width(1, 0)
    obj.set_style_border_color(lv.color_hex(COL_LINE), 0)
    obj.set_style_radius(12, 0)
    obj.set_style_pad_all(0, 0)
    obj.clear_flag(lv.obj.FLAG.SCROLLABLE)
    return obj


class BatteryApp(object):
    def __init__(self, content, ctx):
        self.ctx = ctx
        self.content = content
        self.adc = None
        self.enable = None
        self._fails = 0
        self._attempts = 0
        self._rejected = 0
        self._raws = []
        self._mvs = []
        self._tap_uv = 0
        self._sampled = 0
        self._span = 0
        self._raws_median = 0
        self._batch_ms = None   # first conversion of the current batch
        self._next_ms = None
        self._history = []
        self._history_dirty = False
        self._status = STATUS_MEASURING
        self._percent = None
        self._mv = None
        self._primed = False

        # ---- trace ----
        self._trace = None      # opened after the UI exists, see apps/batt_trace.py
        self._seq = 0
        self._uvs = []          # every conversion this batch; -1 = the read raised
        self._rel = []          # us from the batch start
        self._gap = []          # us from the previous conversion
        self._sp = []           # us from the last repaint request, -1 = none yet
        self._batch_us = 0
        self._prev_us = 0
        self._paint_us = 0
        self._paint_cost_us = 0

        self._open_adc()
        self._build_ui(content)
        self._trace = open_session()
        self._next_ms = time.ticks_add(time.ticks_ms(), SETTLE_MS)
        print("[Battery] ADC{} enabled, settling {}ms".format(
            ADC_CHANNEL, SETTLE_MS))

    # ---------- hardware ----------
    def _open_adc(self):
        try:
            fpioa = FPIOA()
            fpioa.set_function(ENABLE_PIN, FPIOA.GPIO0 + ENABLE_PIN)
            self.enable = Pin(ENABLE_PIN, Pin.OUT, pull=Pin.PULL_NONE, drive=7)
            self.enable.value(1)
            self.adc = battery.adc()
        except BaseException as error:
            print("[Battery] open failed: {}".format(error))
            self._release()
            raise AppUnavailable(BATTERY_UNAVAILABLE)

    def _release(self):
        # The ADC handle belongs to the shared module, so it is not deinit'd here: the
        # status bar still reads the same channel. Only the enable pin is dropped.
        pin = self.enable
        self.enable = None
        if pin is not None:
            try:
                pin.value(0)
            except BaseException:
                pass
        self.adc = None

    # ---------- UI ----------
    def _build_ui(self, content):
        self._big_font = _font("font_montserrat_48", "font_montserrat_32",
                               "font_montserrat_24")
        self._value_font = _font("font_montserrat_24", "font_montserrat_20",
                                 "font_montserrat_16")
        self._mid_font = _font("font_montserrat_20", "font_montserrat_16")
        self._small = _font("font_montserrat_14", "font_montserrat_16")

        left = _card(content, LEFT_X, CARD_Y, LEFT_W, CARD_H)
        right = _card(content, RIGHT_X, CARD_Y, RIGHT_W, CARD_H)

        # ---- left: gauge ----
        _label(left, i18n.t("battery.charge"), 0, 26, LEFT_W, COL_MUTED,
               self._small, lv.TEXT_ALIGN.CENTER)
        self.pct_label = _label(left, "--", 0, 46, LEFT_W, COL_TEXT,
                                self._big_font, lv.TEXT_ALIGN.CENTER)
        self.volt_label = _label(left, "0.00 V", 0, 282, LEFT_W, COL_ACCENT,
                                 self._value_font, lv.TEXT_ALIGN.CENTER)
        self.status_label = _label(left, i18n.t(STATUS_I18N[STATUS_MEASURING]),
                                   0, 330, LEFT_W, COL_MUTED, self._mid_font,
                                   lv.TEXT_ALIGN.CENTER)
        _label(left, i18n.t("battery.charge_note"), 0, 360, LEFT_W,
               COL_MUTED, self._small, lv.TEXT_ALIGN.CENTER)

        body = _plain(left, GAUGE_X, GAUGE_Y, GAUGE_W, GAUGE_H)
        body.set_style_border_width(GAUGE_BORDER, 0)
        body.set_style_border_color(lv.color_hex(COL_GAUGE_EDGE), 0)
        body.set_style_radius(14, 0)
        body.set_style_pad_all(GAUGE_PAD, 0)
        nub = lv.obj(left)
        nub.set_pos(GAUGE_X + GAUGE_W, GAUGE_Y + (GAUGE_H - NUB_H) // 2)
        nub.set_size(NUB_W, NUB_H)
        nub.set_style_bg_color(lv.color_hex(COL_GAUGE_EDGE), 0)
        nub.set_style_bg_opa(lv.OPA.COVER, 0)
        nub.set_style_border_width(0, 0)
        nub.set_style_radius(4, 0)
        nub.clear_flag(lv.obj.FLAG.SCROLLABLE)

        # Ten segments rather than one continuous bar: the charge anchors are a
        # discrete scale (one step per LED threshold), so segments match what the
        # reading actually resolves instead of implying finer accuracy.
        self.segs = []
        for i in range(SEG_COUNT):
            seg = lv.obj(body)
            seg.set_pos(SEG_X0 + i * (SEG_W + SEG_GAP), 0)
            seg.set_size(SEG_W, GAUGE_INNER_H)
            seg.set_style_bg_color(lv.color_hex(COL_SEG_OFF), 0)
            seg.set_style_bg_opa(lv.OPA.COVER, 0)
            seg.set_style_border_width(0, 0)
            seg.set_style_radius(3, 0)
            seg.clear_flag(lv.obj.FLAG.SCROLLABLE)
            self.segs.append(seg)

        # ---- right: numeric rows ----
        _label(right, i18n.t("battery.measurement"), 20, 18, RIGHT_W - 40,
               COL_MUTED, self._small)
        rows = (
            ("battery.row.tap", "--"),
            ("battery.row.code", "--"),
            ("battery.row.span", "--"),
            ("battery.row.rejected", "--"),
            ("battery.row.samples", "--"),
        )
        self.row_values = []
        for i, (name_key, value) in enumerate(rows):
            y = 56 + i * 38
            _label(right, i18n.t(name_key), 20, y, 150, COL_MUTED, self._small)
            self.row_values.append(
                _label(right, value, 170, y - 2, RIGHT_W - 190, COL_TEXT,
                       self._mid_font, lv.TEXT_ALIGN.RIGHT))

        sep = lv.obj(right)
        sep.set_pos(20, 246)
        sep.set_size(RIGHT_W - 40, 1)
        sep.set_style_bg_color(lv.color_hex(COL_LINE), 0)
        sep.set_style_bg_opa(lv.OPA.COVER, 0)
        sep.set_style_border_width(0, 0)
        sep.set_style_radius(0, 0)

        _label(right, i18n.t("battery.last_secs").format(HISTORY_POINTS), 20, 258,
               RIGHT_W - 40, COL_MUTED, self._small)
        _label(right, "{:.1f} V".format(SPARK_MIN_MV / 1000.0), SPARK_X,
               SPARK_Y + SPARK_H + 4, 60, COL_MUTED, self._small)
        _label(right, "{:.1f} V".format(SPARK_MAX_MV / 1000.0),
               SPARK_X + SPARK_W - 60, SPARK_Y + SPARK_H + 4, 60, COL_MUTED,
               self._small, lv.TEXT_ALIGN.RIGHT)

        # Sparkline: one lv.line redrawn once per second. Preallocated point dicts
        # are mutated in place so a redraw allocates no new objects.
        self._spark = lv.line(right)
        self._spark.set_pos(0, 0)
        self._spark.set_style_line_width(2, 0)
        self._spark.set_style_line_color(lv.color_hex(COL_ACCENT), 0)
        self._spark.set_style_line_opa(lv.OPA.COVER, 0)
        try:
            self._spark.set_style_line_rounded(True, 0)
        except BaseException:
            pass
        self._spark_pts = []
        for i in range(HISTORY_POINTS):
            self._spark_pts.append({"x": 0, "y": SPARK_Y + SPARK_H})
        self._spark.set_points(self._spark_pts, HISTORY_POINTS)
        self._repaint_history()

    def _set_value(self, index, text):
        label = self.row_values[index]
        label.set_text(text)

    def _set_status(self, status):
        self._status = status
        try:
            text = i18n.t(STATUS_I18N.get(status, ""), status)
            self.status_label.set_text(text)
            f = i18n.font_for(text, self._mid_font)
            if f is not None:
                self.status_label.set_style_text_font(f, 0)
            self.status_label.set_style_text_color(
                lv.color_hex(STATUS_COLOR.get(status, COL_MUTED)), 0)
        except BaseException:
            pass

    def _paint(self):
        mv = self._mv
        pct = self._percent
        status = self._status
        tap_mv = self._tap_uv // 1000 if self._tap_uv else 0

        # Segments light left to right. The last segment only lights on a full gauge,
        # and any non-zero charge lights at least one, so an empty row really means
        # empty instead of "below one step".
        if pct is None:
            self.pct_label.set_text("--")
            lit = 0
            seg_color = COL_SEG_OFF
        else:
            self.pct_label.set_text("{}%".format(pct))
            # Round to the nearest segment, but any real charge lights at least one.
            lit = (pct * SEG_COUNT + 50) // 100
            if pct > 0 and lit == 0:
                lit = 1
            seg_color = _seg_color(pct)
        for i, seg in enumerate(self.segs):
            seg.set_style_bg_color(
                lv.color_hex(seg_color if i < lit else COL_SEG_OFF), 0)

        if mv is None:
            self.volt_label.set_text("--")
        else:
            self.volt_label.set_text("{:.2f} V".format(mv / 1000.0))

        self._set_value(0, "{} mV".format(tap_mv))
        self._set_value(1, str(self._raws_median or 0))
        self._set_value(2, "{} mV".format(self._span))
        self._set_value(3, str(self._rejected))
        self._set_value(4, "{} / 1 s".format(self._sampled))
        self._set_status(status)

    def _repaint_history(self):
        hist = self._history
        count = len(hist)
        # Nothing measured yet: hide the line instead of drawing a full-width flat
        # segment at the floor, which reads as "one minute of data" on a fresh app.
        if count == 0:
            try:
                self._spark.add_flag(lv.obj.FLAG.HIDDEN)
            except BaseException:
                pass
            return
        try:
            self._spark.clear_flag(lv.obj.FLAG.HIDDEN)
        except BaseException:
            pass

        # A single sample cannot be drawn as a line, so it becomes a one-step flat
        # segment. Points grow from the right edge leftwards, oldest on the left.
        n = count if count > 1 else 2
        width = SPARK_MAX_MV - SPARK_MIN_MV
        step = SPARK_W // (HISTORY_POINTS - 1) if HISTORY_POINTS > 1 else 0
        x0 = SPARK_X + SPARK_W - (n - 1) * step
        for k in range(n):
            mv = hist[count - n + k] if count > 1 else hist[0]
            if mv < SPARK_MIN_MV:
                mv = SPARK_MIN_MV
            elif mv > SPARK_MAX_MV:
                mv = SPARK_MAX_MV
            point = self._spark_pts[k]
            point["x"] = x0 + k * step
            point["y"] = SPARK_Y + SPARK_H - (mv - SPARK_MIN_MV) * SPARK_H // width
        try:
            self._spark.set_points(self._spark_pts, n)
        except BaseException as error:
            print("[Battery] spark draw failed: {}".format(error))

    # ---------- sampling ----------
    def _read_one(self):
        self._attempts += 1
        traced = self._trace is not None
        if traced:
            now_us = time.ticks_us()
            if self._batch_us == 0:
                self._batch_us = now_us
            gap = time.ticks_diff(now_us, self._prev_us) if self._prev_us else 0
            self._prev_us = now_us
            self._rel.append(time.ticks_diff(now_us, self._batch_us))
            self._gap.append(gap)
            self._sp.append(time.ticks_diff(now_us, self._paint_us)
                            if self._paint_us else -1)
        # One conversion per sample, taken as read_uv() only. Both methods describe
        # the same 12-bit conversion (measured raw=3417 next to 1501mV), so the code
        # is derived instead of read a second time.
        try:
            uv = self.adc.read_uv()
            self._fails = 0
        except BaseException as error:
            self._fails += 1
            if traced:
                self._uvs.append(-1)
            print("[Battery] read failed ({}/{}): {}".format(
                self._fails, FAIL_LIMIT, error))
            if self._fails >= FAIL_LIMIT:
                self.ctx.report_unavailable(BATTERY_UNAVAILABLE)
                self._fails = 0
            return

        if uv <= 0 or uv > MAX_INPUT_UV:
            self._rejected += 1
            if traced:
                self._uvs.append(uv)
            return

        # Derive the 12-bit code from the same conversion instead of reading the
        # channel twice. No Vref scaling: read_uv() already reports against the 1.8V
        # full scale the code assumes (see the note in battery.py).
        raw = uv * ADC_FULL_CODE // ADC_FULL_MV
        self._tap_uv = uv
        self._raws.append(raw)
        self._mvs.append(battery.pack_mv(uv))
        if traced:
            self._uvs.append(uv)

    def _publish(self, quiet=False):
        raws = sorted(self._raws)
        mvs = sorted(self._mvs)
        sampled = len(mvs)
        self._sampled = sampled
        if sampled == 0:
            self._mv = None
            self._percent = None
            self._raws_median = 0
            self._span = 0
            self._tap_uv = 0
            if quiet:
                self._status = STATUS_ERROR
            else:
                self._set_status(STATUS_ERROR)
        else:
            middle = sampled // 2
            self._raws_median = raws[middle]
            self._mv = mvs[middle]
            self._span = mvs[-1] - mvs[0]
            status, pct = _classify(self._mv, self._span, self._rejected, sampled)
            self._percent = pct
            if quiet:
                self._status = status
            else:
                self._set_status(status)
            if self._mv > ABSENT_MAX_MV:
                self._history.append(self._mv)
                if len(self._history) > HISTORY_POINTS:
                    del self._history[0]
                self._history_dirty = True

        t_paint = time.ticks_us()
        if quiet:
            self._paint_cost_us = -1
        else:
            self._paint()
            if self._history_dirty:
                self._history_dirty = False
                self._repaint_history()
            self._paint_us = time.ticks_us()
            self._paint_cost_us = time.ticks_diff(self._paint_us, t_paint)

        # A detached USB console must not stop the batch loop: with the cable out the
        # trace file is the only record left of what happened.
        try:
            print("[Battery] raw={} tap={}mV pack={}mV span={}mV reject={} pct={} {}".format(
                self._raws_median, self._tap_uv // 1000,
                self._mv if self._mv is not None else -1,
                self._span, self._rejected,
                self._percent if self._percent is not None else -1,
                self._status))
        except BaseException:
            pass

        if self._trace is not None:
            try:
                self._trace.batch(self._rel, self._gap, self._sp, self._uvs,
                                  self._raws_median, self._tap_uv,
                                  self._status, self._percent,
                                  self._paint_cost_us, quiet)
                self._seq += 1
            except BaseException as error:
                print("[Battery] trace failed: {}".format(error))
                self._trace = None
        self._uvs = []
        self._rel = []
        self._gap = []
        self._sp = []
        self._batch_us = 0
        self._prev_us = 0

        self._raws = []
        self._mvs = []
        self._attempts = 0
        self._rejected = 0

    def step(self):
        now = time.ticks_ms()
        due = self._next_ms
        if due is None or time.ticks_diff(now, due) < 0:
            return

        if not self._primed:
            # The first conversion after ADC() is created returns 0 on this board
            # (measured on channels 0-3). Discard it, otherwise a real zero would
            # be counted as a rejected sample and mark the batch unstable.
            self._primed = True
            try:
                self.adc.read_uv()
            except BaseException:
                pass
            self._next_ms = now
            return

        if self._batch_ms is None:
            self._batch_ms = now
        self._read_one()
        # Close on elapsed time, never on a sample count alone: the loop's own period
        # is what would otherwise decide the window. The count stays as a cap so a loop
        # that suddenly runs fast cannot make one batch outnumber the others.
        elapsed = time.ticks_diff(now, self._batch_ms)
        if (self._attempts < SAMPLE_COUNT
                and not (elapsed >= SAMPLE_WINDOW_MS
                         and len(self._mvs) >= SAMPLE_MIN_COUNT)):
            self._next_ms = time.ticks_add(now, SAMPLE_GAP_MS)
            return

        quiet = bool(self._trace is not None and self._seq
                     and self._seq % TRACE_QUIET_EVERY == 0)
        self._publish(quiet)
        self._batch_ms = None
        # Accumulate on the batch deadline rather than on "now": a batch costs
        # SAMPLE_WINDOW_MS, so chaining from now would stretch the period and make the
        # "n / 1 s" readout a lie.
        self._next_ms = time.ticks_add(due, REFRESH_MS)

    def destroy(self):
        self._release()
        if self._trace is not None:
            try:
                self._trace.close()
            except BaseException:
                pass
            self._trace = None
        print("[Battery] divider disabled")


def create_app(content, ctx):
    return BatteryApp(content, ctx)
