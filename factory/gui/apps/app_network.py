# Network app: WiFi connection flow plus timezone/NTP settings.
#
# Service and UI are separate: connecting, retrying and NTP all live in net_mgr
# (a long-running singleton started by launcher.py). This app is only the
# configuration front end -- destroy() deletes widgets and never disconnects, so
# WiFi stays up after leaving (required, to leave room for background web
# services later).
#
# Layout (non-immersive, content area 800x424):
#   left card : scan list (tap an SSID to open the password keyboard), with the
#               title and the scan button inside the card
#   right card: upper status area (large state + IP + separator + saved/tries/ntp
#               rows), lower settings area (full-width timezone roller plus the
#               sync-now and connect/disconnect toggle)
#
# Visual language matches the other apps: card fill, border and radius follow the
# Vision overlay (0x101418 with a 0x3A4650 outline), rows and buttons use
# 0x1E262E, and the accent is the same grey-teal 0x74B5AC as the desktop icon
# (gui.APP_ACCENTS["network"]) rather than a saturated neon colour.
#
# Blocking discipline: scan and NTP run on net_mgr's worker thread and
# wlan.connect itself returns immediately. app.step() only polls state and
# refreshes widgets, with no blocking calls.

import time

import lvgl as lv

from net import service as net_mgr
import beep
import kb_layer


def _font(*names):
    for name in names:
        font = getattr(lv, name, None)
        if font:
            return font
    return None


COL_TEXT = 0xE6EAF0
COL_MUTED = 0x8A94A2
COL_ACCENT = 0x74B5AC      # network accent (same as the desktop icon, desaturated on purpose)
COL_WARN = 0xFFC857
COL_ERR = 0xFF6B6B
COL_CARD = 0x101418       # card fill: same as the Vision overlay panel
COL_LINE = 0x3A4650       # card border / separator: same as Vision
COL_ROW = 0x1E262E        # list row / button fill: same as the Vision selection row

# Signal colours. The bucket thresholds come from net_mgr (RSSI_STRONG_MIN /
# RSSI_FAIR_MIN), the same source as the status-bar wifi bars, so the two cannot
# drift apart.
COL_RSSI_STRONG = 0x4CD97B    # green: strong
COL_RSSI_FAIR = 0xFFC857      # yellow: fair (same as COL_WARN)
COL_RSSI_WEAK = 0xFF6B6B      # red: weak (same as COL_ERR)


def _rssi_color(rssi):
    bucket = net_mgr.rssi_bucket(rssi)
    if bucket == 2:
        return COL_RSSI_STRONG
    if bucket == 1:
        return COL_RSSI_FAIR
    return COL_RSSI_WEAK

# While connected, scan always returns empty within ~100 ms because the radio is
# locked to one channel. Filling the list straight from the cache then looks like
# no scan happened at all, so "scanning..." is held for at least this long to make
# the action visible rather than decorative.
SCAN_MIN_MS = 650

STATE_TEXT = {
    net_mgr.IDLE: "offline",
    net_mgr.CONNECTING: "connecting",
    net_mgr.CONNECTED: "connected",
    net_mgr.GAVE_UP: "stopped",
}
STATE_COLOR = {
    net_mgr.IDLE: COL_MUTED,
    net_mgr.CONNECTING: COL_WARN,
    net_mgr.CONNECTED: COL_ACCENT,
    net_mgr.GAVE_UP: COL_ERR,
}

# ---------------- timezone roller bitmaps (baked offline) ----------------
# Measured on board under FULL render, tiny_ttf re-rasterises every frame (3184 ms
# per frame while scrolling), so a live font-size gradient always freezes; a
# bitmap blit costs ~0 ms. The three sheets (selected / adjacent / far) are 25
# vertically stacked XRGB8888 cells each, sliced into separate small bytearrays
# on load: once the board heap is fragmented a single 748 KB contiguous malloc
# always fails, so only chained small allocations are safe.
_TZ_SHEETS = None       # [[dsc...]...] per tier; False = load failed; None = not loaded
_TIER_CELL_H = [44, 30, 22]   # must match the TIERS cell heights baked into the sheets


def _tz_cache_dir():
    try:
        d = __file__.rsplit("/", 1)[0]
    except NameError:
        d = "/sdcard/factory/gui/apps"
    return d.rsplit("/", 1)[0] + "/assets/cache"


