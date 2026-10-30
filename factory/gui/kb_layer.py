# Shared keyboard overlay: a bottom drawer with "text field + LVGL keyboard" that
# any app can reuse.
#
# Why this layer exists: everything the password keyboard drags in (28px bin bitmap
# font, mode guard, repeat suppression, the "abc" badge, beeper feedback) is
# firmware/touch-layer minefield unrelated to any business logic -- whoever uses a
# keyboard steps on it. It used to be buried inside the network app, unusable
# elsewhere. Here it is one object:
#
#     import kb_layer
#     kb = kb_layer.KbLayer(title="join: " + ssid, on_ok=self._connect,
#                           accent=COL_ACCENT)
#     # app.step() every frame:
#     kb.poll()
#     # closing (from on_ok or by tapping the backdrop):
#     kb.close()
#     kb.get_text()        # read the input
#
# Discipline: KbLayer depends on poll() every frame (mode guard + repeat edge). An
# app that forgets it loses the 1#/number-page switch and long-press repeat
# suppression. Construction opens the overlay.
#
# ---------------------------------------------------------------------------
# Measured constraints on board (these shaped the implementation; all were hit):
# - The keyboard font is a bin bitmap font baked offline, loaded into memory once by
#   font_load, and repaint is a plain blit (28px full screen ~7ms/frame). Runtime
#   TTF (tiny_ttf/freetype) has no glyph cache enabled in this firmware and re-
#   rasterises every frame under FULL render (measured 485~1298ms/frame at 36px),
#   so it is unusable; the largest built-in compile-time bitmap font is only 16px.
#   Load failure falls back to montserrat_16.
# - The default map's mode-switch key is hard-coded in C and the binding does not
#   expose set_map, so the key's behaviour cannot change; it can only be caught
#   where the mode lands (see _mode_guard).
# - Keyboard keys lack NO_REPEAT and keep feeding text while held; the binding does
#   not expose a long-press repeat delay, so it can only be rolled back on text
#   change (see _on_changed / _press_edge).
# - The ⌨/✖ control key only emits CANCEL in C (without changing mode) and the number
#   page has no key back to the text page, so CANCEL is taken over and turned into
#   "back to lowercase".
# - The num map's back key icon is the hard-coded ⌨ (the icon cannot be replaced), so
#   a non-clickable "abc" badge covers it: taps pass through to the key beneath,
#   behaviour unchanged, only the meaning becomes clear. Measured position: the num
#   map's top row has width shares 1+1+1+2 and the ⌨ column centre sits at keyboard-
#   local (627, 38) (measured from snapshot pixels).

import time

import lvgl as lv

import beep

# Keyboard bin bitmap font (baked offline, deployed to assets/fonts).
_KB_FONT_BIN = "kb_nunito_bold_28.bin"
_KB_FONT_CACHE = None   # font_load allocates memory; a module-level cache avoids reloading per overlay


def load_kb_font():
    """Keyboard font: load the 28px bin bitmap font at runtime, falling back to the
    built-in montserrat_16.

    Module-level cache: font_load allocates, so it loads once per process. Returns a
    font object or None.
    """
    global _KB_FONT_CACHE
    if _KB_FONT_CACHE is False:
        return _fallback_font()
    if _KB_FONT_CACHE is not None:
        return _KB_FONT_CACHE
    try:
        d = __file__.rsplit("/", 1)[0]
    except NameError:
        d = "/sdcard/factory/gui"
    path = "A:" + d + "/assets/fonts/" + _KB_FONT_BIN
    try:
        font = lv.font_load(path)
    except Exception:
        font = None
    if font is None:
        _KB_FONT_CACHE = False
        return _fallback_font()
    _KB_FONT_CACHE = font
    return font


def _fallback_font():
    for name in ("font_montserrat_16", "font_montserrat_14"):
        f = getattr(lv, name, None)
        if f:
            return f
    return None


def _load_png(path):
    """Read a PNG into an LVGL image descriptor, returning None on failure.

    The icons are opaque RGB already composited onto the field's background colour,
    so no alpha blending is needed and they sit seamlessly on the field.
    """
    try:
        with open(path, "rb") as f:
            data = f.read()
        return lv.img_dsc_t({"data_size": len(data), "data": data})
    except OSError:
        return None


def _asset(name):
    try:
        d = __file__.rsplit("/", 1)[0]
    except NameError:
        d = "/sdcard/factory/gui"
    return d + "/assets/" + name


