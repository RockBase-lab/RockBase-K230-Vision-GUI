# The 56px status bar: Home at the left, title or clock in the middle, wifi and
# battery at the right.
#
# Every icon is a bitmap. Built-in SYMBOLs are not used for the right-hand icons
# because this firmware's built-in fonts are only 14/16px, too small inside a 56px
# bar; and the clock cannot use live text at all, since under RENDER_MODE.FULL a
# per-second change repaints the whole screen and tiny_ttf re-rasterises at ~150ms
# each time, a lag the eye clearly sees.
#
# One StatusBar instance per screen. The resident home keeps its own while an app
# screen owns another; going back repoints Launcher.status at the home instance and
# resets its cached keys, so the first tick re-reads wifi and charge state instead
# of trusting a key frozen before the app started.

import time

import lvgl as lv

from core import i18n
from core import paths
from hardware import battery
from net import service as net_mgr
from ui.image import load as load_png
from shell import widgets
from shell import tile_cache
from shell.registry import _sym
from shell.theme import (COL_TEXT, STATUS_H, STATUS_BATT_W,
                         CLOCK_CELL_W, CLOCK_COLON_W, CLOCK_H)

STATUS_ICONS = {}  # PNG icon cache: home / wifi_* / battery_*
CLOCK_IMGS = {}    # clock digit bitmap cache: '0'..'9' / ':'


def battery_level():
    """Charge cell count 0-3 for the status bar, 0 when the pack cannot be read."""
    try:
        state = battery.status()
    except BaseException as error:
        print("[GUI] battery read failed: {}".format(error))
        return 0
    if state is None:
        return 0
    return state[2]


def status_icon(name):
    """Load and cache a status icon (assets/status/icon_status_<name>.png) on use.

    Repainting a PNG is just a blit (measured 0ms), so there is no reason to
    pre-decode the whole set.
    """
    if name in STATUS_ICONS:
        return STATUS_ICONS[name]
    dsc = load_png(paths.asset_path("status", "icon_status_{}.png".format(name)))
    STATUS_ICONS[name] = dsc
    return dsc


def clock_images():
    """Load the clock digit bitmaps (Nunito Bold 32px)."""
    if CLOCK_IMGS:
        return CLOCK_IMGS
    for ch in "0123456789":
        CLOCK_IMGS[ch] = load_png(paths.asset_path("glyphs", "clock_{}.png".format(ch)))
    CLOCK_IMGS[":"] = load_png(paths.asset_path("glyphs", "clock_colon.png"))
    return CLOCK_IMGS


