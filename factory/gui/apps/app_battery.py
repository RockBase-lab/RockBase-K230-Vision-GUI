# Battery app: pack voltage through the on-board divider on ADC5.
#
# Sampling: 31 samples 10 ms apart, median taken, so a single full-scale spike
# cannot move the reading. One sample per step() call keeps the main loop
# responsive -- the settle time is a deadline, never a sleep.
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

import batt
from apps.error import AppUnavailable

BATTERY_UNAVAILABLE = ("Battery ADC does not respond.\n"
                       "The pack voltage cannot be measured.\n"
                       "Restart the board and try again.")

# ---- wiring ----
# Measured on this board: toggling GPIO69 changes the ADC5 reading by 3-4mV, i.e. it
# does not gate the divider (the divider reads the same with it high, low, or after a
# 3s settle). It is still driven here so the pin is never left floating; do not rely
# on it to save power.
ENABLE_PIN = 69             # nominally the divider switch, measured ineffective
ADC_CHANNEL = batt.ADC_CHANNEL   # only channel whose reading matches a divider tap

# ---- conversion ----
# One source for the whole chain: the status bar reads the same pack, so the
# constants live in batt.py rather than being duplicated here.
DIV_NUM = batt.DIV_NUM      # 1.5k + 1k divider, tap at 1k: pack = tap * 5/2
DIV_DEN = batt.DIV_DEN
VREF_FIX_NUM = batt.VREF_FIX_NUM
VREF_FIX_DEN = batt.VREF_FIX_DEN
ADC_FULL_CODE = batt.ADC_FULL_CODE
ADC_FULL_MV = batt.ADC_FULL_MV
MAX_INPUT_UV = batt.MAX_INPUT_UV

# ---- sampling ----
# Measured with the board idle: 31 samples at 10ms hold the peak-to-peak span at
# 3-40mV, so the reference implementation's 10ms cadence is safe. A 100mV limit
# leaves room for a real load step.
SETTLE_MS = 500             # settle time after the divider enable pin is driven
SAMPLE_COUNT = 31           # odd count: the median is an actual sample
SAMPLE_GAP_MS = 10
REFRESH_MS = 1000
SPAN_LIMIT_MV = 100         # peak-to-peak above this means an unstable input
ABSENT_MAX_MV = batt.ABSENT_MAX_MV
PRESENT_MIN_MV = 3000
PRESENT_MAX_MV = 4200
FAIL_LIMIT = 20             # consecutive ADC exceptions = the channel is gone

# Resting OCV table shared with the status bar (one source for both).
OCV_CURVE = batt.OCV_CURVE

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
GAUGE_PAD = 4
NUB_W = 16
NUB_H = 50

HISTORY_POINTS = 60         # one reading per second = one minute
SPARK_X = 16
SPARK_Y = 282
SPARK_W = RIGHT_W - 32
SPARK_H = 68
SPARK_MIN_MV = 3300
SPARK_MAX_MV = 4250

STATUS_MEASURING = "MEASURING"
STATUS_OK = "BATTERY PRESENT"
STATUS_ABSENT = "NO BATTERY"
STATUS_UNSTABLE = "UNSTABLE INPUT"
STATUS_RANGE = "OUT OF RANGE"
STATUS_ERROR = "ADC ERROR"

STATUS_COLOR = {
    STATUS_MEASURING: COL_MUTED,
    STATUS_OK: COL_OK,
    STATUS_ABSENT: COL_MUTED,
    STATUS_UNSTABLE: COL_WARN,
    STATUS_RANGE: COL_ERR,
    STATUS_ERROR: COL_ERR,
}


def _font(*names):
    for name in names:
        font = getattr(lv, name, None)
        if font:
            return font
    return None


def _percent(mv):
    """Map a pack voltage to a 0-100 estimate (shared OCV curve)."""
    return batt.percent(mv)


def _level_color(pct):
    if pct is None:
        return COL_MUTED
    if pct <= 10:
        return COL_ERR
    if pct <= 25:
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
    if mv < PRESENT_MIN_MV or mv > PRESENT_MAX_MV:
        return STATUS_RANGE, None
    return STATUS_OK, _percent(mv)