def mode_consts():
    """Resolve the keyboard mode enum (lower, upper, special, number, arabic).

    In this binding MODE hangs off lv.keyboard.MODE (measured on board: TEXT_LOWER=0,
    TEXT_UPPER=1, SPECIAL=2, NUMBER=3, TEXT_ARABIC=8). Those measured values are the
    fallback when lookup fails. arabic returns -1 when unavailable (the firmware does
    not enable that feature) so it never matches.
    """
    mode_cls = getattr(lv.keyboard, "MODE", None)

    def pick(*attrs):
        for attr in attrs:
            v = getattr(mode_cls, attr, None)
            if v is None:
                v = getattr(lv.keyboard, attr, None)
            if v is not None:
                return v
        return None

    lower = pick("TEXT_LOWER")
    upper = pick("TEXT_UPPER")
    special = pick("SPECIAL")
    number = pick("NUMBER")
    arabic = pick("TEXT_ARABIC")
    if None in (lower, upper, special, number):
        lower, upper, special, number = 0, 1, 2, 3
    if arabic is None:
        arabic = -1
    return (lower, upper, special, number, arabic)


# Dark card colour defaults (consistent with Vision/network). Apps may override the
# accent etc.
COL_TEXT = 0xE6EAF0
COL_MUTED = 0x8A94A2
COL_CARD = 0x101418
COL_LINE = 0x3A4650
COL_ROW = 0x1E262E
COL_ACCENT = 0x74B5AC


