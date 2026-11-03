# Boot progress screen: one screen with a title, a bar, a percentage and two
# status lines, advanced from the boot sequence in main().
#
# Why a module of its own: the boot sequence is measurement-driven (each step's
# weight is its measured cost), so the progress model has to be reusable without
# pulling in the launcher, which runs via exec() and would execute twice.
#
# Redraw discipline: every set() ends with lv.task_handler(). Without it the bar
# does not move until the next unrelated repaint, and a multi-second step looks
# like a freeze.

import lvgl as lv

import time

COL_BG = 0x12141A
COL_TEXT = 0xFFFFFF
COL_MUTED = 0x6B7484
COL_TRACK = 0x252B36
COL_FILL = 0x00E5A0
COL_WARN = 0xFFC857

# Progress motion. The bar is interpolated between steps by hand (see _animate_to)
# because the boot sequence blocks inside C calls where nothing can repaint.
# Budget scales with the distance covered, so the OCR font step (44% of the bar in
# one go) sweeps and a 4% model step does not spend 200 ms of boot for nothing.
ANIM_MS_PER_PCT = 6
ANIM_MIN_MS = 45
ANIM_MAX_MS = 240
ANIM_MAX_FRAMES = 14

TITLE_SIZE = 28
BAR_W = 520
BAR_H = 12
BAR_RADIUS = 6

# Vertical layout, measured from the screen centre so the block stays centred.
TITLE_DY = -118
BAR_DY = -22
PCT_DY = 22
MAIN_DY = 62
SUB_DY = 92


def _font(*names):
    for name in names:
        font = getattr(lv, name, None)
        if font:
            return font
    return None