def _tz_load_sheets():
    """Load the three sprite sheets and slice them per row. Returns None on
    failure, degrading the picker to plain text."""
    global _TZ_SHEETS
    if _TZ_SHEETS is not None:
        return _TZ_SHEETS or None
    import gc
    tiers = []
    try:
        for tier in range(3):
            path = "{}/tz_t{}.bin".format(_tz_cache_dir(), tier)
            with open(path, "rb") as f:
                head = f.read(12)
                if len(head) < 12 or head[:4] != b"TZS1":
                    raise ValueError("bad header {}".format(repr(head[:4])))
                count = head[5]
                w = head[6] | (head[7] << 8)
                cell_h = head[8] | (head[9] << 8)
                cf = head[10] | (head[11] << 8)
                rows = []
                for _ in range(count):
                    raw = f.read(w * cell_h * 4)   # one small allocation per row
                    if len(raw) != w * cell_h * 4:
                        raise ValueError("short row")
                    rows.append(lv.img_dsc_t(
                        {"header": {"cf": cf, "w": w, "h": cell_h},
                         "data_size": len(raw), "data": raw}))
                tiers.append(rows)
    except BaseException as error:
        print("[net-app] tz sheets load failed: {}".format(error))
        _TZ_SHEETS = False
        return None
    _TZ_SHEETS = tiers
    gc.collect()
    print("[net-app] tz sheets loaded: {} tiers, free={}KB".format(
        len(tiers), gc.mem_free() // 1024))
    return tiers


def _tz_free_sheets():
    """Release the ~1.4 MB bitmap heap when leaving the app; it is reloaded on
    the next entry."""
    global _TZ_SHEETS
    _TZ_SHEETS = None
    import gc
    gc.collect()


class TzPicker:
    """An iOS-style timezone roller: free scrolling, three size/brightness tiers,
    snapping on release.

    - 25 stops (UTC-12..+12) with 7 rows visible; PITCH is the row pitch, and size
      and opacity fall into three tiers by |idx-pos| (the selection is largest and
      brightest, dimming outwards).
    - Drag points come from ctx.touch.read_raw() because the event object on this
      binding has no get_current_point; the snap animation is driven by poll()
      since this binding has no lv.anim.
    - One lv.img per row; set_src only swaps the dsc reference, because the
      bitmaps are pre-baked and each frame just moves y.
    """

    COUNT = 25
    VISIBLE = 5           # centre +-2; 5*PITCH = 220 <= the 226 container height, nothing clipped
    PITCH = 44
    SNAP_MS = 180
    # One FT5316 drag interleaves several phantom Up events (measured up to 12;
    # phantom Up -> next contact p50 = 20-26 ms, max = 40 ms). The picker consumes
    # raw points directly instead of going through the TouchScreen bridge, so it
    # applies its own holdoff here: an Up does not snap immediately, and a new
    # contact inside HOLDOFF counts as the same drag. Must stay > 40.
    HOLDOFF_MS = 45
    TIER_OPA = [255, 165, 95]
    # The tier boundaries must be 0.5/1.5: tier0 (the large selected style) is
    # exactly "the row nearest the centre", identical to the round(_pos) used when
    # snapping on release. With a boundary above 0.5 (0.62 was tried), _pos halfway
    # between tiers made two rows render as selected at once, so the row that
    # looked selected disagreed with where the roller snapped (saw +9 selected yet
    # it snapped to +8/+10).
    TIER_EDGE = (0.5, 1.5)

    def __init__(self, app, card, x, y, w, h):
        self.app = app
        self.container = lv.obj(card)
        self.container.set_size(w, h)
        self.container.set_pos(x, y)
        self.container.set_style_bg_opa(lv.OPA.TRANSP, 0)
        self.container.set_style_border_width(0, 0)
        self.container.set_style_pad_all(0, 0)
        self.container.clear_flag(lv.obj.FLAG.SCROLLABLE)
        try:
            self.container.set_style_clip_corner(True, 0)
        except Exception:
            pass

        self.sheets = _tz_load_sheets()
        self.rows = []
        for _ in range(self.VISIBLE):
            if self.sheets:
                im = lv.img(self.container)
            else:
                im = lv.label(self.container)   # bitmaps missing: plain-text fallback
                im.set_style_text_color(lv.color_hex(COL_TEXT), 0)
            self.rows.append(im)

        self._cy = h // 2
        # Hit rectangle in absolute screen coordinates: the card sits at content
        # TOP_RIGHT(-8,8), the content top edge is ctx.content_y, and the card is
        # 342 wide, so card.x = 800-342-8.
        self._sx0 = (app.ctx.screen_w - 342 - 8) + x
        self._sy0 = app.ctx.content_y + 8 + y
        self._sx1 = self._sx0 + w
        self._sy1 = self._sy0 + h
        self._scy = self._sy0 + self._cy

        self._pos = 12.0
        self._last_pos = None      # _pos at the last render (idle frame skipping)
        self._committed = 12
        self._drag = None          # (finger_y, pos0)
        self._up_pending = False   # an Up arrived but holdoff has not elapsed (FT5316 phantom Up)
        self._up_ms = 0
        self._up_y = 0
        self._anim = None          # (start_ms, from_pos, to_pos)
        self._shown = [None for _ in range(self.VISIBLE)]  # type: list
        # Centre stop crossed by dragging/animation: each crossing clicks once (iOS
        # picker feel).
        self._tick_idx = None

    def set_value(self, idx, animate=True):
        idx = max(0, min(self.COUNT - 1, idx))
        if animate:
            self._anim = (time.ticks_ms(), self._pos, float(idx))
        else:
            self._pos = float(idx)
            self._committed = idx
        self._last_pos = None      # force a render next frame
        self._render()

    def value(self):
        return int(round(self._pos))

    # ---------- touch ----------

    def _in_rect(self, x, y):
        return self._sx0 <= x <= self._sx1 and self._sy0 <= y <= self._sy1

    def _on_point(self, x, y, ev):
        if ev == 2:                      # Down
            if self._in_rect(x, y):
                self._drag = (y, self._pos)
                self._up_pending = False
                self._anim = None
                # Start counting from the current centre stop: each crossing
                # afterwards clicks once, the starting stop itself does not.
                self._tick_idx = int(round(self._pos))
        elif ev == 3:                    # Move
            if self._drag is not None:
                self._up_pending = False
                y0, pos0 = self._drag
                p = pos0 + (y0 - y) / float(self.PITCH)
                self._pos = max(0.0, min(self.COUNT - 1.0, p))
        else:                            # Up (may be an FT5316 phantom Up)
            if self._drag is None:
                return
            self._up_pending = True
            self._up_ms = time.ticks_ms()
            self._up_y = y

    def _finish_drag(self):
        """No new contact inside the holdoff window -> a real release: apply the tap
        or snap."""
        if self._drag is None:
            return
        y0, pos0 = self._drag
        y = self._up_y
        self._drag = None
        self._up_pending = False
        moved = abs(y - y0)
        if moved < 8:
            # Tap: select the row under the finger (iOS behaviour), otherwise snap
            # to the nearest row
            d = (y - self._scy) / float(self.PITCH)
            target = max(0.0, min(self.COUNT - 1.0,
                                  round(self._pos + d)))
        else:
            target = max(0.0, min(self.COUNT - 1.0, round(self._pos)))
        if target != self._pos:
            self._anim = (time.ticks_ms(), self._pos, float(target))
        else:
            self._maybe_commit()

    def _maybe_commit(self):
        idx = int(round(self._pos))
        if idx != self._committed:
            self._committed = idx
            # The click already played in poll()'s tier-crossing tick (the snap
            # animation goes through the same tick), so nothing is added here.
            self.app._tz_committed(idx)

    # ---------- per-frame driving ----------

    def poll(self):
        # While the password drawer is open the keyboard covers the picker area, so
        # do not drive it (avoids stray timezone taps).
        if self.app._pwd is not None:
            return
        touch = self.app.ctx.touch
        if touch is not None:
            pts = touch.read_raw()
            if pts:
                for x, y, ev, _tid in pts:
                    self._on_point(x, y, ev)
        # Pending Up: no new contact inside the holdoff window -> a real release
        # (bridges phantom FT5316 Ups).
        if self._up_pending and self._drag is not None and \
                time.ticks_diff(time.ticks_ms(), self._up_ms) >= self.HOLDOFF_MS:
            self._finish_drag()
        if self._anim is not None:
            start, p0, p1 = self._anim
            t = time.ticks_diff(time.ticks_ms(), start) / float(self.SNAP_MS)
            if t >= 1.0:
                self._pos = p1
                self._anim = None
                self._maybe_commit()
            else:
                e = t * t * (3.0 - 2.0 * t)      # smoothstep
                self._pos = p0 + (p1 - p0) * e
        # Each stop crossed while dragging or snapping clicks once (iOS picker
        # feel), keyed on the change of the centre stop round(_pos). beep.tap
        # throttles itself to 60 ms, so a fast flick does not machine-gun.
        if self._drag is not None or self._anim is not None:
            cur = int(round(self._pos))
            if self._tick_idx is None:
                self._tick_idx = cur
            elif cur != self._tick_idx:
                self._tick_idx = cur
                beep.tap()
        else:
            self._tick_idx = None
        # Do not re-render while idle (no drag, animation or pending Up and _pos
        # unchanged), saving one full-screen blit per frame.
        if self._drag is None and self._anim is None and \
                not self._up_pending and self._pos == self._last_pos:
            return
        self._last_pos = self._pos
        self._render()

    def _tier_of(self, d):
        if d < self.TIER_EDGE[0]:
            return 0
        if d < self.TIER_EDGE[1]:
            return 1
        return 2

    def _render(self):
        center = int(round(self._pos))
        for slot in range(self.VISIBLE):
            idx = center + (slot - self.VISIBLE // 2)
            im = self.rows[slot]
            if idx < 0 or idx >= self.COUNT:
                try:
                    im.add_flag(lv.obj.FLAG.HIDDEN)
                except Exception:
                    im.set_y(-200)
                self._shown[slot] = None
                continue
            d = abs(idx - self._pos)
            tier = self._tier_of(d)
            try:
                im.clear_flag(lv.obj.FLAG.HIDDEN)   # the row may have been hidden last frame
            except Exception:
                pass
            if self.sheets:
                cell_h = _TIER_CELL_H[tier]
                y_top = self._cy + (idx - self._pos) * self.PITCH - cell_h // 2
                key = (idx, tier)
                if self._shown[slot] != key:
                    im.set_src(self.sheets[tier][idx])
                    try:
                        im.set_size(170, cell_h)
                        im.set_style_opa(self.TIER_OPA[tier], 0)
                    except Exception:
                        pass
                    self._shown[slot] = key
                im.align(lv.ALIGN.TOP_MID, 0, int(y_top))
            else:
                label = "UTC{:+d}".format(idx - 12)
                if self._shown[slot] != (idx, tier):
                    im.set_text(label)
                    try:
                        im.set_style_opa(self.TIER_OPA[tier], 0)
                    except Exception:
                        pass
                    self._shown[slot] = (idx, tier)
                im.align(lv.ALIGN.TOP_MID, 0,
                         int(self._cy + (idx - self._pos) * self.PITCH - 12))

    def destroy(self):
        # close_app calls this before deleting app_scr. A redraw in between would
        # leave the row imgs pointing at a dangling dsc, so hide every row first
        # (hidden rows are not rendered) and only then free the bitmap heap.
        for im in self.rows:
            try:
                im.add_flag(lv.obj.FLAG.HIDDEN)
            except Exception:
                pass
        _tz_free_sheets()


# Matches the TIERS cell heights baked into the sheets (reading the dsc header
# varies per binding, so the table is the reliable source)
_TIER_CELL_H = (44, 30, 22)


class NetworkApp:
    def __init__(self, content, ctx):
        self.ctx = ctx
        self.content = content
        self.font = _font("font_montserrat_16", "font_montserrat_14")
        self._last_state = None
        self._last_ip = None
        self._last_ntp = None
        self._list_rows = []
        self._scan_at = 0
        self._scan_requested = False  # scan requested; may not have started while the worker is busy
        self._scan_running = False    # scan_start succeeded, waiting for the result
        self._scan_force = False      # whether this scan may go deep (disconnect/reconnect for a real list)
        self._scan_wait_logged = False  # the worker-busy wait was already logged (no per-frame spam)
        self._pwd = None            # password keyboard overlay (kb_layer.KbLayer, None = closed)
        self._pwd_ssid = ""

        # ---- left card: scan list (title and scan button live inside the card, so
        # the card border is the boundary) ----
        self.list_panel = lv.obj(content)
        self.list_panel.set_size(430, 408)
        self.list_panel.align(lv.ALIGN.TOP_LEFT, 8, 8)
        # List card: vertical scrolling only (rows are narrower than the card, so
        # nothing overflows horizontally).
        self._style_card(self.list_panel, scrollable=True)

        title = lv.label(self.list_panel)
        title.set_text("WiFi networks")
        title.set_style_text_color(lv.color_hex(COL_TEXT), 0)
        if self.font:
            title.set_style_text_font(self.font, 0)
        title.set_pos(14, 14)

        self.scan_btn = lv.btn(self.list_panel)
        self.scan_btn.set_size(96, 34)
        self.scan_btn.align(lv.ALIGN.TOP_RIGHT, -12, 10)
        self._style_btn(self.scan_btn)
        sl = lv.label(self.scan_btn)
        sl.set_text("scan")
        sl.set_style_text_color(lv.color_hex(COL_TEXT), 0)
        if self.font:
            sl.set_style_text_font(self.font, 0)
        sl.center()
        sl.clear_flag(lv.obj.FLAG.CLICKABLE)
        beep.on_click(self.scan_btn, lambda e: self._start_scan(force=True))

        # ---- right card: status table + timezone + buttons (no scrolling, the
        # content fits one screen) ----
        card = lv.obj(content)
        card.set_size(342, 408)
        card.align(lv.ALIGN.TOP_RIGHT, -8, 8)
        self._style_card(card)          # not scrollable + clip_corner

        self.big_font = _font("font_montserrat_24", "font_montserrat_20",
                              "font_montserrat_16")
        # The state is the star of this card: large and in the accent colour.
        self.st_state = self._card_label(card, "-", 16, 12, self.big_font)

        sep = lv.obj(card)
        sep.set_size(310, 1)
        sep.set_pos(16, 54)
        sep.set_style_bg_color(lv.color_hex(COL_LINE), 0)
        sep.set_style_border_width(0, 0)

        # Status details: a two-column table (keys at a fixed x=16, values at a
        # fixed x=112, fixed width, left aligned).
        self.st_ip = self._table_row(card, "ip", 64)
        self.st_net = self._table_row(card, "network", 90)
        self.st_ntp = self._table_row(card, "ntp", 116)

        # Roller heading: centred and in the accent colour, distinct from the muted
        # table keys above.
        tzlab = lv.label(card)
        tzlab.set_text("timezone")
        tzlab.set_style_text_color(lv.color_hex(COL_ACCENT), 0)
        if self.font:
            tzlab.set_style_text_font(self.font, 0)
        tzlab.align(lv.ALIGN.TOP_MID, 0, 146)

        # Separator between heading and roller, same style as the status one,
        # marking the top of the settings area.
        tzsep = lv.obj(card)
        tzsep.set_size(310, 1)
        tzsep.set_pos(16, 168)
        tzsep.set_style_bg_color(lv.color_hex(COL_LINE), 0)
        tzsep.set_style_border_width(0, 0)

        # Timezone picker: a custom iOS-style roller filling the lower half of the
        # card (y=174 to the bottom). The native roller cannot vary size and
        # brightness per row (non-selected rows share one style), so this is
        # hand-built: a clipped container with one label per stop, positioned,
        # sized and faded by distance from the selection. Drag points come from
        # ctx.touch.read_raw() since the event object on this binding has no
        # get_current_point.
        self.picker = TzPicker(self, card, x=16, y=174, w=310, h=226)
        self.picker.set_value(net_mgr.cfg.get("tz", 8) + 12, animate=False)

        self.st_err = self._card_label(card, "", 16, 404)
        self.st_err.set_style_text_color(lv.color_hex(COL_ERR), 0)

        # Refresh status immediately on entry, and populate the list from whatever
        # result already exists (last scan or cache) -- otherwise leaving and
        # re-entering skips the automatic scan because scan_get() is non-empty,
        # leaving the list blank.
        self._refresh_status(force=True)
        nets = net_mgr.scan_get() or net_mgr._scan_cache
        if nets:
            self._rebuild_list(nets)
            print("[net-app] list from existing result: {} row(s)".format(
                len(nets)))
        elif not net_mgr.scanning():
            self._start_scan()

    # ---------- widget helpers ----------

    def _style_card(self, obj, scrollable=False):
        # Same as the Vision overlay panel: dark fill, thin border, large radius.
        obj.set_style_bg_color(lv.color_hex(COL_CARD), 0)
        obj.set_style_border_width(1, 0)
        obj.set_style_border_color(lv.color_hex(COL_LINE), 0)
        obj.set_style_radius(12, 0)
        # The default theme gives lv.obj a ~20 px pad. Without clearing it every
        # child's set_pos is shifted by the pad, so coordinates no longer match the
        # design, the content leans to one side, and rows get squeezed until a
        # horizontal scrollbar appears. Clearing it makes set_pos a true absolute
        # coordinate relative to the card border.
        obj.set_style_pad_all(0, 0)
        # clip_corner clips every child to the card's rounded rectangle so nothing
        # is ever drawn outside the bounds (long text used to push a horizontal
        # scrollbar out of the right card, leaving roller and buttons looking
        # off-screen).
        try:
            obj.set_style_clip_corner(True, 0)
        except Exception:
            pass
        if scrollable:
            # List card: vertical scrolling only -- never a horizontal bar, even if
            # the content is measured a few pixels too wide.
            try:
                obj.set_scroll_dir(lv.DIR.VER)
            except Exception:
                pass
            for _cls in ("SCROLLBAR_MODE", "SCROLLBAR"):
                _mode = getattr(getattr(lv, _cls, None), "AUTO", None)
                if _mode is not None:
                    try:
                        obj.set_scrollbar_mode(_mode)
                        break
                    except Exception:
                        continue
        else:
            # Status card: scrolling fully off and the bar hidden, guaranteeing a
            # single-screen layout with no horizontal bar.
            obj.clear_flag(lv.obj.FLAG.SCROLLABLE)
            for _cls in ("SCROLLBAR_MODE", "SCROLLBAR"):
                _off = getattr(getattr(lv, _cls, None), "OFF", None)
                if _off is not None:
                    try:
                        obj.set_scrollbar_mode(_off)
                        break
                    except Exception:
                        continue

    def _style_btn(self, btn, color=COL_ROW):
        btn.set_style_bg_color(lv.color_hex(color), 0)
        btn.set_style_radius(10, 0)
        btn.set_style_border_width(0, 0)
        self._press_feedback(btn)

    def _press_feedback(self, obj):
        # Grow slightly around the centre while pressed and restore on release -- a
        # bubble confirming the tap landed.
        try:
            obj.set_style_transform_width(6, lv.STATE.PRESSED)
            obj.set_style_transform_height(6, lv.STATE.PRESSED)
        except Exception:
            pass

    def _card_label(self, parent, text, x, y, font=None):
        lab = lv.label(parent)
        lab.set_text(text)
        lab.set_style_text_color(lv.color_hex(COL_TEXT), 0)
        f = font or self.font
        if f:
            lab.set_style_text_font(f, 0)
        lab.set_pos(x, y)
        return lab

    def _table_row(self, card, key, y):
        # Two-column table: keys at a fixed x=16 in the muted colour, values at a
        # fixed x=112, fixed width, left aligned.
        kl = lv.label(card)
        kl.set_text(key)
        kl.set_style_text_color(lv.color_hex(COL_MUTED), 0)
        if self.font:
            kl.set_style_text_font(self.font, 0)
        kl.set_pos(16, y)
        vl = lv.label(card)
        vl.set_pos(112, y)
        vl.set_style_text_color(lv.color_hex(COL_TEXT), 0)
        if self.font:
            vl.set_style_text_font(self.font, 0)
        try:
            vl.set_width(200)
        except Exception:
            pass
        return vl

    # ---------- scan list ----------

    def _start_scan(self, force=False):
        # Only record the request: if the WLAN is busy with the connect/NTP worker,
        # scan_start returns False and the real start is retried in step() once the
        # worker is free. force=True (the scan button) allows a deep scan: while
        # connected the radio is locked to one channel and finds nothing, so it has
        # to disconnect, scan and reconnect, about 6 s. The automatic scan on entry
        # does not force.
        self._scan_requested = True
        self._scan_force = bool(force)
        self._scan_running = False
        self._scan_wait_logged = False
        self._scan_at = time.ticks_ms()
        self._set_list_msg("scanning...")
        print("[net-app] scan requested (force={} state={} worker_busy={} scanning={})".format(
            force, net_mgr.state, net_mgr._worker_busy, net_mgr.scanning()))

    def _set_list_msg(self, msg):
        self._clear_rows()
        lab = lv.label(self.list_panel)
        lab.set_text(msg)
        lab.set_style_text_color(lv.color_hex(COL_MUTED), 0)
        if self.font:
            lab.set_style_text_font(self.font, 0)
        lab.align(lv.ALIGN.TOP_MID, 0, 80)

    def _clear_rows(self):
        for r in self._list_rows:
            r.delete()
        self._list_rows = []

    def _rebuild_list(self, nets):
        self._clear_rows()
        saved = net_mgr.saved_ssid()
        y = 52          # rows start below the card's title line
        for ssid, rssi, sec in nets[:14]:
            is_saved = ssid == saved
            row = lv.btn(self.list_panel)
            row.set_size(406, 44)
            row.set_pos(12, y)
            row.set_style_bg_color(lv.color_hex(COL_ROW), 0)
            row.set_style_radius(10, 0)
            # Saved network: highlighted with an accent border (same as the Vision
            # selection row)
            row.set_style_border_width(2 if is_saved else 0, 0)
            row.set_style_border_color(lv.color_hex(COL_ACCENT), 0)
            lab = lv.label(row)
            lab.set_text(ssid)
            lab.set_style_text_color(
                lv.color_hex(COL_ACCENT if is_saved else COL_TEXT), 0)
            if self.font:
                lab.set_style_text_font(self.font, 0)
            lab.align(lv.ALIGN.LEFT_MID, 12, 0)
            lab.clear_flag(lv.obj.FLAG.CLICKABLE)
            # Signal strength sits at the right and its colour encodes the level:
            # green = strong, yellow = fair, red = weak. The right side used to hold
            # a WIFI icon, which only showed encryption, not quality.
            if rssi is not None:
                rl = lv.label(row)
                rl.set_text("{}dBm".format(rssi))
                rl.set_style_text_color(lv.color_hex(_rssi_color(rssi)), 0)
                if self.font:
                    rl.set_style_text_font(self.font, 0)
                rl.align(lv.ALIGN.RIGHT_MID, -12, 0)
                rl.clear_flag(lv.obj.FLAG.CLICKABLE)
                # Limit the SSID width so a long name does not run into the signal
                # value.
                try:
                    lab.set_width(406 - 24 - 92)
                except Exception:
                    pass
            beep.on_click(row, lambda e, s=ssid: self._open_pwd(s))
            self._press_feedback(row)
            self._list_rows.append(row)
            y += 48

    # ---------- password overlay ----------

    def _open_pwd(self, ssid):
        # The keyboard overlay comes from kb_layer.KbLayer (backdrop, bottom drawer,
        # textarea, keyboard and mode guard); other apps reuse it by swapping only
        # the title, callback and colours.
        if self._pwd is not None:
            return
        self._pwd_ssid = ssid
        self._pwd = kb_layer.KbLayer(title="join: {}".format(ssid),
                                     on_ok=self._connect_typed,
                                     ok_text="connect", password=True,
                                     max_len=63, text_font=self.font,
                                     accent=COL_ACCENT)

    def _connect_typed(self):
        pwd = self._pwd.get_text()
        self._pwd.close()
        self._pwd = None
        net_mgr.connect(self._pwd_ssid, pwd)
        self._refresh_status(force=True)

    def _tz_committed(self, idx):
        # The picker snapped to a new zone (idx=0..24 maps to UTC-12..+12): persist
        # the config and apply it at once. apply_tz re-renders last_ntp_text (the
        # last sync moment, purely local) in the new zone, and the forced refresh
        # updates the ntp row in the same frame without touching the network.
        net_mgr.cfg["tz"] = idx - 12
        net_mgr.save()
        net_mgr.apply_tz()
        self._refresh_status(force=True)

    # ---------- status refresh ----------

    def _refresh_status(self, force=False):
        st = net_mgr.state
        ip = net_mgr.ip()
        ntp = net_mgr.last_ntp_text
        if not force and st == self._last_state and ip == self._last_ip \
                and ntp == self._last_ntp:
            return
        self._last_state, self._last_ip, self._last_ntp = st, ip, ntp
        self.st_state.set_text(STATE_TEXT.get(st, st))
        self.st_state.set_style_text_color(
            lv.color_hex(STATE_COLOR.get(st, COL_TEXT)), 0)
        self.st_ip.set_text(ip or "-")
        saved = net_mgr.saved_ssid()
        self.st_net.set_text(saved or "-")
        self.st_ntp.set_text(ntp)
        self.st_err.set_text(net_mgr.last_error if st == net_mgr.GAVE_UP else "")

    def step(self):
        # net_mgr.poll() is driven by the launcher.py main loop (inside and outside
        # apps), so it is not called again here.
        self._refresh_status()
        # Timezone roller: consume touch points and advance the drag/snap animation
        # (poll driven, like paging).
        self.picker.poll()
        # Password keyboard overlay: driven every frame (press budget and mode guard
        # live inside kb_layer).
        if self._pwd is not None:
            self._pwd.poll(self.ctx.touch)
        # Scan driving: while the worker is busy scan_start returns False and the
        # next frame retries until it really starts.
        if self._scan_requested and not net_mgr.scanning() \
                and not net_mgr._worker_busy:
            if net_mgr.scan_start(self._scan_force):
                self._scan_requested = False
                self._scan_running = True
                self._scan_at = time.ticks_ms()
                print("[net-app] scan started (deep={})".format(self._scan_force))
        elif self._scan_requested and not self._scan_wait_logged:
            self._scan_wait_logged = True
            print("[net-app] scan deferred: worker_busy={} scanning={}".format(
                net_mgr._worker_busy, net_mgr.scanning()))
        # Completion check: it was started (_scan_running) and is no longer
        # scanning -> fill the list. Do not rely on catching the true->false edge of
        # scanning(): while connected, a scan can return empty in ~100 ms and the
        # edge falls between main-loop frames, leaving the list stuck on
        # "scanning..." forever. SCAN_MIN_MS also sets a floor, because an instant
        # result (especially the cache fallback) looks like no scan ran; holding
        # "scanning..." makes the action perceivable.
        if self._scan_running and not net_mgr.scanning() \
                and time.ticks_diff(time.ticks_ms(), self._scan_at) >= SCAN_MIN_MS:
            self._scan_running = False
            nets = net_mgr.scan_get()
            src = "fresh"
            if not nets:
                # A scan while connected is always empty: fall back to the last
                # successful scan (taken at boot).
                nets = net_mgr._scan_cache
                src = "cache"
            if nets:
                print("[net-app] scan done in {}ms: {} row(s) from {}".format(
                    time.ticks_diff(time.ticks_ms(), self._scan_at),
                    len(nets), src))
                self._rebuild_list(nets)
            elif net_mgr.state == net_mgr.CONNECTED:
                # No cache either (never scanned since boot): at least list the
                # network in use.
                ssid = net_mgr.cfg.get("ssid", "")
                if ssid:
                    print("[net-app] scan empty, showing current network only")
                    self._rebuild_list([(ssid, None, "WPA")])
                else:
                    print("[net-app] scan empty, no saved ssid")
                    self._set_list_msg("no networks found")
            else:
                print("[net-app] scan empty, not connected")
                self._set_list_msg("no networks found")
        elif self._scan_running and \
                time.ticks_diff(time.ticks_ms(), self._scan_at) > 25000:
            self._scan_running = False
            print("[net-app] scan timeout after 25s")
            self._set_list_msg("scan timeout")

    def destroy(self):
        # UI only. net_mgr is a resident service: WiFi stays connected and NTP keeps
        # being driven by the main loop.
        if self._pwd is not None:
            self._pwd.close()
            self._pwd = None
        # Hide the picker rows before freeing the bitmap heap (close_app calls
        # destroy before app_scr.delete, and a redraw in between would leave the row
        # imgs pointing at a dangling dsc).
        self.picker.destroy()


def create_app(content, ctx):
    return NetworkApp(content, ctx)