def _label(parent, text, x, y, width, color, font=None, align=None):
    lb = lv.label(parent)
    lb.set_text(text)
    if width:
        lb.set_width(width)
        lb.set_style_text_align(align or lv.TEXT_ALIGN.LEFT, 0)
    lb.set_style_text_color(lv.color_hex(color), 0)
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
        self._next_ms = None
        self._history = []
        self._history_dirty = False
        self._status = STATUS_MEASURING
        self._percent = None
        self._mv = None
        self._primed = False

        self._open_adc()
        self._build_ui(content)
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
            self.adc = batt.adc()
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
        _label(left, "CHARGE", 0, 26, LEFT_W, COL_MUTED,
               self._small, lv.TEXT_ALIGN.CENTER)
        self.pct_label = _label(left, "--", 0, 46, LEFT_W, COL_TEXT,
                                self._big_font, lv.TEXT_ALIGN.CENTER)
        self.volt_label = _label(left, "0.00 V", 0, 282, LEFT_W, COL_ACCENT,
                                 self._value_font, lv.TEXT_ALIGN.CENTER)
        self.status_label = _label(left, STATUS_MEASURING, 0, 330, LEFT_W,
                                   COL_MUTED, self._mid_font,
                                   lv.TEXT_ALIGN.CENTER)
        _label(left, "resting-voltage estimate", 0, 360, LEFT_W, COL_MUTED,
               self._small, lv.TEXT_ALIGN.CENTER)

        body = _plain(left, GAUGE_X, GAUGE_Y, GAUGE_W, GAUGE_H)
        body.set_style_border_width(3, 0)
        body.set_style_border_color(lv.color_hex(COL_GAUGE_EDGE), 0)
        body.set_style_radius(14, 0)
        nub = lv.obj(left)
        nub.set_pos(GAUGE_X + GAUGE_W, GAUGE_Y + (GAUGE_H - NUB_H) // 2)
        nub.set_size(NUB_W, NUB_H)
        nub.set_style_bg_color(lv.color_hex(COL_GAUGE_EDGE), 0)
        nub.set_style_bg_opa(lv.OPA.COVER, 0)
        nub.set_style_border_width(0, 0)
        nub.set_style_radius(4, 0)
        nub.clear_flag(lv.obj.FLAG.SCROLLABLE)

        self.fill = lv.obj(body)
        self.fill.set_pos(GAUGE_PAD, GAUGE_PAD)
        self.fill.set_size(0, GAUGE_H - GAUGE_PAD * 2)
        self.fill.set_style_bg_color(lv.color_hex(COL_MUTED), 0)
        self.fill.set_style_bg_opa(lv.OPA.COVER, 0)
        self.fill.set_style_border_width(0, 0)
        self.fill.set_style_radius(9, 0)
        self.fill.clear_flag(lv.obj.FLAG.SCROLLABLE)

        # ---- right: numeric rows ----
        _label(right, "MEASUREMENT", 20, 18, RIGHT_W - 40, COL_MUTED,
               self._small)
        rows = (
            ("DIVIDER TAP", "--"),
            ("ADC CODE", "--"),
            ("SAMPLE SPAN", "--"),
            ("REJECTED", "--"),
            ("SAMPLES", "--"),
        )
        self.row_values = []
        for i, (name, value) in enumerate(rows):
            y = 56 + i * 38
            _label(right, name, 20, y, 150, COL_MUTED, self._small)
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

        _label(right, "LAST {} s".format(HISTORY_POINTS), 20, 258,
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
            self.status_label.set_text(status)
            self.status_label.set_style_text_color(
                lv.color_hex(STATUS_COLOR.get(status, COL_MUTED)), 0)
        except BaseException:
            pass

    def _paint(self):
        mv = self._mv
        pct = self._percent
        status = self._status
        tap_mv = self._tap_uv // 1000 if self._tap_uv else 0

        if pct is None:
            self.pct_label.set_text("--")
            self.fill.set_size(0, GAUGE_H - GAUGE_PAD * 2)
        else:
            self.pct_label.set_text("{}%".format(pct))
            inner_w = GAUGE_W - GAUGE_PAD * 2
            width = inner_w * pct // 100
            if pct > 0 and width < 4:
                width = 4
            self.fill.set_size(width, GAUGE_H - GAUGE_PAD * 2)
            self.fill.set_style_bg_color(lv.color_hex(_level_color(pct)), 0)

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
        # One conversion per sample, taken as read_uv() only. Both methods describe
        # the same 12-bit conversion (measured raw=3417 next to 1501mV), so the code
        # is derived instead of read a second time.
        try:
            uv = self.adc.read_uv()
            self._fails = 0
        except BaseException as error:
            self._fails += 1
            print("[Battery] read failed ({}/{}): {}".format(
                self._fails, FAIL_LIMIT, error))
            if self._fails >= FAIL_LIMIT:
                self.ctx.report_unavailable(BATTERY_UNAVAILABLE)
                self._fails = 0
            return

        if uv <= 0 or uv > MAX_INPUT_UV:
            self._rejected += 1
            return

        # Derive the 12-bit code from the un-corrected voltage, then apply the Vref
        # correction so the multiply stays inside 32 bits.
        raw = uv * ADC_FULL_CODE // ADC_FULL_MV
        uv = uv * VREF_FIX_NUM // VREF_FIX_DEN
        self._tap_uv = uv
        self._raws.append(raw)
        self._mvs.append(uv * DIV_NUM // DIV_DEN // 1000)

    def _publish(self):
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
            self._set_status(STATUS_ERROR)
        else:
            middle = sampled // 2
            self._raws_median = raws[middle]
            self._mv = mvs[middle]
            self._span = mvs[-1] - mvs[0]
            status, pct = _classify(self._mv, self._span, self._rejected, sampled)
            self._percent = pct
            self._set_status(status)
            if self._mv > ABSENT_MAX_MV:
                self._history.append(self._mv)
                if len(self._history) > HISTORY_POINTS:
                    del self._history[0]
                self._history_dirty = True

        self._paint()
        if self._history_dirty:
            self._history_dirty = False
            self._repaint_history()

        print("[Battery] raw={} tap={}mV pack={}mV span={}mV reject={} pct={} {}".format(
            self._raws_median, self._tap_uv // 1000,
            self._mv if self._mv is not None else -1,
            self._span, self._rejected,
            self._percent if self._percent is not None else -1,
            self._status))

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

        self._read_one()
        if self._attempts < SAMPLE_COUNT:
            self._next_ms = time.ticks_add(now, SAMPLE_GAP_MS)
            return

        self._publish()
        # Accumulate on the batch deadline rather than on "now": one batch costs
        # 31 * 10ms = 310ms, so chaining from now would stretch the period to 1.31s
        # and the "31 / 1 s" readout would be a lie.
        self._next_ms = time.ticks_add(due, REFRESH_MS)

    def destroy(self):
        self._release()
        print("[Battery] divider disabled")


def create_app(content, ctx):
    return BatteryApp(content, ctx)