class Splash:
    """Boot progress screen. Build it after lv.init(); delete it once home shows."""

    def __init__(self):
        self.scr = lv.obj()
        self.scr.set_style_bg_color(lv.color_hex(COL_BG), 0)
        self.scr.set_style_bg_opa(lv.OPA.COVER, 0)
        self.scr.set_style_border_width(0, 0)
        self.scr.set_style_pad_all(0, 0)
        self.scr.clear_flag(lv.obj.FLAG.SCROLLABLE)

        big = _font("font_montserrat_28", "font_montserrat_24", "font_montserrat_20")
        mid = _font("font_montserrat_20", "font_montserrat_16", "font_montserrat_14")
        small = _font("font_montserrat_16", "font_montserrat_14")

        self.title = lv.label(self.scr)
        self.title.set_text("RockBase")
        self.title.set_style_text_color(lv.color_hex(COL_TEXT), 0)
        if big is not None:
            self.title.set_style_text_font(big, 0)
        self.title.align(lv.ALIGN.CENTER, 0, TITLE_DY)

        self.bar = lv.bar(self.scr)
        self.bar.set_size(BAR_W, BAR_H)
        self.bar.align(lv.ALIGN.CENTER, 0, BAR_DY)
        # 0..1000 so a single-percent step is still representable and the fill
        # moves often enough to read as progress rather than a jump.
        self.bar.set_range(0, 1000)
        self.bar.set_value(0, lv.ANIM.OFF)
        self.bar.set_style_radius(BAR_RADIUS, 0)
        self.bar.set_style_bg_color(lv.color_hex(COL_TRACK), 0)
        self.bar.set_style_bg_opa(lv.OPA.COVER, 0)
        self.bar.set_style_border_width(0, 0)
        self.bar.set_style_pad_all(0, 0)
        try:
            self.bar.set_style_bg_color(lv.color_hex(COL_FILL), lv.PART.INDICATOR)
            self.bar.set_style_radius(BAR_RADIUS, lv.PART.INDICATOR)
        except BaseException:
            pass

        self.pct = lv.label(self.scr)
        self.pct.set_text("0%")
        self.pct.set_style_text_color(lv.color_hex(COL_FILL), 0)
        if mid is not None:
            self.pct.set_style_text_font(mid, 0)
        self.pct.align(lv.ALIGN.CENTER, 0, PCT_DY)

        self.main = lv.label(self.scr)
        self.main.set_text("")
        self.main.set_style_text_color(lv.color_hex(COL_TEXT), 0)
        if mid is not None:
            self.main.set_style_text_font(mid, 0)
        self.main.align(lv.ALIGN.CENTER, 0, MAIN_DY)

        self.sub = lv.label(self.scr)
        self.sub.set_text("")
        self.sub.set_style_text_color(lv.color_hex(COL_MUTED), 0)
        if small is not None:
            self.sub.set_style_text_font(small, 0)
        self.sub.align(lv.ALIGN.CENTER, 0, SUB_DY)

        # Second status slot: the network is indeterminate (0 to 12 s), so it gets
        # its own line instead of a share of the bar. Doing otherwise makes the bar
        # sit still for a whole second while nothing is wrong.
        self.note_label = lv.label(self.scr)
        self.note_label.set_text("")
        self.note_label.set_style_text_color(lv.color_hex(COL_MUTED), 0)
        if small is not None:
            self.note_label.set_style_text_font(small, 0)
        self.note_label.align(lv.ALIGN.CENTER, 0, SUB_DY + 26)

        self._pct = 0
        self._start = time.ticks_ms()
        lv.scr_load(self.scr)
        self._flush()

    def _flush(self):
        try:
            lv.task_handler()
        except BaseException:
            pass

    def _set_pct(self, value):
        value = int(value)
        if value < 0:
            value = 0
        elif value > 100:
            value = 100
        if value == self._pct:
            return
        self._pct = value
        self.bar.set_value(value * 10, lv.ANIM.OFF)
        self.pct.set_text("{}%".format(value))

    def _animate_to(self, target):
        """Slide the bar to target instead of assigning it, so steps do not jump.

        The motion is driven here rather than through lv_anim because the boot
        sequence spends most of its time inside blocking C calls (load_kmodel,
        font_load), and nothing repaints while those run: an LVGL animation would
        sit frozen and then snap to its end value on the next timer tick. Spending
        the frames between steps is the only place the motion can actually show.

        Every millisecond here is boot time, so the distance decides the budget: a
        4% step gets a blink, and a 44% jump (the OCR font) gets the full sweep.
        """
        start = self._pct
        if target <= start:
            self._set_pct(target)
            self._flush()
            return
        delta = target - start
        frames = delta if delta < ANIM_MAX_FRAMES else ANIM_MAX_FRAMES
        if frames < 2:
            self._set_pct(target)
            self._flush()
            return
        budget = ANIM_MS_PER_PCT * delta
        if budget < ANIM_MIN_MS:
            budget = ANIM_MIN_MS
        elif budget > ANIM_MAX_MS:
            budget = ANIM_MAX_MS
        frame_ms = budget // frames
        if frame_ms < 1:
            frame_ms = 1
        for i in range(1, frames + 1):
            self._set_pct(start + delta * i // frames)
            self._flush()
            time.sleep_ms(frame_ms)

    def set(self, done_ms, total_ms, main, sub):
        """Advance to a fraction of the measured budget (both in milliseconds).

        The text goes first: it names what is loading right now, so it should not
        wait for the bar to catch up.
        """
        if main:
            self.main.set_text(main)
        if sub is not None:
            self.sub.set_text(sub)
        if total_ms > 0:
            self._animate_to(done_ms * 100 // total_ms)
        self._flush()

    def note(self, text):
        """Update only the second status slot (the indeterminate network line)."""
        self.note_label.set_text(text)
        self._flush()

    def warn(self, text):
        """Write the second slot in the warning colour (boot proceeded anyway)."""
        self.note_label.set_text(text)
        self.note_label.set_style_text_color(lv.color_hex(COL_WARN), 0)
        self._flush()

    def finish(self, main, sub=""):
        """Fill the bar before the screen is deleted, so it never ends at 97%."""
        self._animate_to(100)
        self.main.set_text(main)
        self.sub.set_text(sub)
        self._flush()

    def elapsed_ms(self):
        return time.ticks_diff(time.ticks_ms(), self._start)

    def delete(self):
        try:
            self.scr.delete()
        except BaseException:
            pass
