# Settings app: user preferences plus the board facts the old System tile held.
#
# One vertically scrolling column of cards, table-driven from GROUPS below, so a
# new preference is one entry plus a DEFAULTS key in config.py.
#
# Row types: toggle (switch, whole row taps), stepper (- / value / +, clamped),
# info (read-only; "live": "heap" or "models" refreshes once per second), and
# action (runs a named handler, e.g. restore defaults).
#
# config.set() persists every change to /data/settings/gui.json, so there is no
# Save button. Live consumers (click tone, backlight) apply immediately; the rest
# are read by the owning app when it starts.

import gc
import os
import time

import lvgl as lv

import config
from ui import backlight
from ui import beep

COL_TEXT = 0xE6EAF0
COL_MUTED = 0x8A94A2
COL_ACCENT = 0x9CA3AF      # same grey as the desktop tile (APP_ACCENTS["settings"])
COL_WARN = 0xFFC857
COL_ROW = 0x1E262E         # card fill, same as the Network app rows
COL_TRACK = 0x3A4650       # switch track when off

SCROLL_W = 768
SCROLL_H = 408
ROW_W = 732
ROW_H = 56
ROW_GAP = 10
ROW_X = 8
SECTION_H = 34
CONFIRM_MS = 3000          # second tap window for a destructive action


def _uname(index, fallback):
    """Read one os.uname() field, falling back when the call is unavailable."""
    try:
        return os.uname()[index]
    except BaseException:
        return fallback


# Static board identity, read once at import. Release and machine come from the
# running firmware rather than a hard-coded string, so a firmware update shows up
# without editing this table.
FW_RELEASE = _uname(2, "unknown")
FW_MACHINE = _uname(4, "K230")

GROUPS = (
    ("SOUND", (
        {"type": "toggle", "key": "ui.click_sound",
         "label": "Button feedback",
         "hint": "click when a button, key or tile is pressed"},
        {"type": "toggle", "key": "camera.shutter_sound",
         "label": "Camera shutter",
         "hint": "synthesised shutter sound on every photo"},
    )),
    ("DISPLAY", (
        {"type": "stepper", "key": "display.brightness",
         "label": "Brightness", "hint": "panel backlight, 20-100%",
         "min": 20, "max": 100, "step": 10, "unit": "%"},
    )),
    ("NETWORK", (
        {"type": "info", "label": "Wi-Fi and timezone",
         "value": "in the Network app"},
    )),
    ("SYSTEM", (
        # This section is the old System tile: that entry was a placeholder, so the
        # board facts live here instead of behind a second, empty icon.
        {"type": "info", "label": "Firmware", "value": FW_RELEASE},
        {"type": "info", "label": "Machine", "value": FW_MACHINE},
        # Scope matters: gc only reports the MicroPython GC arena, whose initial
        # size is about 4 MB but which grows on demand from the system heap
        # (measured: a 3 MB contiguous block succeeds with 1.19 MB reported free).
        # So show free bytes and say it grows, rather than "free / total", which
        # reads as a nearly exhausted 4 MB budget on a 1 GB board.
        {"type": "info", "key": "sys.heap", "label": "Python heap",
         "hint": "GC arena, grows on demand; not DDR", "live": "heap"},
        {"type": "info", "label": "KPU models", "live": "models"},
        {"type": "info", "label": "Settings file", "value": config.PATH},
    )),
    ("RESET", (
        {"type": "action", "label": "Restore defaults",
         "hint": "tap twice to confirm", "action": "reset"},
    )),
)


def _font(*names):
    for name in names:
        font = getattr(lv, name, None)
        if font:
            return font
    return None


def _heap_text():
    """Free bytes in the MicroPython GC arena, in MB.

    Free only: the arena starts near 3.95 MB but grows on demand (a 3 MB
    contiguous block succeeds at 1.19 MB reported free), so free/total would
    imply a ceiling that does not exist.
    """
    return "{:.2f} MB free".format(gc.mem_free() / 1048576.0)