class KbLayer:
    """Bottom-drawer keyboard overlay. Constructing opens it; poll() every frame;
    close() shuts it.

    Parameters:
      title       title text at the panel's top left
      on_ok       callback for the connect button (no arguments). Read the input
                  with kb.get_text(); this class handles closing.
      ok_text     connect button label (default "connect")
      password    whether the field is in password mode (default True)
      max_len     maximum field length (default 63)
      text_font   font for title/field/button (default montserrat_16)
      accent/card/line/row/text/muted  colour overrides
    """

    def __init__(self, title, on_ok, ok_text="connect", password=True,
                 max_len=63, text_font=None, accent=COL_ACCENT,
                 card=COL_CARD, line=COL_LINE, row=COL_ROW,
                 text=COL_TEXT, muted=COL_MUTED):
        self._on_ok = on_ok
        self._modes = mode_consts()
        self._font = text_font or _fallback_font()
        self._kb_font = load_kb_font()
        self.panel = None           # backdrop (None = not open / already closed)
        self.ta = None
        self.kb = None
        self._badge = None
        self._eye = None
        self._eye_img = None
        self._eye_dsc = None      # current eye icon descriptor (kept alive by reference, prevents GC)
        self._pwd_hidden = True
        self._prev = ""
        self._locked = False
        self._reverting = False
        self._press_on = False
        self._text_mode = self._modes[1]   # default to the uppercase page

        self._build(title, ok_text, password, max_len,
                    accent, card, line, row, text, muted)

    # ---------- Construction ----------
    def _build(self, title, ok_text, password, max_len,
               accent, card, line, row, text, muted):
        # Full-screen backdrop: tapping empty space closes; the panel swallows taps
        # to stop pass-through.
        backdrop = lv.obj(lv.scr_act())
        backdrop.set_size(800, 480)
        backdrop.align(lv.ALIGN.CENTER, 0, 0)
        backdrop.set_style_bg_color(lv.color_hex(0x000000), 0)
        backdrop.set_style_bg_opa(lv.OPA._70, 0)
        backdrop.set_style_border_width(0, 0)
        backdrop.set_style_pad_all(0, 0)
        backdrop.clear_flag(lv.obj.FLAG.SCROLLABLE)
        beep.on_click(backdrop, lambda e: self.close())

        # Bottom drawer: the panel sits on the screen's bottom edge with the keyboard
        # at the panel's very bottom (= the screen's bottom).
        # The panel height leaves room for what the keyboard really occupies (28px
        # keys make rows taller, so 400 keeps them uncompressed).
        # pad must be cleared and SCROLLABLE off: the default theme's ~20px pad offsets
        # align(BOTTOM_MID) (measured panel.x=-22) and content that slightly overflows
        # opens a scrollbar.
        panel = lv.obj(backdrop)
        panel.set_size(800, 400)
        panel.align(lv.ALIGN.BOTTOM_MID, 0, 0)
        panel.set_style_bg_color(lv.color_hex(card), 0)
        panel.set_style_border_width(2, 0)
        panel.set_style_border_color(lv.color_hex(line), 0)
        panel.set_style_radius(14, 0)
        panel.set_style_pad_all(0, 0)
        panel.clear_flag(lv.obj.FLAG.SCROLLABLE)
        panel.add_flag(lv.obj.FLAG.CLICKABLE)   # swallow taps so they do not reach the backdrop

        t = lv.label(panel)
        t.set_text(title)
        t.set_style_text_color(lv.color_hex(accent), 0)
        if self._font:
            t.set_style_text_font(self._font, 0)
        t.set_pos(16, 10)

        ta = lv.textarea(panel)
        # Width is derived backwards from sitting next to the connect button: the
        # button occupies 632..784 (16 right margin) and the field 16..620, leaving a
        # 12px gap -- the field used to be only 470 wide, leaving 158px empty on the
        # right so the button looked detached.
        ta.set_size(604, 42)
        ta.set_pos(16, 40)
        ta.set_password_mode(bool(password))
        ta.set_placeholder_text("password")
        ta.set_one_line(True)
        ta.set_max_length(max_len)
        # The default theme's light field is harsh on a dark card, so repaint it with
        # the card palette.
        ta.set_style_bg_color(lv.color_hex(row), 0)
        ta.set_style_border_color(lv.color_hex(line), 0)
        ta.set_style_border_width(1, 0)
        ta.set_style_radius(8, 0)
        ta.set_style_text_color(lv.color_hex(text), 0)
        try:
            ta.set_style_text_color(lv.color_hex(muted),
                                    lv.PART.TEXTAREA_PLACEHOLDER)
        except Exception:
            pass
        if self._font:
            ta.set_style_text_font(self._font, 0)

        kb = lv.keyboard(panel)
        kb.set_size(784, 300)
        kb.align(lv.ALIGN.BOTTOM_MID, 0, 0)
        kb.set_textarea(ta)
        # Keyboard key feedback: a buttonmatrix has no object per key and emits
        # VALUE_CHANGED (not CLICKED), so bind attaches sound only. Pressing delete on
        # an empty field changes no text and makes no sound; a known small flaw.
        beep.bind(kb, lv.EVENT.VALUE_CHANGED)
        # The ⌨/✖ control key only emits CANCEL in C (without changing mode) and the
        # number page has no key back to the text page, so CANCEL is taken over and
        # turned into "back to lowercase".
        try:
            kb.add_event(self._on_cancel, lv.EVENT.CANCEL, None)
        except Exception:
            pass
        # The num map's back key icon is the hard-coded ⌨ (the icon cannot be
        # replaced), so a badge flush with the key face covers it. All geometry is
        # measured from on-board snapshot pixels (800x480 full screen):
        #   the keyboard widget is at abs (6,176) 784x300; its content area has an
        #   extra 14px padding (a child's get_x() is relative to the content area, so
        #   14 must be added to get the real position),
        #   the ⌨ key occupies (489,185)..(780,250) on screen,
        #   converted to the content area = (489-6-14, 185-176-14) = (469, -5), size
        #   293x66 (the width is calibrated from the pixel profile to the right edge
        #   at 780; 2px less exposes the key's grey border).
        #   The badge's bottom edge at 250 leaves 11px to the ✓ key's top at 261, so
        #   they do not touch.
        #
        # Three rules that must hold (all were hit):
        # 1) It must **cover the key face completely**: a badge smaller than the key
        #    lets the pressed-state dark fill show around it, looking like the text
        #    gained a background box.
        # 2) It must **not overhang the key face**: overhanging pushes into the ✓ key
        #    12px below, gluing the white and grey blocks together and looking like
        #    "abc and √ overlapping" (measured: 1px is enough to stick).
        # 3) The fill is **white** (matching the number keys), not key grey: ✓/⌫ are
        #    grey keys right next to it and a grey badge would smear into one block;
        #    white clearly marks it as a different key.
        # lv.obj rather than lv.btn: a btn's pressed state carries a theme shadow or
        # outline that draws a frame outside the key; obj has no state styles and,
        # being non-clickable, still passes taps through to the key beneath.
        badge = lv.obj(kb)
        badge.set_size(293, 66)
        badge.set_pos(469, -5)
        badge.set_style_bg_color(lv.color_hex(0xFFFFFF), 0)   # same colour as the number keys
        badge.set_style_bg_opa(255, 0)
        badge.set_style_border_color(lv.color_hex(0xD8D8D8), 0)
        badge.set_style_border_width(1, 0)
        badge.set_style_radius(8, 0)
        badge.set_style_pad_all(0, 0)
        badge.clear_flag(lv.obj.FLAG.CLICKABLE)
        badge.clear_flag(lv.obj.FLAG.SCROLLABLE)
        blab = lv.label(badge)
        blab.set_text("abc")
        blab.set_style_text_color(lv.color_hex(0x2B7A70), 0)   # accent colour (one step darker, readable on white)
        if self._kb_font is not None:
            try:
                blab.set_style_text_font(self._kb_font, 0)
            except Exception:
                pass
        blab.center()
        blab.clear_flag(lv.obj.FLAG.CLICKABLE)
        badge.add_flag(lv.obj.FLAG.HIDDEN)
        # Key popovers: the character floats above the key while pressed. The bubble
        # geometry is hard-coded in LVGL, so "bigger/taller" only comes from larger
        # keys and font size, applied to the ITEMS part.
        try:
            kb.set_popovers(True)
        except Exception:
            pass
        if self._kb_font is not None:
            try:
                kb.set_style_text_font(self._kb_font, lv.PART.ITEMS)
            except Exception:
                pass

        # A long press must yield only one character: default map keys lack NO_REPEAT
        # and keep feeding text while held; the binding exposes no long-press repeat
        # delay, so it is caught on text change.
        ta.add_event(self._on_changed, lv.EVENT.VALUE_CHANGED, None)
        # The case-mode constants live in different places across bindings; set only
        # when found
        for attr in ("TEXT_UPPERCASE", "MODE_TEXT_UPPERCASE"):
            mode = getattr(lv.keyboard, attr, None) or getattr(lv, attr, None)
            if mode is not None:
                try:
                    kb.set_mode(mode)
                except Exception:
                    pass
                break

        ok = lv.btn(panel)
        ok.set_size(152, 42)
        ok.set_pos(632, 40)
        ok.set_style_bg_color(lv.color_hex(accent), 0)
        ok.set_style_radius(10, 0)
        ok.set_style_border_width(0, 0)
        try:
            ok.set_style_transform_width(6, lv.STATE.PRESSED)
            ok.set_style_transform_height(6, lv.STATE.PRESSED)
        except Exception:
            pass
        ol = lv.label(ok)
        ol.set_text(ok_text)
        ol.set_style_text_color(lv.color_hex(0x10161F), 0)
        # Use the same 28px bold bitmap font as the keyboard keys (matching the
        # keycaps below); fall back to the card font when loading failed. It is
        # smaller than the 42px-tall button, so it does not overflow.
        if self._kb_font is not None:
            try:
                ol.set_style_text_font(self._kb_font, 0)
            except Exception:
                pass
        elif self._font:
            ol.set_style_text_font(self._font, 0)
        ol.center()
        ol.clear_flag(lv.obj.FLAG.CLICKABLE)
        beep.on_click(ok, lambda e: self._fire_ok())

        # Show/hide password eye button at the field's right end.
        # Created only in password mode (password=True); the 36x36 hit area is a ring
        # larger than the icon so a finger can hit it (the 24x24 icon alone is too
        # narrow).
        # The icon is already composited onto the field's background colour and is not a
        # transparent PNG -- it sits seamlessly on the field and avoids the colour shift
        # from alpha blending over the rounded border.
        # Semantics: masked text shows the "eye" (tap to reveal), plain text shows the
        # "crossed-out eye" (tap to hide again).
        eye = None
        eye_img = None
        eye_dsc = None
        if password:
            ta.set_style_pad_right(34, 0)   # reserve room for the icon so text does not overlap it
            eye_dsc = _load_png(_asset("icon_kb_eye.png"))
            eye = lv.obj(panel)
            eye.set_size(36, 36)
            eye.set_pos(588 - 6, 40 + (42 - 36) // 2)
            eye.set_style_bg_opa(lv.OPA.TRANSP, 0)
            eye.set_style_border_width(0, 0)
            eye.set_style_pad_all(0, 0)
            eye.clear_flag(lv.obj.FLAG.SCROLLABLE)
            eye_img = lv.img(eye)
            if eye_dsc is not None:
                eye_img.set_src(eye_dsc)
            eye_img.center()
            eye_img.clear_flag(lv.obj.FLAG.CLICKABLE)
            beep.on_click(eye, lambda e: self._toggle_pwd())

        self.panel = backdrop
        self.ta = ta
        self.kb = kb
        self._badge = badge
        self._eye = eye
        self._eye_img = eye_img
        self._eye_dsc = eye_dsc
        self._pwd_hidden = bool(password)   # masked by default (tap the eye to reveal)
        try:
            self._text_mode = kb.get_mode()
        except Exception:
            self._text_mode = self._modes[1]

    # ---------- Public ----------
    def get_text(self):
        if self.ta is None:
            return ""
        return self.ta.get_text() or ""

    def poll(self, touch):
        """Called every frame: press edge for repeat suppression plus the mode
        guard. touch = ctx.touch (may be None)."""
        if self.panel is None:
            return
        self._press_edge(touch)
        self._mode_guard()

    def close(self):
        if self.panel is None:
            return
        self.ta = None
        self.kb = None
        self._badge = None
        self._eye = None
        self._eye_img = None
        self._locked = False
        self._press_on = False
        try:
            self.panel.delete()
        except Exception:
            pass
        self.panel = None

    # ---------- Internal ----------
    def _fire_ok(self):
        cb = self._on_ok
        if cb is not None:
            cb()

    def _toggle_pwd(self):
        """Toggle password visibility and swap in the matching eye icon.

        Icon descriptors must not live only inside the img: set_src replaces the old
        one and the Python-side reference gets collected -> snow/blank appears on the
        image. So both descriptors are kept alive on the instance (released only when
        the overlay closes).
        """
        ta = self.ta
        if ta is None:
            return
        self._pwd_hidden = not self._pwd_hidden
        try:
            ta.set_password_mode(self._pwd_hidden)
        except Exception:
            return
        if self._eye_img is not None:
            name = "icon_kb_eye.png" if self._pwd_hidden else "icon_kb_eye_off.png"
            dsc = _load_png(_asset(name))
            if dsc is not None:
                self._eye_dsc = dsc
                try:
                    self._eye_img.set_src(dsc)
                except Exception:
                    pass

    def _on_changed(self, e):
        """One press accepts one character: repeat events roll back to the snapshot.

        Two layers covered: the LVGL keyboard's long-press repeat, and the FT5316's
        long-interval fake Up being read as "lift and press again". A real lift resets
        the allowance in _press_edge.
        """
        ta = self.ta
        if ta is None or self._reverting:
            return
        if self._locked:
            self._reverting = True
            try:
                ta.set_text(self._prev)
            except Exception:
                pass
            self._reverting = False
            return
        self._prev = ta.get_text() or ""
        self._locked = True

    def _press_edge(self, touch):
        """Reset the character allowance on the press rising edge (the next key only
        works after a real lift)."""
        pressed = touch is not None and touch.pressed
        if pressed and not self._press_on:
            self._locked = False
        self._press_on = pressed

    def _on_cancel(self, e):
        """CANCEL from the ⌨/✖ control key -> switch back to the lowercase text page
        (the number page's way home)."""
        if self.kb is None:
            return
        try:
            self.kb.set_mode(self._modes[0])
        except Exception:
            pass

    def _mode_guard(self):
        """Keyboard mode guard (polls get_mode every frame).

        The default map's switch key is hard-coded in C and the binding does not
        expose set_map, so its behaviour cannot change; the mode can only be caught
        where it lands:
        - "1#" hard-switches to special (the symbol page). What is wanted is a large
          numeric pad, so special is immediately converted to number (9 big digits).
          The number page's own ⌨ key returns to lowercase.
        - "أب" (the Arabic page, mode 8): the font has no Arabic glyphs so the whole
          page is tofu; bounce straight back to the current text page, making the tap
          effectively a no-op (newer firmware removed that key, so this expires by
          itself).
        """
        kb = self.kb
        if kb is None:
            return
        lower, upper, special, number, arabic = self._modes
        try:
            mode = kb.get_mode()
        except Exception:
            return
        if mode == special:
            try:
                kb.set_mode(number)
            except Exception:
                pass
        elif mode == number:
            if self._badge is not None:
                try:
                    self._badge.clear_flag(lv.obj.FLAG.HIDDEN)
                except Exception:
                    pass
        elif mode == arabic:
            try:
                kb.set_mode(self._text_mode)
            except Exception:
                pass
        elif mode == lower or mode == upper:
            self._text_mode = mode
            if self._badge is not None:
                try:
                    self._badge.add_flag(lv.obj.FLAG.HIDDEN)
                except Exception:
                    pass
