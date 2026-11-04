# The launcher state machine: home screen, folder flyouts, app lifetime and the
# main loop.
#
# Visual spec: 800x480 dark theme
#   - Three dark levels: desktop darkest < tile next < border brightest
#   - 56px status bar at the top: Home at the top-left (exit inside an app), clock
#     in the middle, wifi/battery at the right (all bitmap icons);
#     no bottom navigation bar (exit = status-bar Home or the physical CANCEL key)
#   - 164x164 square icons, 4x2 per page; a full page is centred, a partial last
#     page starts at the top-left
#   - Page indicator dots: 12px dot / 30x12 pill when active
#   - Physical keys: GPIO0=CANCEL (active low), GPIO53=OK (active high)
#   - Navigation: CANCEL goes back exactly one level along the path the user came
#     in by (app -> the folder flyout it was opened from -> the page that folder
#     lives on -> page 0). See back_out(), which owns the rule.
#
# App contract (apps/app_xxx.py):
#   create_app(content, ctx) -> app
#     content: lv.obj the app mounts its UI on (area = full width below the status bar)
#     ctx:     context; the app calls ctx.request_exit() to ask for the home screen
#   app.step()      called once per main-loop iteration (non-blocking, state machine)
#   app.destroy()   called when returning home; releases hardware (sensor/audio, ...)

import gc
import os
import sys
import time

import lvgl as lv

from core import i18n
from core import paths
from core.errors import AppUnavailable
from hardware import panel
from net import service as net_mgr
from ui import beep
from ui import paging
from ui.image import load as load_png
from shell import display_init
from shell import perf
from shell import statusbar
from shell import tile_cache
from shell import touchscreen
from shell import widgets
from shell.buttons import HardwareButtons
from shell.context import AppContext
from shell.registry import APPS, FOLDER_MEMBERS, ACCENTS
from shell.theme import (COL_BG, COL_BAR, COL_TEXT, COL_MUTED, COL_DOT_OFF,
                         ACCENT, STATUS_H, GRID_PAD_L, GRID_MARGIN,
                         GRID_V_MARGIN, DOTS_GAP, DOT_H, TILE_SIZE, TILE_BG,
                         TILE_BORDER, TILE_RADIUS, TILE_PAD, NAME_COLOR)

DISPLAY_WIDTH = panel.WIDTH
DISPLAY_HEIGHT = panel.HEIGHT