class StatusBar:
    """Builds the bar inside an already positioned container and refreshes it.

    Immersive app screens have no status bar at all; the launcher keeps None there.
    """

    def __init__(self, parent, center_text, show_clock):
        self.clock = None          # digit widgets (None = no clock on this screen)
        self.wifi_img = None
        self.batt_img = None
        self._wifi_key = None      # last variant applied, avoids a set_src every second
        self._battery_key = None
        self._build(parent, center_text, show_clock)

    def reset_keys(self):
        """Force the next tick to re-apply both icons.

        Used when this bar becomes the active screen's again: its widgets kept the
        src they had before the app started, while the per-second refresh went to
        the app screen's widgets.
        """
        self._wifi_key = None
        self._battery_key = None

    def tick(self):
        """Per-second refresh, driven from the main loop."""
        self.update_clock()
        self.update_wifi()
        self.update_battery()

    # ---- build ----
    def _build(self, status, center_text, show_clock):
        self.clock = None
        center = lv.label(status)
        center.set_text(center_text)
        center.set_style_text_color(lv.color_hex(COL_TEXT), 0)
        if center_text and i18n.needs_cjk(center_text):
            # App title in the status bar: the default montserrat_16 has no CJK
            # glyphs, so a localized title takes the preloaded 16px UI face.
            f = tile_cache.cjk_font(i18n.CJK_FONT_16)
            if f is not None:
                center.set_style_text_font(f, 0)
        center.align(lv.ALIGN.CENTER, 0, 0)
        if show_clock:
            self._build_clock(status)

        # Right-hand icons: battery hugs the edge, wifi sits to its left. The battery
        # carries four charge cells and is replaced on the 1s tick from
        # battery.status(), the same way wifi swaps its signal variant.
        batt_w = STATUS_BATT_W
        batt_src = status_icon("battery_" + str(battery_level()))
        if batt_src is not None:
            batt_icon = lv.img(status)
            batt_icon.set_src(batt_src)
            batt_icon.align(lv.ALIGN.RIGHT_MID, -14, 0)
            widgets.no_hit(batt_icon)
            self.batt_img = batt_icon
            self._battery_key = None
            self.update_battery()
        else:
            self.batt_img = None
        wifi_src = status_icon("wifi")
        if wifi_src is not None:
            wifi_icon = lv.img(status)
            wifi_icon.set_src(wifi_src)
            wifi_icon.align(lv.ALIGN.RIGHT_MID, -(14 + batt_w + 14), 0)
            widgets.no_hit(wifi_icon)
            # Keep this widget so update_wifi can swap its src every second based on
            # connection state/rssi. The key must start cleared: it caches "the
            # variant last applied", but this widget is brand new (white full bars).
            # Left set, the first tick would skip set_src and the icon would stay
            # white while disconnected (hit on board).
            self.wifi_img = wifi_icon
            self._wifi_key = None
            self.update_wifi()
        else:
            self.wifi_img = None
        if batt_src is None and wifi_src is None:
            # Fallback: both PNGs are missing, so use built-in symbols
            right = lv.label(status)
            parts = []
            wifi = _sym("WIFI")
            batt_sym = _sym("BATTERY_FULL", "BATTERY_3")
            if wifi:
                parts.append(wifi)
            if batt_sym:
                parts.append(batt_sym)
            right.set_text(" ".join(parts))
            right.set_style_text_color(lv.color_hex(COL_TEXT), 0)
            right.align(lv.ALIGN.RIGHT_MID, -14, 0)

    def _build_clock(self, status):
        # HH:MM:SS = 6 digits + 2 colons. Each digit is positioned absolutely
        # relative to CENTER: a flex container would crop the first and last digit
        # symmetrically when its width falls short (hit on board).
        slots_w = [CLOCK_CELL_W, CLOCK_CELL_W, CLOCK_COLON_W,
                   CLOCK_CELL_W, CLOCK_CELL_W, CLOCK_COLON_W,
                   CLOCK_CELL_W, CLOCK_CELL_W]
        total_w = sum(slots_w)
        imgs = clock_images()
        colon_dsc = imgs.get(":")
        cells = []
        x = -total_w // 2
        for slot, sw in enumerate(slots_w):
            im = lv.img(status)
            im.set_size(sw, CLOCK_H)
            if slot in (2, 5) and colon_dsc is not None:
                im.set_src(colon_dsc)
            im.align(lv.ALIGN.CENTER, x + sw // 2, 0)
            widgets.no_hit(im)
            cells.append(im)
            x += sw
        self.clock = cells
        self.update_clock()

    # ---- refresh ----
    def update_clock(self):
        """Refresh the digit bitmaps. Only the home screen builds a clock, so this
        returns immediately inside an app."""
        ws = self.clock
        if not ws:
            return
        try:
            tm = time.localtime()
            text = "{:02d}{:02d}{:02d}".format(tm[3], tm[4], tm[5])
        except Exception:
            text = "000000"
        imgs = clock_images()
        di = 0
        for slot in range(8):
            if slot in (2, 5):
                continue
            dsc = imgs.get(text[di])
            if dsc is not None:
                ws[slot].set_src(dsc)
            di += 1

    def update_wifi(self):
        """Refresh the wifi icon; skipped on immersive screens (wifi_img is None)."""
        img = self.wifi_img
        if img is None:
            return
        key = _wifi_state_key()
        if key == self._wifi_key:
            return
        src = status_icon("wifi_" + key)
        if src is not None:
            try:
                img.set_src(src)
                self._wifi_key = key
            except BaseException as error:
                print("[GUI] wifi icon error: {}".format(error))

    def update_battery(self):
        """Refresh the battery icon from the shared ADC cache.

        battery.status() is cached for 20s inside the module, so the 1s tick only
        costs a compare and a set_src when the charge cell actually changes.
        """
        img = self.batt_img
        if img is None:
            return
        key = battery_level()
        if key == self._battery_key:
            return
        src = status_icon("battery_" + str(key))
        if src is not None:
            try:
                img.set_src(src)
                self._battery_key = key
            except BaseException as error:
                print("[GUI] battery icon error: {}".format(error))


def _wifi_state_key():
    """Pick the wifi icon variant ("off"/"1"/"2"/"3") from the connection state plus
    rssi. The bucket thresholds live in net_mgr and share their source with the
    signal colouring in the Network list."""
    try:
        return net_mgr.wifi_icon_key()
    except Exception:
        return "off"


def build_home_button(status):
    """Large Home button at the top-left; disabled by the caller on the home screen."""
    home_btn = lv.btn(status)
    home_btn.set_size(STATUS_H - 12, STATUS_H - 12)
    home_btn.align(lv.ALIGN.LEFT_MID, 8, 0)
    home_btn.set_style_bg_opa(lv.OPA.TRANSP, 0)
    home_btn.set_style_radius(10, 0)
    home_btn.set_style_bg_color(lv.color_hex(COL_TEXT), lv.STATE.PRESSED)
    home_btn.set_style_bg_opa(lv.OPA._10, lv.STATE.PRESSED)
    widgets.clean(home_btn)
    home_src = status_icon("home")
    if home_src is not None:
        home_icon = lv.img(home_btn)
        home_icon.set_src(home_src)
        home_icon.center()
        widgets.no_hit(home_icon)
    else:
        # Fallback: use the built-in symbol when the PNG is missing
        home_icon = lv.label(home_btn)
        home_icon.set_text(_sym("HOME"))
        home_icon.set_style_text_color(lv.color_hex(COL_TEXT), 0)
        f = widgets.font("font_montserrat_24", "font_montserrat_16")
        if f:
            home_icon.set_style_text_font(f, 0)
        home_icon.center()
    return home_btn