def _models_text():
    """Resident KPU models out of the verified ceiling of ten (model_pool).

    Imported lazily: model_pool pulls in nncase_runtime, and a diagnostic page
    must still open when that binding is missing. Weights are outside the GC
    heap, so this tracks preload use, not memory.
    """
    try:
        import model_pool
        return "{} resident".format(model_pool.loaded_count())
    except BaseException:
        return "n/a"


class SettingsApp:
    def __init__(self, content, ctx):
        self.ctx = ctx
        self.content = content
        self.font = _font("font_montserrat_20", "font_montserrat_16")
        self.small = _font("font_montserrat_14", "font_montserrat_16")
        self._toggles = []    # (key, switch)
        self._steppers = []   # (item, value label)
        self._live = []       # (kind, label, config key or None)
        self._actions = {}    # action name -> value label
        self._confirm = {}    # action name -> ticks_ms deadline
        self._last_live_ms = 0

        self.scroll = lv.obj(content)
        self.scroll.set_size(SCROLL_W, SCROLL_H)
        self.scroll.align(lv.ALIGN.TOP_MID, 0, 8)
        self.scroll.set_style_bg_opa(lv.OPA.TRANSP, 0)
        self.scroll.set_style_border_width(0, 0)
        self.scroll.set_style_pad_all(0, 0)
        try:
            self.scroll.set_scroll_dir(lv.DIR.VER)
        except BaseException:
            pass
        self.scroll.add_flag(lv.obj.FLAG.SCROLLABLE)

        y = 2
        for title, items in GROUPS:
            y = self._section(y, title)
            for item in items:
                y = self._build_row(item, y)
        # Spacer at the very bottom: LVGL derives the scroll range from child
        # extents, and the last card can otherwise sit flush against the edge.
        spacer = lv.obj(self.scroll)
        spacer.set_size(1, 8)
        spacer.set_pos(0, y)
        spacer.set_style_bg_opa(lv.OPA.TRANSP, 0)
        spacer.set_style_border_width(0, 0)
        spacer.clear_flag(lv.obj.FLAG.SCROLLABLE)
        spacer.clear_flag(lv.obj.FLAG.CLICKABLE)

        self._refresh_live(force=True)
        print("[settings] ready: {} toggle(s), {} stepper(s)".format(
            len(self._toggles), len(self._steppers)))

    # ---------- layout helpers ----------

    def _label(self, parent, text, x, y, color=COL_TEXT, font=None):
        lab = lv.label(parent)
        lab.set_text(text)
        lab.set_style_text_color(lv.color_hex(color), 0)
        f = font or self.font
        if f:
            lab.set_style_text_font(f, 0)
        lab.set_pos(x, y)
        return lab

    def _section(self, y, title):
        lab = lv.label(self.scroll)
        lab.set_text(title)
        lab.set_style_text_color(lv.color_hex(COL_ACCENT), 0)
        if self.small:
            lab.set_style_text_font(self.small, 0)
        lab.set_pos(ROW_X + 2, y + 8)
        return y + SECTION_H

    def _card(self, y, height=ROW_H):
        row = lv.obj(self.scroll)
        row.set_size(ROW_W, height)
        row.set_pos(ROW_X, y)
        row.set_style_bg_color(lv.color_hex(COL_ROW), 0)
        row.set_style_radius(10, 0)
        row.set_style_border_width(0, 0)
        row.set_style_pad_all(0, 0)
        row.clear_flag(lv.obj.FLAG.SCROLLABLE)
        return row

    def _press_feedback(self, obj):
        # Same bubble as the other apps: grow slightly while pressed.
        try:
            obj.set_style_transform_width(4, lv.STATE.PRESSED)
            obj.set_style_transform_height(4, lv.STATE.PRESSED)
        except BaseException:
            pass

    def _rows_label(self, row, item):
        self._label(row, item["label"], 16, 8)
        hint = item.get("hint")
        if hint and self.small:
            self._label(row, hint, 16, 31, COL_MUTED, self.small)

    # ---------- row builders ----------

    def _build_row(self, item, y):
        kind = item["type"]
        if kind == "toggle":
            return self._toggle_row(item, y)
        if kind == "stepper":
            return self._stepper_row(item, y)
        if kind == "info":
            return self._info_row(item, y)
        if kind == "action":
            return self._action_row(item, y)
        return y

    def _toggle_row(self, item, y):
        row = self._card(y)
        row.add_flag(lv.obj.FLAG.CLICKABLE)
        self._press_feedback(row)
        self._rows_label(row, item)

        sw = lv.switch(row)
        sw.set_size(52, 28)
        sw.align(lv.ALIGN.RIGHT_MID, -18, 0)
        sw.clear_flag(lv.obj.FLAG.SCROLLABLE)
        # The row owns the click: a non-clickable switch passes hit testing to
        # its parent, so the whole card is a 56px target.
        sw.clear_flag(lv.obj.FLAG.CLICKABLE)
        try:
            sw.set_style_bg_color(lv.color_hex(COL_TRACK), 0)
            sw.set_style_bg_color(lv.color_hex(COL_ACCENT), lv.STATE.CHECKED)
        except BaseException:
            pass
        if config.get_bool(item["key"]):
            sw.add_state(lv.STATE.CHECKED)

        key = item["key"]
        beep.on_click(row, lambda e, _k=key, _s=sw: self._on_toggle(_k, _s))
        self._toggles.append((key, sw))
        return y + ROW_H + ROW_GAP

    def _stepper_row(self, item, y):
        row = self._card(y)
        # Read-only row body: only the two buttons are click targets, so a tap
        # on the card cannot invent an action the user did not ask for.
        row.clear_flag(lv.obj.FLAG.CLICKABLE)
        self._rows_label(row, item)

        value = config.get_int(item["key"], item.get("max", 100))
        unit = item.get("unit", "")
        vlabel = lv.label(row)
        vlabel.set_text("{}{}".format(value, unit))
        vlabel.set_style_text_color(lv.color_hex(COL_TEXT), 0)
        if self.font:
            vlabel.set_style_text_font(self.font, 0)
        vlabel.align(lv.ALIGN.RIGHT_MID, -116, 0)

        minus = self._step_btn(row, "-", -60)
        plus = self._step_btn(row, "+", -18)
        beep.on_click(minus, lambda e, _i=item: self._on_step(_i, -1))
        beep.on_click(plus, lambda e, _i=item: self._on_step(_i, 1))
        self._steppers.append((item, vlabel))
        return y + ROW_H + ROW_GAP

    def _step_btn(self, parent, text, right_offset):
        btn = lv.btn(parent)
        btn.set_size(40, 36)
        btn.align(lv.ALIGN.RIGHT_MID, right_offset, 0)
        btn.set_style_bg_color(lv.color_hex(0x2A3340), 0)
        btn.set_style_radius(8, 0)
        btn.set_style_border_width(0, 0)
        btn.set_style_pad_all(0, 0)
        btn.clear_flag(lv.obj.FLAG.SCROLLABLE)
        lab = lv.label(btn)
        lab.set_text(text)
        lab.set_style_text_color(lv.color_hex(COL_TEXT), 0)
        if self.font:
            lab.set_style_text_font(self.font, 0)
        lab.center()
        lab.clear_flag(lv.obj.FLAG.CLICKABLE)
        return btn

    def _info_row(self, item, y):
        row = self._card(y)
        row.clear_flag(lv.obj.FLAG.CLICKABLE)
        self._rows_label(row, item)
        value = item.get("value", "")
        vlabel = lv.label(row)
        vlabel.set_text(value)
        vlabel.set_style_text_color(lv.color_hex(COL_MUTED), 0)
        if self.small:
            vlabel.set_style_text_font(self.small, 0)
        vlabel.align(lv.ALIGN.RIGHT_MID, -18, 0)
        live = item.get("live")
        if live:
            self._live.append((live, vlabel, item.get("key")))
        return y + ROW_H + ROW_GAP

    def _action_row(self, item, y):
        row = self._card(y)
        row.add_flag(lv.obj.FLAG.CLICKABLE)
        self._press_feedback(row)
        self._rows_label(row, item)
        value = lv.label(row)
        value.set_text("tap to run")
        value.set_style_text_color(lv.color_hex(COL_MUTED), 0)
        if self.small:
            value.set_style_text_font(self.small, 0)
        value.align(lv.ALIGN.RIGHT_MID, -18, 0)
        name = item.get("action", "")
        self._actions[name] = value
        beep.on_click(row, lambda e, _i=item: self._on_action(_i))
        return y + ROW_H + ROW_GAP

    # ---------- handlers ----------

    def _on_toggle(self, key, sw):
        value = not sw.has_state(lv.STATE.CHECKED)
        if value:
            sw.add_state(lv.STATE.CHECKED)
        else:
            sw.clear_state(lv.STATE.CHECKED)
        self._apply(key, value)

    def _apply(self, key, value):
        config.set(key, value)
        if key == "ui.click_sound":
            beep.set_enabled(value)
            if value:
                beep.tap()          # preview: the enabling tap is also the demo
        elif key == "display.brightness":
            if not backlight.set_percent(value):
                print("[settings] backlight write failed (GPIO fallback?)")

    def _on_step(self, item, direction):
        key = item["key"]
        lo = item.get("min", 0)
        hi = item.get("max", 100)
        step = item.get("step", 10)
        value = config.get_int(key, hi) + direction * step
        if value < lo:
            value = lo
        elif value > hi:
            value = hi
        config.set(key, value)
        if key == "display.brightness":
            backlight.set_percent(value)
        for _item, label in self._steppers:
            if _item is item:
                label.set_text("{}{}".format(value, item.get("unit", "")))
                break

    def _on_action(self, item):
        name = item.get("action", "")
        now = time.ticks_ms()
        deadline = self._confirm.get(name, 0)
        if deadline and time.ticks_diff(now, deadline) < 0:
            self._confirm.pop(name, None)
            self._run(name, item)
        else:
            self._confirm[name] = time.ticks_add(now, CONFIRM_MS)
            label = self._actions.get(name)
            if label is not None:
                label.set_text("tap again to confirm")
                label.set_style_text_color(lv.color_hex(COL_WARN), 0)

    def _run(self, name, item):
        if name == "reset":
            config.reset()
            beep.set_enabled(config.get_bool("ui.click_sound", True))
            backlight.set_percent(config.get_int("display.brightness", 100))
            self._refresh_controls()
            self._refresh_live(force=True)
            label = self._actions.get(name)
            if label is not None:
                label.set_text("defaults restored")
                label.set_style_text_color(lv.color_hex(COL_ACCENT), 0)
            print("[settings] defaults restored")

    def _refresh_controls(self):
        for key, sw in self._toggles:
            if config.get_bool(key):
                sw.add_state(lv.STATE.CHECKED)
            else:
                sw.clear_state(lv.STATE.CHECKED)
        for item, label in self._steppers:
            label.set_text("{}{}".format(
                config.get_int(item["key"], item.get("max", 100)),
                item.get("unit", "")))

    # ---------- per-frame ----------

    def _refresh_live(self, force=False):
        now = time.ticks_ms()
        if not force and time.ticks_diff(now, self._last_live_ms) < 1000:
            return
        self._last_live_ms = now
        for kind, label, _key in self._live:
            try:
                if kind == "heap":
                    label.set_text(_heap_text())
                elif kind == "models":
                    label.set_text(_models_text())
            except BaseException:
                pass
        # Clear a stale "tap again to confirm" after the window expires.
        for name in list(self._confirm.keys()):
            if time.ticks_diff(now, self._confirm[name]) >= 0:
                self._confirm.pop(name, None)
                label = self._actions.get(name)
                if label is not None:
                    label.set_text("tap to run")
                    label.set_style_text_color(lv.color_hex(COL_MUTED), 0)

    def step(self):
        self._refresh_live()

    def destroy(self):
        # Nothing hardware-owned here; every change was already persisted.
        pass


def create_app(content, ctx):
    return SettingsApp(content, ctx)