class Launcher:
    def __init__(self):
        self.active_app = None
        self.active_meta = None
        self.ctx = None
        # Status bar of the current screen (None on an immersive app screen)
        self.status = None
        self.dots = []
        self.pager = None      # home pager (paging.SnapPager; None when there is one page)
        self._scratch = None   # temporary screen used only when rebuilding a live home
        # Home page to restore. A release_home app deletes the home screen, so the
        # pager is rebuilt from scratch and would otherwise always come back on page
        # 0. The page is saved before that deletion so CANCEL can step back one level
        # at a time (app -> folder flyout -> the page the folder lives on -> page 0).
        self.home_page = 0
        # Folder the running app was opened from, so exiting returns to its flyout
        # instead of the bare desktop. None when the app was opened from a top-level
        # tile.
        self.return_folder = None
        self.home_scr = None   # built once and kept resident for reuse
        self.app_scr = None    # current app screen, deleted explicitly on exit
        self.app_content = None  # current app content container (hidden if it fails)
        self.home_status = None  # home status bar (an app screen owns its own; restored on return)
        self.splash = None     # boot splash screen, deleted once home is up
        self.folder = None     # open folder flyout {"meta":..,"obj":..} (home screen only)

    # ---------- Shared shell: status bar (with the large Home) + content area ----------
    def _build_shell(self, center_text, show_clock, immersive=False):
        """56px status bar: large Home button at the top-left, title/clock centred,
        status icons at the right.

        No bottom navigation bar; leaving an app means tapping the status-bar Home
        or pressing the physical CANCEL key.
        Returns (scr, content, home_btn); home_btn is disabled on the home screen
        (None means immersive).
        """
        scr = lv.obj()
        scr.set_style_bg_color(lv.color_hex(COL_BG), 0)
        scr.set_style_bg_opa(lv.OPA.COVER, 0)
        scr.set_style_border_width(0, 0)

        if immersive:
            # Immersive: no status bar, the content area is the whole screen. The app
            # owns its own exit interaction.
            content = lv.obj(scr)
            content.set_size(DISPLAY_WIDTH, DISPLAY_HEIGHT)
            content.align(lv.ALIGN.TOP_MID, 0, 0)
            content.set_style_bg_opa(lv.OPA.TRANSP, 0)
            content.set_style_border_width(0, 0)
            content.set_style_pad_all(0, 0)
            # lv.obj is scrollable by default: children that touch the edge or
            # overflow (a detection label drawn 16px above its box, for instance)
            # extend the scroll range and put a scrollbar on the right/bottom edge
            # (hit on board). An immersive content area never needs scrolling.
            content.clear_flag(lv.obj.FLAG.SCROLLABLE)
            # No status bar on this screen: the previous screen's bar dies with that
            # screen, so nothing may keep pointing at it.
            self.status = None
            return scr, content, None

        # Status bar: the container itself must not scroll (otherwise overflowing
        # content puts a scrollbar on the right edge)
        status = lv.obj(scr)
        status.set_size(DISPLAY_WIDTH, STATUS_H)
        status.align(lv.ALIGN.TOP_MID, 0, 0)
        status.set_style_bg_color(lv.color_hex(COL_BAR), 0)
        status.set_style_border_width(0, 0)
        status.set_style_radius(0, 0)
        status.set_style_pad_all(0, 0)
        status.clear_flag(lv.obj.FLAG.SCROLLABLE)

        # Large Home button at the top-left (tapping it inside an app returns home;
        # it is disabled on the home screen)
        home_btn = statusbar.build_home_button(status)
        self.status = statusbar.StatusBar(status, center_text, show_clock)

        # Content area (from below the status bar down to the bottom of the screen)
        content = lv.obj(scr)
        content.set_size(DISPLAY_WIDTH, DISPLAY_HEIGHT - STATUS_H)
        content.align(lv.ALIGN.TOP_MID, 0, STATUS_H)
        content.set_style_bg_opa(lv.OPA.TRANSP, 0)
        content.set_style_border_width(0, 0)
        content.set_style_pad_all(0, 0)
        content.clear_flag(lv.obj.FLAG.SCROLLABLE)

        return scr, content, home_btn

    # ---------- Home screen ----------
    def _release_home_memory(self):
        """Destroy the home screen and release the tile bitmap cache (~1.5MB total)
        to hand a contiguous heap to a memory-hungry app.

        Prerequisite: the active screen has already switched away (deleting the
        active screen is never safe). dots/pager/status point at objects on the
        deleted screen and must be cleared with it, otherwise the next update would
        write to freed widgets.
        On the way back, build_home takes the full rebuild path (tiles reload from
        the SD cache in about a second).
        """
        if self.home_scr is not None:
            # Save the page before the pager is destroyed. build_home() rebuilds it
            # from scratch, and a fresh SnapPager always starts on page 0.
            if self.pager is not None:
                self.home_page = self.pager.page
            try:
                self.home_scr.delete()
                print("[GUI]   home screen deleted for release_home (page {})".format(
                    self.home_page))
            except BaseException as error:
                print("[GUI]   home screen delete error:", error)
            self.home_scr = None
        self.home_status = None
        self.status = None
        self.dots = []
        self.pager = None
        tile_cache.BAKED_TILES.clear()
        gc.collect()
        print("[GUI] home memory released | {}".format(widgets.mem_str()))

    def build_home(self):
        # The home screen is built exactly once: lv.scr_load does not destroy the old
        # screen, so rebuilding it on every app exit would pile up object trees
        # forever and make the UI slower the longer it runs.
        # A language change is the one reason to rebuild a resident home: the tile
        # bitmaps hold baked text, so consume_home_dirty() forces the full path below.
        if self.home_scr is not None and not i18n.consume_home_dirty():
            lv.scr_load(self.home_scr)
            self.status = self.home_status
            # Back on the home widgets: their src is frozen at whatever it was before
            # the app started (during the app, the per-second refresh went to the app
            # screen's widgets). Clearing the keys forces a fresh set_src next tick.
            if self.status is not None:
                self.status.reset_keys()
            return
        if self.home_scr is not None:
            # Language changed while the home screen stayed resident: drop it and its
            # baked tiles so the rebuild picks up the new language's cache. The active
            # screen is never the home here (build_home runs from close_app with the
            # app screen still active, or from run() before home exists), so deleting
            # it is safe. Guard anyway against deleting the active screen.
            self._scratch = None
            if self.home_scr == lv.scr_act():
                scratch = lv.obj()
                lv.scr_load(scratch)
                self._scratch = scratch
            try:
                self.home_scr.delete()
            except BaseException as error:
                print("[GUI] home delete for language failed: {}".format(error))
            self.home_scr = None
            self.home_status = None
            self.dots = []
            self.pager = None
            tile_cache.BAKED_TILES.clear()
            gc.collect()
        # Coming back from an app that used release_home: the tile bitmaps were
        # dropped, so reload them from the SD cache first
        if not tile_cache.BAKED_TILES:
            tile_cache.reload_all()
        print("[GUI] build_home: shell...")
        scr, content, home_btn = self._build_shell("", show_clock=True)
        # A Home button is meaningless on the home screen (we are already there):
        # disable it so it cannot steal events or mislead
        disabled = getattr(lv.STATE, "DISABLED", None)
        if home_btn is not None and disabled is not None:
            home_btn.add_state(disabled)

        area_w = DISPLAY_WIDTH
        area_h = DISPLAY_HEIGHT - STATUS_H

        # Paging: at most 4 columns x 2 rows = 8 icons per page, horizontal swipe + snap
        cols, rows = 4, 2
        per_page = cols * rows
        pages = [APPS[i:i + per_page] for i in range(0, len(APPS), per_page)] or [[]]

        # Vertical distribution (symmetric, bottom-up):
        #   GRID_V_MARGIN | row1 gap_y row2 | DOTS_GAP | dots DOT_H | GRID_V_MARGIN
        # rows_avail is the height available to the two icon rows; the row gap is
        # derived from it backwards so no blank space piles up at the bottom.
        rows_avail = area_h - 2 * GRID_V_MARGIN - DOTS_GAP - DOT_H
        # Derive spacing from the screen width / available height: fill the page as
        # much as possible while keeping the left-right (top-bottom) margins equal.
        gap_x = TILE_PAD
        if cols > 1:
            gap_x = max(TILE_PAD, (area_w - 2 * GRID_MARGIN - cols * TILE_SIZE) // (cols - 1))
        gap_y = TILE_PAD
        if rows > 1:
            gap_y = max(TILE_PAD, (rows_avail - rows * TILE_SIZE) // (rows - 1))
        grid_h = rows * TILE_SIZE + (rows - 1) * gap_y
        dots_y = GRID_V_MARGIN + grid_h + DOTS_GAP
        print("[GUI] grid: {}x{} tile={} gap=({},{}) span={}x{} | dots_y={} (bottom margin={})"
              .format(cols, rows, TILE_SIZE, gap_x, gap_y,
                      cols * TILE_SIZE + (cols - 1) * gap_x, grid_h,
                      dots_y, area_h - (dots_y + DOT_H)))
        wrap = lv.obj(content)
        wrap.set_size(area_w, grid_h)
        wrap.align(lv.ALIGN.TOP_MID, 0, GRID_V_MARGIN)
        wrap.set_style_bg_opa(lv.OPA.TRANSP, 0)
        wrap.set_style_border_width(0, 0)
        wrap.set_style_pad_all(0, 0)
        wrap.set_flex_flow(lv.FLEX_FLOW.ROW)
        # Scroll container setup (clear pad_column/pad_row, disable momentum/elastic/
        # scrollbar, set the snap duration) comes from the paging module so the
        # gallery and other apps share one set of parameters.
        paging.setup_horiz_scroller(wrap, area_w)
        # With a single page, scrolling is disabled outright: FT5316 touch jitter
        # would be classified as a scroll, and LVGL then cancels the icons' CLICKED
        # (which shows up as icons being very hard to tap).
        if len(pages) <= 1:
            wrap.clear_flag(lv.obj.FLAG.SCROLLABLE)

        for page_apps in pages:
            page = lv.obj(wrap)
            page.set_size(area_w, grid_h)
            page.clear_flag(lv.obj.FLAG.SCROLLABLE)
            page.set_style_bg_opa(lv.OPA.TRANSP, 0)
            page.set_style_border_width(0, 0)
            page.set_flex_flow(lv.FLEX_FLOW.ROW_WRAP)
            # A full page (per_page icons) is spread out and centred; a partial page
            # starts at the top-left instead and is not centred.
            if len(page_apps) >= per_page:
                page.set_flex_align(lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER,
                                    lv.FLEX_ALIGN.CENTER)
                # 16px sideways plus the 8px centring adds up to 24, exactly
                # GRID_MARGIN, so the left edge lines up with a partial page.
                # No vertical pad: the content height already equals grid_h, and
                # adding more would overflow and push the rows away.
                page.set_style_pad_left(GRID_PAD_L, 0)
                page.set_style_pad_right(GRID_PAD_L, 0)
                page.set_style_pad_top(0, 0)
                page.set_style_pad_bottom(0, 0)
            else:
                page.set_flex_align(lv.FLEX_ALIGN.START, lv.FLEX_ALIGN.START,
                                    lv.FLEX_ALIGN.START)
                # The left pad matches a full page's real outer margin, so the icons'
                # left edge does not jump when the page changes
                page.set_style_pad_left(GRID_MARGIN, 0)
                page.set_style_pad_right(GRID_PAD_L, 0)
                page.set_style_pad_top(0, 0)
                page.set_style_pad_bottom(0, 0)
            page.set_style_pad_row(gap_y, 0)
            page.set_style_pad_column(gap_x, 0)
            for meta in page_apps:
                self._make_app_tile(page, meta)

        # Page indicator dots (12px dot / 30x12 pill when active)
        if len(pages) > 1:
            self.dots = []
            dot_row = lv.obj(content)
            dot_row.set_size(area_w, DOT_H)
            dot_row.align(lv.ALIGN.TOP_MID, 0, dots_y)
            dot_row.set_style_bg_opa(lv.OPA.TRANSP, 0)
            dot_row.set_style_border_width(0, 0)
            dot_row.clear_flag(lv.obj.FLAG.SCROLLABLE)
            dot_row.set_flex_flow(lv.FLEX_FLOW.ROW)
            dot_row.set_flex_align(lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER,
                                   lv.FLEX_ALIGN.CENTER)
            dot_row.set_style_pad_all(0, 0)
            dot_row.set_style_pad_column(8, 0)
            for di in range(len(pages)):
                dot = lv.obj(dot_row)
                self._set_dot_style(dot, di == 0)
                self.dots.append(dot)

            def _set_dots(idx):
                for di, dot in enumerate(self.dots):
                    self._set_dot_style(dot, di == idx)

            def _on_page_changed(page):
                _set_dots(page)

            def _is_pressed():
                t = touchscreen.instance()
                return t is not None and t.pressed

            # paging.SnapPager owns the threshold test and the snapping (the same
            # logic the gallery and other apps use).
            self.pager = paging.SnapPager(
                wrap, area_w, len(pages),
                on_page_changed=_on_page_changed,
                is_pressed=_is_pressed)

        lv.scr_load(scr)
        print("[GUI] build_home: {} pages, {} apps | {}".format(
            len(pages), len(APPS), widgets.mem_str()))
        # Restore the page the user left from. A rebuild drops the pager, and without
        # this the desktop always came back on page 0, which is what made CANCEL look
        # like it skipped the folder level entirely.
        if self.pager is not None and self.home_page > 0:
            if self.home_page < self.pager.page_count:
                self.pager.go(self.home_page, animate=False)
                for di, dot in enumerate(self.dots):
                    self._set_dot_style(dot, di == self.home_page)
                print("[GUI] home page restored: {}".format(self.home_page))
            else:
                self.home_page = 0
        self.home_scr = scr
        # The language-rebuild guard may have parked the display on a scratch screen;
        # now that the new home is active, drop it.
        if self._scratch is not None:
            try:
                self._scratch.delete()
            except BaseException:
                pass
            self._scratch = None
        # The home status bar needs its own reference: entering an app makes
        # _build_shell repoint self.status at the app screen's bar (or None when
        # immersive), and the early-return path back home restores from this copy.
        # Failing to keep it leaves the wifi icon frozen at its boot value after
        # switching SSID and coming back (hit on board).
        self.home_status = self.status

    def _set_dot_style(self, dot, active):
        if active:
            dot.set_size(30, 12)
            dot.set_style_bg_color(lv.color_hex(ACCENT), 0)
        else:
            dot.set_size(12, 12)
            dot.set_style_bg_color(lv.color_hex(COL_DOT_OFF), 0)
        dot.set_style_bg_opa(lv.OPA.COVER, 0)
        dot.set_style_radius(6, 0)  # half of the 12px height; no 0xFFFF circle here
        dot.set_style_border_width(0, 0)

    def _make_app_tile(self, parent, meta):
        # A tile is one baked bitmap (button + icon + label already rendered), so a
        # full-screen repaint during a scroll costs a bitmap blit, about 0ms.
        # The bitmap itself is clickable, so hit testing is unchanged: one CLICKED
        # event per tile.
        baked = tile_cache.BAKED_TILES.get(meta["id"])
        if baked is not None:
            dsc, _raw = baked
            tile = lv.img(parent)
            tile.set_src(dsc)
            tile.set_size(TILE_SIZE, TILE_SIZE)
            tile.add_flag(lv.obj.FLAG.CLICKABLE)
            tile.clear_flag(lv.obj.FLAG.SCROLLABLE)
            # lv.img is not in the scroll chain by default: a drag started on a tile
            # must bubble up to the scrollable wrap for paging to work
            for _fn in ("SCROLL_CHAIN", "SCROLL_CHAIN_HOR", "SCROLL_CHAIN_VER"):
                _f = getattr(lv.obj.FLAG, _fn, None)
                if _f is not None:
                    tile.add_flag(_f)
        else:
            # Baked bitmap missing (should never happen): fall back to an empty
            # rounded button placeholder
            tile = lv.obj(parent)
            tile.set_size(TILE_SIZE, TILE_SIZE)
            tile.set_style_bg_color(lv.color_hex(TILE_BG), 0)
            tile.set_style_radius(TILE_RADIUS, 0)
            tile.add_flag(lv.obj.FLAG.CLICKABLE)
            tile.clear_flag(lv.obj.FLAG.SCROLLABLE)
            lb = lv.label(tile)
            lb.set_text(tile_cache.tile_name(meta))
            lb.center()

        def on_clicked(event, m=meta):
            print("[GUI] tile clicked: '{}'".format(m["name"]))
            if m.get("folder"):
                self._open_folder(m)
            else:
                # A top-level tile has no folder level to return to. Clearing the
                # breadcrumb here (not only in close_app) keeps a failed launch from
                # leaving a stale one behind for the next app.
                self.return_folder = None
                self.open_app(m)

        # Click sound plus app launch (beep.on_click plays first, then the callback)
        beep.on_click(tile, on_clicked)

    # ---------- Folder flyout ----------
    # The flyout lives on the home screen (no new screen), so the home tiles and
    # paging state stay exactly as they were and closing it only deletes one object
    # tree. Member tiles skip the tile bake (saving the cache and memory of 16
    # tiles) and are drawn directly as an icon PNG, or an accent badge plus symbol.

    def _open_folder(self, meta):
        if self.home_scr is None:
            return
        self._close_folder()
        members = FOLDER_MEMBERS.get(meta["id"], [])
        if not members:
            print("[GUI] folder '{}' empty".format(meta["id"]))
            return

        # Backdrop: full-screen translucent black that swallows home-screen taps;
        # tapping it closes the flyout.
        # It is attached to the home screen (scr) rather than content because content
        # is only 424px tall and the backdrop has to cover the status bar.
        scr = self.home_scr
        backdrop = lv.obj(scr)
        backdrop.set_size(DISPLAY_WIDTH, DISPLAY_HEIGHT)
        backdrop.set_pos(0, 0)
        backdrop.set_style_bg_color(lv.color_hex(0x000000), 0)
        backdrop.set_style_bg_opa(lv.OPA._60, 0)
        backdrop.set_style_border_width(0, 0)
        backdrop.set_style_radius(0, 0)
        backdrop.clear_flag(lv.obj.FLAG.SCROLLABLE)
        backdrop.add_flag(lv.obj.FLAG.CLICKABLE)
        beep.on_click(backdrop, lambda e: self._close_folder())

        # Panel: same visual language as the Vision flyout (dark fill + border +
        # large radius).
        n = len(members)
        cols = 3
        rows = (n + cols - 1) // cols
        cell = 112       # member tile (tap target >=100px; 3 rows still fit the panel)
        gap = 12
        panel_w = cols * cell + (cols - 1) * gap + 32
        panel_h = rows * cell + (rows - 1) * gap + 76   # 76 = title row + top/bottom pad
        panel = lv.obj(backdrop)
        panel.set_size(panel_w, panel_h)
        panel.align(lv.ALIGN.CENTER, 0, 0)
        panel.set_style_bg_color(lv.color_hex(COL_BAR), 0)
        panel.set_style_bg_opa(lv.OPA.COVER, 0)
        panel.set_style_border_width(1, 0)
        panel.set_style_border_color(lv.color_hex(TILE_BORDER), 0)
        panel.set_style_radius(20, 0)
        panel.set_style_pad_all(0, 0)
        panel.clear_flag(lv.obj.FLAG.SCROLLABLE)
        try:
            panel.set_style_clip_corner(True, 0)
        except Exception:
            pass

        title = lv.label(panel)
        folder_title = tile_cache.tile_name(meta)
        title.set_text(folder_title)
        title.set_style_text_color(lv.color_hex(COL_TEXT), 0)
        tf = tile_cache.tile_name_font(folder_title)
        if tf:
            title.set_style_text_font(tf, 0)
        title.align(lv.ALIGN.TOP_MID, 0, 14)

        grid = lv.obj(panel)
        # The grid must be **one ring larger** than the tile array: when an edge tile
        # grows on press it would otherwise cross the grid boundary and be clipped
        # (LVGL clips children by default), which looks like the dialog cut it off.
        # Hence the EXPAND padding on all four sides with the grid size bumped by
        # 2*EXPAND, keeping the content area exactly equal to the tile array total.
        grid_expand = 12
        content_w = cols * cell + (cols - 1) * gap
        content_h = rows * cell + (rows - 1) * gap
        grid.set_size(content_w + 2 * grid_expand, content_h + 2 * grid_expand)
        grid.align(lv.ALIGN.TOP_MID, 0, 52 - grid_expand)
        grid.set_style_bg_opa(lv.OPA.TRANSP, 0)
        grid.set_style_border_width(0, 0)
        grid.set_style_pad_left(grid_expand, 0)
        grid.set_style_pad_right(grid_expand, 0)
        grid.set_style_pad_top(grid_expand, 0)
        grid.set_style_pad_bottom(grid_expand, 0)
        grid.set_style_pad_row(gap, 0)
        grid.set_style_pad_column(gap, 0)
        grid.set_flex_flow(lv.FLEX_FLOW.ROW_WRAP)
        grid.set_flex_align(lv.FLEX_ALIGN.START, lv.FLEX_ALIGN.START,
                            lv.FLEX_ALIGN.START)
        grid.clear_flag(lv.obj.FLAG.SCROLLABLE)

        for m in members:
            self._make_member_tile(grid, m)

        # Disable home scrolling while the flyout is open: if FT5316 jitter were taken
        # for a scroll, LVGL would cancel the CLICKED of the backdrop and members (the
        # same trap as the single-page no-scroll case, hit on board).
        if self.pager is not None:
            self.pager.set_enabled(False)
        self.folder = {"meta": meta, "obj": backdrop}
        print("[GUI] folder '{}' opened: {} members | {}".format(
            meta["id"], n, widgets.mem_str()))

    def _make_member_tile(self, parent, meta):
        tile = lv.obj(parent)
        tile.set_size(112, 112)
        tile.set_style_bg_color(lv.color_hex(TILE_BG), 0)
        tile.set_style_bg_opa(lv.OPA.COVER, 0)
        tile.set_style_radius(16, 0)
        tile.set_style_border_width(1, 0)
        tile.set_style_border_color(lv.color_hex(TILE_BORDER), 0)
        tile.set_style_pad_all(0, 0)
        tile.add_flag(lv.obj.FLAG.CLICKABLE)
        tile.clear_flag(lv.obj.FLAG.SCROLLABLE)
        self._press_feedback(tile)

        icon_src = None
        if meta.get("icon"):
            # Flyout member icons use the 64px version scaled at build time
            # (tools/gen_member_icons.py).
            # Do not use the 112px original with set_size(64, 64): on lv.img a size
            # means **clip**, not scale, so only a top-left block of the icon shows
            # and it looks truncated (measured on board).
            # This binding also has no set_scale/set_style_transform_scale, and
            # SIZE_MODE only affects layout, not rendering, so scaling can only be
            # done offline.
            base = meta["icon"].rsplit("/", 1)[-1]
            icon_src = load_png(paths.asset_path("cache", "member_" + base))
            if icon_src is None:
                # Fallback: original image when the scaled copy is missing (it will
                # be clipped, but at least it is not blank)
                icon_src = load_png(paths.gui_path(meta["icon"]))
        if icon_src is not None:
            ic = lv.img(tile)
            ic.set_src(icon_src)
            # The 64px version is placed at its native size; no set_size call (that
            # would clip it).
            ic.align(lv.ALIGN.TOP_MID, 0, 6)
            widgets.no_hit(ic)
        else:
            # Placeholder member: accent round badge plus an LVGL symbol (built-in
            # symbol font, no TTF needed).
            idx = 0
            for fid, ms in FOLDER_MEMBERS.items():
                if meta in ms:
                    idx = ms.index(meta)
                    break
            accent = ACCENTS[idx % len(ACCENTS)]
            badge = lv.obj(tile)
            badge.set_size(56, 56)
            badge.align(lv.ALIGN.TOP_MID, 0, 10)
            badge.set_style_bg_color(lv.color_hex(accent), 0)
            badge.set_style_bg_opa(lv.OPA.COVER, 0)
            badge.set_style_radius(32, 0)
            badge.set_style_border_width(0, 0)
            # Padding must be cleared and SCROLLABLE turned off: the theme's default
            # pad eats space inside the circle, and once the symbol label overflows
            # the badge becomes scrollable, scrollbars appear at its right/bottom
            # edge and push the symbol out of view (hit on board: the coloured circle
            # looked "hollow" with an extra bar on its right).
            badge.set_style_pad_all(0, 0)
            badge.clear_flag(lv.obj.FLAG.SCROLLABLE)
            widgets.no_hit(badge)
            sym = lv.label(badge)
            sym.set_text(meta.get("symbol", ""))
            sym.set_style_text_color(lv.color_hex(COL_TEXT), 0)
            f = widgets.font("font_montserrat_20", "font_montserrat_16")
            if f:
                sym.set_style_text_font(f, 0)
            sym.center()
            widgets.no_hit(sym)

        lab = lv.label(tile)
        member_name = i18n.name(meta["id"], meta["name"])
        lab.set_text(member_name)
        lab.set_style_text_color(lv.color_hex(NAME_COLOR), 0)
        if i18n.needs_cjk(member_name):
            # 16px CJK: reuse the preloaded OCR face (SourceHanSans 16px) rather
            # than baking a second CJK size for one label row.
            f = tile_cache.cjk_font("ocr_cjk_16.bin")
        else:
            f = None
        if f is None:
            f = widgets.font("font_montserrat_14", "font_montserrat_16")
        if f:
            lab.set_style_text_font(f, 0)
        lab.align(lv.ALIGN.BOTTOM_MID, 0, -5)
        widgets.no_hit(lab)

        def on_clicked(event, m=meta):
            print("[GUI] folder member clicked: '{}'".format(m["name"]))
            # Remember the folder this member came from. Exiting the app then reopens
            # this flyout, so CANCEL walks back the way the user came in instead of
            # jumping straight to the desktop.
            if self.folder is not None:
                self.return_folder = self.folder["meta"]
            # Close the flyout before opening the app: open_app switches screens and
            # may call release_home, which would destroy the flyout objects still
            # attached to the home screen and leave the state dangling.
            self._close_folder()
            self.open_app(m)

        beep.on_click(tile, on_clicked)

    def _press_feedback(self, obj):
        # Same treatment as app_network: grow slightly on press, restore on release.
        try:
            obj.set_style_transform_width(6, lv.STATE.PRESSED)
            obj.set_style_transform_height(6, lv.STATE.PRESSED)
        except Exception:
            pass

    def _close_folder(self):
        if self.folder is None:
            return
        try:
            self.folder["obj"].delete()
        except BaseException as error:
            print("[GUI] folder close error: {}".format(error))
        self.folder = None
        if self.pager is not None:
            self.pager.set_enabled(True)
        print("[GUI] folder closed | {}".format(widgets.mem_str()))

    def back_out(self):
        """Walk back exactly one navigation level. Returns whether a layer was
        consumed.

        The levels, in order: the app (or its own handler) -> the folder flyout the
        app was entered from, or the desktop for a top-level app -> the desktop page
        the folder sits on -> page 0. Each press moves one level, so the user
        retraces the path they came in by instead of being dropped on the first page.

        Kept as one method rather than inlined in the key poll so the whole back-out
        ladder can be exercised without physical button events.
        """
        # An app may intercept first (gallery full screen -> grid, and so on).
        if self.ctx is not None:
            handler = self.ctx.back_handler
            if handler is not None:
                try:
                    if handler():
                        return True
                except BaseException as error:
                    print("[GUI] back handler error: {}".format(error))
            self.ctx.request_exit()
            return True

        # Desktop with a flyout open: close it and stay on this page.
        if self.folder is not None:
            self._close_folder()
            return True

        # Desktop with nothing open: step back one page. On page 0 there is nothing
        # left, which is what lets the last press end the back-out quietly.
        pager = self.pager
        if pager is not None and pager.page > 0:
            print("[GUI] CANCEL: page {} -> 0".format(pager.page))
            self.home_page = 0
            # go() overwrites an in-flight snap safely, so this also works right
            # after a swipe.
            pager.go(0)
            return True
        return False

    # ---------- App lifecycle ----------
    def open_app(self, meta):
        t0 = time.ticks_ms()
        print("[GUI] open '{}' begin | {}".format(meta["id"], widgets.mem_str()))
        immersive = bool(meta.get("immersive"))
        ctx = AppContext()
        ctx.touch = touchscreen.instance()
        if immersive:
            # Immersive: content area is the whole screen, no status bar, and the app
            # implements its own exit interaction
            ctx.width = DISPLAY_WIDTH
            ctx.height = DISPLAY_HEIGHT
            ctx.content_x = 0
            ctx.content_y = 0
            scr, content, home_btn = self._build_shell(
                tile_cache.tile_name(meta), show_clock=False, immersive=True)
        else:
            scr, content, home_btn = self._build_shell(
                tile_cache.tile_name(meta), show_clock=False)
            ctx.content_x = 0
            ctx.content_y = STATUS_H
            if home_btn is not None:
                beep.on_click(
                    home_btn,
                    lambda e: (print("[GUI] home clicked"), ctx.request_exit()))
        ctx.app_name = tile_cache.tile_name(meta)

        # release_home: reclaim home-screen memory before a heavy AI or
        # memory-sensitive app starts.
        # Switch to this app's screen first (home_scr must no longer be active before
        # it can be deleted safely), then destroy the home screen and release the
        # tile bitmap cache, freeing a contiguous heap for the kmodel load and
        # inference.
        if meta.get("release_home"):
            lv.scr_load(scr)
            self._release_home_memory()

        app = None
        startup_error = None
        try:
            module = __import__(meta["module"], None, None, ["create_app"])
            app = module.create_app(content, ctx)
        except AppUnavailable as error:
            # Hardware missing (camera not plugged in, for example): show a friendly
            # notice panel instead of exposing the underlying exception.
            print("[GUI] open '{}' unavailable: {}".format(meta["id"], error.message))
            startup_error = error
            gc.collect()
        except BaseException as error:
            print("[GUI] open {} failed: {}".format(meta["id"], error))
            sys.print_exception(error)
            # Truncate: the panel height is fixed at 320px and a long exception
            # string would cover the Cancel button.
            startup_error = AppUnavailable(
                "This app could not start.\n\n{}".format(error)[:160])

        self.active_app = app
        self.active_meta = meta
        self.ctx = ctx
        self.app_scr = scr
        self.app_content = content
        # Discard the down/up left over from "tap the icon to enter the app",
        # otherwise the app's tap detection may fire a spurious exit
        touch = touchscreen.instance()
        if touch is not None:
            touch.clear_raw()
        lv.scr_load(scr)
        if startup_error is not None:
            # The panel must be created after scr became the active screen (it is
            # attached to scr and deleted together with close_app).
            self._show_app_unavailable(scr, ctx, meta, startup_error, content)
        print("[GUI] open '{}' done in {}ms | {}".format(
            meta["id"], time.ticks_diff(time.ticks_ms(), t0), widgets.mem_str()))

    def _show_app_unavailable(self, scr, ctx, meta, error, content=None):
        """Notice panel for an app that cannot start (missing hardware or a failed
        constructor).

        Key point: the panel does not depend on active_app existing. When
        construction fails active_app is None, so the main loop's ctx.want_exit check
        must sit outside the app branch; otherwise CANCEL only sets a flag nobody
        ever handles and the user is stuck on the error screen (hit on board).
        """
        # An app that failed to construct may already have registered a CANCEL
        # interceptor (Scan sets back_handler before opening its sensor, for one). It
        # must be cleared here, or CANCEL would be consumed by a half-initialised
        # object and never get the user out.
        ctx.back_handler = None

        # A failed app may already have built part of its widgets (status text, an
        # empty viewfinder). Those leftovers show through the 60% backdrop (a
        # "camera init..." line at the top, for instance), so hide the whole content
        # layer.
        if content is not None:
            try:
                content.add_flag(lv.obj.FLAG.HIDDEN)
            except BaseException:
                pass

        # Backdrop: full-screen translucent black; tapping empty space also exits.
        backdrop = lv.obj(scr)
        backdrop.set_size(DISPLAY_WIDTH, DISPLAY_HEIGHT)
        backdrop.set_pos(0, 0)
        backdrop.set_style_bg_color(lv.color_hex(0x000000), 0)
        backdrop.set_style_bg_opa(lv.OPA._60, 0)
        backdrop.set_style_border_width(0, 0)
        backdrop.set_style_radius(0, 0)
        backdrop.set_style_pad_all(0, 0)
        backdrop.clear_flag(lv.obj.FLAG.SCROLLABLE)
        backdrop.add_flag(lv.obj.FLAG.CLICKABLE)
        beep.on_click(backdrop, lambda e: ctx.request_exit())

        # Panel: same visual language as the folder flyout (dark fill + border +
        # large radius).
        panel = lv.obj(backdrop)
        panel.set_size(600, 320)
        panel.align(lv.ALIGN.CENTER, 0, 0)
        panel.set_style_bg_color(lv.color_hex(COL_BAR), 0)
        panel.set_style_bg_opa(lv.OPA.COVER, 0)
        panel.set_style_border_width(1, 0)
        panel.set_style_border_color(lv.color_hex(TILE_BORDER), 0)
        panel.set_style_radius(20, 0)
        panel.set_style_pad_all(0, 0)
        panel.clear_flag(lv.obj.FLAG.SCROLLABLE)
        panel.add_flag(lv.obj.FLAG.CLICKABLE)

        title = lv.label(panel)
        title.set_text("{} unavailable".format(tile_cache.tile_name(meta)))
        title.set_style_text_color(lv.color_hex(COL_TEXT), 0)
        tf = widgets.font("font_montserrat_24", "font_montserrat_20",
                          "font_montserrat_16")
        if tf:
            title.set_style_text_font(tf, 0)
        title.align(lv.ALIGN.TOP_MID, 0, 30)

        msg = lv.label(panel)
        msg.set_text(error.message)
        msg.set_style_text_color(lv.color_hex(COL_MUTED), 0)
        mf = widgets.font("font_montserrat_20", "font_montserrat_16")
        if mf:
            msg.set_style_text_font(mf, 0)
        msg.set_style_text_align(lv.TEXT_ALIGN.CENTER, 0)
        # set_style_max_width requires a selector (otherwise TypeError); excessively
        # wide text wraps automatically.
        try:
            msg.set_style_max_width(520, 0)
        except BaseException:
            msg.set_width(520)
        msg.align(lv.ALIGN.TOP_MID, 0, 100)

        # Cancel: the app-wide neutral button style, no brand cyan, because bright
        # cyan (0x4CC9F0) is too loud on a dark panel and clashes with the
        # de-saturated palette used by the home screen and flyouts.
        # The size uses the keyboard's 28px bold bitmap font (font_load pulls it into
        # memory once and repaints are blits). TILE_FONT cannot be used here: it is
        # tiny_ttf, which measures 485-1298ms per frame when re-rasterised under
        # RENDER_MODE.FULL.
        btn = lv.btn(panel)
        btn.set_size(200, 60)
        btn.align(lv.ALIGN.BOTTOM_MID, 0, -36)
        widgets.clean(btn)              # clear the theme border/shadow first
        btn.set_style_radius(30, 0)
        btn.set_style_bg_color(lv.color_hex(TILE_BG), 0)
        btn.set_style_bg_opa(lv.OPA.COVER, 0)
        btn.set_style_border_width(1, 0)
        btn.set_style_border_color(lv.color_hex(TILE_BORDER), 0)
        # Pressed state: one level brighter fill plus a slight grow
        btn.set_style_bg_color(lv.color_hex(0x2A3340), lv.STATE.PRESSED)
        try:
            btn.set_style_transform_width(6, lv.STATE.PRESSED)
            btn.set_style_transform_height(6, lv.STATE.PRESSED)
        except BaseException:
            pass
        lab = lv.label(btn)
        lab.set_text("Cancel")
        lab.set_style_text_color(lv.color_hex(COL_TEXT), 0)
        bold = None
        try:
            from ui import kb_layer
            bold = kb_layer.load_kb_font()
        except BaseException as error:
            print("[GUI] kb font unavailable: {}".format(error))
        if bold is None:
            bold = widgets.font("font_montserrat_20", "font_montserrat_16")
        if bold:
            lab.set_style_text_font(bold, 0)
        lab.center()
        widgets.no_hit(lab)
        beep.on_click(btn, lambda e: ctx.request_exit())
        print("[GUI] unavailable panel shown for '{}'".format(meta["id"]))

    def _fail_running_app(self):
        """Hardware dropped out at runtime: release the app, hide its UI and reuse
        the "app unavailable" panel.

        The only difference from a failed construction is timing: the app has already
        built its UI and run for some frames, so destroy() must run first to release
        the sensor/kpu, then active_app is cleared (close_app skips destroy when it is
        None, so nothing is released twice), and finally the same panel lets the user
        Cancel back to the home screen.
        """
        app = self.active_app
        ctx = self.ctx
        meta = self.active_meta or {"id": "?", "name": "App"}
        message = ctx.unavailable_msg
        ctx.unavailable_msg = None
        ctx.back_handler = None
        ctx.ok_pressed = False
        self.active_app = None
        if app is not None:
            try:
                app.destroy()
            except BaseException as error:
                print("[GUI]   runtime destroy error: {}".format(error))
        gc.collect()
        print("[GUI] app '{}' went unavailable while running: {}".format(
            meta["id"], message))
        self._show_app_unavailable(self.app_scr, ctx, meta,
                                   AppUnavailable(message), self.app_content)

    def close_app(self):
        t0 = time.ticks_ms()
        app = self.active_app
        app_id = self.active_meta["id"] if self.active_meta else "?"
        self.active_app = None
        self.active_meta = None
        self.ctx = None
        self.app_content = None
        # Note: do not clear self.dots -- home_scr is kept resident, dots belong to
        # the home indicators, and clearing them breaks the indicator update after
        # returning home and entering an app again (home is built only once).
        # The down/up of the exit gesture must not leak into the next app (or into
        # the home screen's tap detection)
        touch = touchscreen.instance()
        if touch is not None:
            touch.clear_raw()
        # A top-level tile clears the breadcrumb; only a folder member keeps one.
        returning_to = self.return_folder
        self.return_folder = None
        print("[GUI] close '{}' begin | {}".format(app_id, widgets.mem_str()))
        if app is not None:
            try:
                app.destroy()
                print("[GUI]   app.destroy() ok")
            except BaseException as error:
                print("[GUI]   app.destroy() error: {}".format(error))
        # Switch back to the home screen before destroying the app screen: the active
        # screen must never be deleted, or LVGL's active screen dangles and touch
        # hit-testing stops working entirely (nothing responds).
        self.build_home()
        print("[GUI]   home restored | {}".format(widgets.mem_str()))
        if self.app_scr is not None:
            try:
                self.app_scr.delete()
                print("[GUI]   app screen deleted | {}".format(widgets.mem_str()))
            except BaseException as error:
                print("[GUI]   app screen delete error: {}".format(error))
            self.app_scr = None
        gc.collect()
        # Layered back-out, second step: the app was entered from a folder flyout, so
        # reopen that flyout on the home screen we just restored. The page is already
        # correct because build_home() restores self.home_page.
        if returning_to is not None:
            print("[GUI]   returning to folder '{}'".format(returning_to["id"]))
            self._open_folder(returning_to)
        print("[GUI] close '{}' done in {}ms | after gc: {}".format(
            app_id, time.ticks_diff(time.ticks_ms(), t0), widgets.mem_str()))

    # ---------- Main loop ----------
    def run(self):
        self.build_home()
        # home is now the active screen, so only at this point is deleting the splash
        # safe (never delete the screen that is being displayed)
        if self.splash is not None:
            try:
                self.splash.delete()
            except BaseException:
                pass
            self.splash = None
        buttons = HardwareButtons()
        touch = touchscreen.instance()
        last_clock_update = time.ticks_ms()
        # Read once: these are module globals set during bring-up, and the loop must
        # not pay two module lookups per frame for a value that never changes.
        refr_now = display_init.REFR_NOW
        disp_drv = display_init.disp_drv
        counters = perf.PERF
        # The network decision already happened in boot_load(): it is either
        # connected, not configured, or gave up after the boot window. Nothing here
        # starts a connect, so the desktop never inherits a held GIL from a wlan C
        # call.
        while True:
            os.exitpoint()
            # Click-sound shutdown: duty is zeroed when its time is up (an integer
            # comparison when idle)
            beep.poll()
            buttons.poll(self)
            if touch is not None:
                touch.poll()
            t0 = time.ticks_ms()
            next_wake = lv.task_handler()
            dt = time.ticks_diff(time.ticks_ms(), t0)
            counters["task_sum"] += dt
            counters["task_n"] += 1
            counters["loop_n"] += 1
            if dt > counters["task_max"]:
                counters["task_max"] = dt
            app = self.active_app
            if app is not None:
                try:
                    app.step()
                except BaseException as error:
                    print("[GUI] app step error: {}".format(error))
                    sys.print_exception(error)
                    self.ctx.request_exit()
            # Hardware dropping out at runtime (camera unplugged mid-session, say):
            # the app only sets a flag and this is the single place that cleans up.
            # Ordered after app.step() and before want_exit: with the panel showing,
            # want_exit is still False, and only Cancel or the CANCEL key returns
            # home.
            if self.ctx is not None and self.ctx.unavailable_msg is not None:
                self._fail_running_app()
            # The want_exit check must sit outside the app branch: when an app failed
            # to construct (AppUnavailable raised because the camera is missing, for
            # example) active_app is None and the screen holds only the notice panel,
            # so CANCEL only sets ctx.want_exit. Nested inside the if above, nobody
            # would ever handle it and the user would be stuck on the error panel.
            if self.ctx is not None and self.ctx.want_exit:
                self.close_app()
            elif app is None and self.pager is not None:
                # Home paging snap (poll driven; while an app runs it manages its own
                # pager)
                self.pager.poll()
            # Network service is resident: the main loop drives its connection state
            # machine and NTP scheduling (inside and outside apps).
            # poll only compares ticks and advances state, which is negligible;
            # blocking work lives in net_mgr's own _thread and never passes through
            # here.
            try:
                net_mgr.poll()
            except BaseException as error:
                print("[GUI] net poll error: {}".format(error))
            now = time.ticks_ms()
            if time.ticks_diff(now, last_clock_update) >= 1000:
                if self.status is not None:
                    self.status.tick()
                last_clock_update = now
            # Frame boost: while active (touching / just released) force an immediate
            # repaint every frame to break LVGL's LV_DEF_REFR_PERIOD(30ms) ceiling.
            # Frames with no dirty area return quickly from refr_now.
            active = touch is not None and touch.active()
            if active and perf.FRAME_BOOST and refr_now is not None:
                try:
                    refr_now(disp_drv)
                except BaseException:
                    pass
            # Sleep until the next animation frame LVGL suggests (UINT32_MAX means
            # idle), but touch/key polling needs a short period, so idle is capped
            # at 5ms and animation follows the returned value (>=1ms, including
            # scroll snap animations).
            # Only drop to 1ms while an Up is awaiting the holdoff decision: that
            # granularity directly sets the post-release snap gap.
            if touch is not None and touch.busy():
                time.sleep_ms(1)
            elif active:
                # Active (dragging / snap animation): no sleep, refr_now pushes the
                # frame rate to the hardware ceiling.
                # This must be 0ms rather than 1ms, because 1ms actually costs more
                # on MicroPython.
                pass
            elif next_wake >= 0xFFFFFFFF or next_wake < 0:
                time.sleep_ms(5)
            else:
                time.sleep_ms(max(1, min(next_wake, 30)))
