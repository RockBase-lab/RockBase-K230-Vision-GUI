# Gallery: 2x2 thumbnail grid + full-screen viewer, reads /data/photos (written by Camera).
#
# Interaction
#   grid : swipe left/right to page (4 thumbnails per page); tap -> full screen
#   full : swipe left/right or OK key -> previous/next; tap -> back to grid
#   CANCEL (GPIO0) -> exit app (handled centrally by the launcher)
#
# Paging and scrolling both go through the paging module (same thresholds/style/snap
# logic as the desktop launcher); the measurement notes sit at the top of
# factory/gui/paging.py.
#
# Why thumbnails must be cached on disk (measured on board, 4 x 1920x1080 -> 385x180):
#     re-decode + crop and scale every time = 898 ms (decode only 23 ms, scale 172 ms each)
#     read the cached RGB888 raw pixels    = 56 ms (4 x 203 KB, read in chunks)
# Scaling is the absolute bottleneck, so caching JPEG is useless; what has to be cached is
# the already-scaled pixel data.
# Each of those 195 ms blocks would stall the scroll_to_x animation, which shows up as
# "a short pause after release before the page finally changes".
#
# Image display: do **not** use image.as_lvgl_img_src; build lv.img_dsc_t explicitly
# instead. Reason (measured): when the cached 3 bpp RGB888 pixels are handed to LVGL
# through as_lvgl_img_src, this firmware occasionally shifts the hue of the whole image
# (yellow turns blue) and the next attempt looks normal again - the color-format handling
# inside that function is unreliable. An explicit dsc sets cf=lv.COLOR_FORMAT.RGB888
# ourselves, taking exactly the same path as the desktop tiles in launcher.py (those tiles never
# shifted color).
# See the notes on paging.RawImageCache.get() and tools/_probe_dsc.py.
#
# Reference lifetime: dsc.data points straight at the bytearray we own (zero copy,
# measured virtaddr == addressof). The widget must call set_src(None) before it is
# unbound, and only then may the cache drop that key; otherwise the widget points at freed
# memory (garbled output).

import gc
import os
import sys
import time

import image
import lvgl as lv

from ui import paging
from ui import beep

# Photo directory: must match SAVE_DIR in app_camera.py.
# Keep it on /data (6.4 GB data partition), not /sdcard (500 MB, GUI assets only).
PHOTO_DIR = "/data/photos"
THUMB_CACHE_DIR = "/data/cache/thumbs"
FULL_CACHE_DIR = "/data/cache/full"
JPEG_EXT = ".jpg"

GRID_COLS = 2
GRID_ROWS = 2
PER_PAGE = GRID_COLS * GRID_ROWS

TITLE_H = 34              # height of the top title bar
CELL_MARGIN = 10          # grid outer margin
# Thumbnail width **must be a multiple of 4**. At 32 bpp LVGL renders RGB888 images in
# batches of 4 pixels, and the leftover pixels past the last full batch get blended with
# the background color - visible as the **rightmost column being too bright** (about +8 in
# luminance, i.e. a 1 px light vertical line on dark photos).
# On-board A/B: width 385 gave 123 in the last column vs 115 in the next one (+8);
# width 384 gave 115 vs 114 (+1).
# 800 = 2*384 + 12(gap) + 2*10(margin), so the spare width goes to the gap instead of
# keeping 385.
CELL_W = 384
CELL_GAP = 12             # cell spacing (fixed by the divisibility rule above)
CELL_H = 180              # (424 - 34 - 2*10 - 10) / 2

FULL_W = 800              # full-screen image size (fills the whole content); 800 % 4 == 0
FULL_H = 424

MAX_THUMBS = 16           # max thumbnail Image objects alive at once (LRU eviction)
# Full-screen memory window: +/-2 = at most 5 images (800x424x3 = 1 MB each).
# 2 rather than 1 so the image the next page swipe needs is already prefetched, making a
# page swipe decode-free.
FULL_WINDOW = 2

GALLERY_DIAG = False


def _list_photos():
    """Return the full paths of all photos, sorted by file name."""
    try:
        names = os.listdir(PHOTO_DIR)
    except OSError:
        return []
    names = [n for n in names if n.lower().endswith(JPEG_EXT)]
    names.sort()
    return [PHOTO_DIR + "/" + n for n in names]


def _roi_and_scale(src_w, src_h, dst_w, dst_h):
    """Compute the ROI and scale factor for a centred crop-to-fill to the target aspect
    ratio.

    After a uniform scale the output size is dictated by the source aspect ratio:
    1920x1080 -> 385x180 yields ~343x180 with 21 px empty on each side. Cropping the extra
    edges to the target aspect ratio first and then scaling up to the target size fills it.
    """
    if src_w * dst_h > src_h * dst_w:
        roi_h = src_h
        roi_w = int(round(src_h * dst_w / float(dst_h)))
        roi_x = (src_w - roi_w) // 2
        roi_y = 0
    else:
        roi_w = src_w
        roi_h = int(round(src_w * dst_h / float(dst_w)))
        roi_x = 0
        roi_y = (src_h - roi_h) // 2
    roi_w = max(1, min(roi_w, src_w))
    roi_h = max(1, min(roi_h, src_h))
    roi_x = max(0, min(roi_x, src_w - roi_w))
    roi_y = max(0, min(roi_y, src_h - roi_h))
    scale = max(dst_w / float(roi_w), dst_h / float(roi_h))
    return (roi_x, roi_y, roi_w, roi_h), scale


def _produce(path, dst_w, dst_h):
    """Decode and crop-to-fill to the target size, returning an RGB888 Image (None on
    failure).

    Note: the returned Image's pixels live in the **media pool** and any later decode
    reuses that buffer. Callers must copy the pixels out first (paging.RawImageCache
    does), never hold the Image for long.
    """
    try:
        img = image.Image(path)
        roi, scale = _roi_and_scale(img.width(), img.height(), dst_w, dst_h)
        out = img.copy(roi=roi, x_scale=scale, y_scale=scale)
        if out.format() != image.RGB888:
            out = out.to_rgb888()
        return out
    except BaseException as error:
        print("[Gallery] produce '{}' failed: {}".format(path, error))
        sys.print_exception(error)
        return None


class GalleryApp:
    def __init__(self, content, ctx):
        self.ctx = ctx
        self.content = content
        self.w = ctx.width
        self.h = ctx.height

        self.photos = _list_photos()
        self.pages = max(1, (len(self.photos) + PER_PAGE - 1) // PER_PAGE)
        self.mode = "grid"

        # Thumbnail cache: raw pixels on disk + an in-memory LRU.
        # The size is part of the file name, so changing the size switches files
        # automatically and stale content is never read back.
        # max_buffers is only an alarm line: the thumbnail window is 3 pages x 4 = 12,
        # the full-screen window is +/-2 = 5. Going past it means some key was never
        # dropped (a precursor to a memory leak).
        self.thumb_cache = paging.RawImageCache(THUMB_CACHE_DIR, CELL_W, CELL_H,
                                               bpp=3, fmt=image.RGB888,
                                               max_buffers=MAX_THUMBS)
        self.full_cache = paging.RawImageCache(FULL_CACHE_DIR, FULL_W, FULL_H,
                                               bpp=3, fmt=image.RGB888,
                                               max_buffers=2 * FULL_WINDOW + 1)
        self._thumb_mem = {}       # idx -> Image
        self._thumb_order = []
        self._thumb_binding = {}   # idx -> currently bound img widget
        self._thumb_queue = []     # thumbnail indices to generate (loaded across frames)

        # Full-screen images: large pixel count (800x424x3 = 1 MB), kept in a window and
        # prefetched across frames
        self._full_mem = {}
        self._full_queue = []

        self.grid_page = 0
        self.full_idx = 0

        # ---- Top title ----
        self.title = lv.label(content)
        self.title.set_text("")
        self.title.set_style_text_color(lv.color_hex(0xD8DADE), 0)
        self.title.align(lv.ALIGN.TOP_MID, 0, 8)

        # ---- Empty gallery hint ----
        self.empty = lv.label(content)
        self.empty.set_text("No photos\n\n" + PHOTO_DIR)
        self.empty.set_style_text_color(lv.color_hex(0x9CA3AF), 0)
        self.empty.set_style_text_align(lv.TEXT_ALIGN.CENTER, 0)
        self.empty.align(lv.ALIGN.CENTER, 0, 0)
        self.empty.add_flag(lv.obj.FLAG.HIDDEN)

        # ---- Full-screen file name (bottom centre) ----
        # At the bottom rather than the top: the top is taken by the page counter; the
        # bottom does not fight the image itself, and the dark translucent background keeps
        # it readable on both bright and dark photos.
        self.filename = lv.label(content)
        self.filename.set_text("")
        self.filename.set_style_text_color(lv.color_hex(0xE5E7EB), 0)
        self.filename.set_style_bg_color(lv.color_hex(0x000000), 0)
        self.filename.set_style_bg_opa(lv.OPA._60, 0)
        self.filename.set_style_radius(8, 0)
        self.filename.set_style_pad_all(8, 0)
        self.filename.set_style_text_align(lv.TEXT_ALIGN.CENTER, 0)
        self.filename.align(lv.ALIGN.BOTTOM_MID, 0, -14)
        self.filename.add_flag(lv.obj.FLAG.HIDDEN)

        # ---- Grid paging container ----
        self.grid_area_h = self.h - TITLE_H
        self.grid_wrap = lv.obj(content)
        self.grid_wrap.set_size(self.w, self.grid_area_h)
        self.grid_wrap.align(lv.ALIGN.TOP_MID, 0, TITLE_H)
        self.grid_wrap.set_style_bg_opa(lv.OPA.TRANSP, 0)
        self.grid_wrap.set_style_border_width(0, 0)
        self.grid_wrap.set_style_pad_all(0, 0)
        self.grid_wrap.set_flex_flow(lv.FLEX_FLOW.ROW)
        paging.setup_horiz_scroller(self.grid_wrap, self.w)
        if self.pages <= 1:
            # A single page must disable scrolling, otherwise touch jitter is taken as a
            # scroll and CLICKED is cancelled
            self.grid_wrap.clear_flag(lv.obj.FLAG.SCROLLABLE)

        # One container per page (built once; paging never rebuilds the object tree)
        self._cells = []           # 4 cells per page: {"box","img","index"}
        for p in range(self.pages):
            page = lv.obj(self.grid_wrap)
            page.set_size(self.w, self.grid_area_h)
            page.set_style_bg_opa(lv.OPA.TRANSP, 0)
            page.set_style_border_width(0, 0)
            page.set_style_pad_all(0, 0)
            page.clear_flag(lv.obj.FLAG.SCROLLABLE)
            cells = []
            for i in range(PER_PAGE):
                col = i % GRID_COLS
                row = i // GRID_COLS
                cx = CELL_MARGIN + col * (CELL_W + CELL_GAP)
                cy = CELL_MARGIN + row * (CELL_H + CELL_GAP)
                box = lv.obj(page)
                box.set_size(CELL_W, CELL_H)
                box.set_pos(cx, cy)
                box.set_style_bg_color(lv.color_hex(0x23262A), 0)
                box.set_style_bg_opa(lv.OPA.COVER, 0)
                box.set_style_radius(10, 0)
                box.set_style_border_width(0, 0)
                box.set_style_pad_all(0, 0)
                box.clear_flag(lv.obj.FLAG.SCROLLABLE)
                # The image fills the whole cell and its square corners break the rounded
                # ones -> let children be clipped by the parent's radius
                try:
                    box.set_style_clip_corner(True, 0)
                except Exception:
                    pass
                img = lv.img(box)
                img.align(lv.ALIGN.CENTER, 0, 0)
                img.clear_flag(lv.obj.FLAG.SCROLLABLE)
                img.clear_flag(lv.obj.FLAG.CLICKABLE)   # clicks land on the box
                idx = p * PER_PAGE + i
                cells.append({"box": box, "img": img, "index": idx})
                if idx < len(self.photos):
                    beep.on_click(box, self._make_open_cb(idx))
                else:
                    box.add_flag(lv.obj.FLAG.HIDDEN)
            self._cells.append(cells)

        self.grid_pager = paging.SnapPager(
            self.grid_wrap, self.w, self.pages,
            on_page_changed=self._on_grid_page,
            is_pressed=self._is_pressed)

        # ---- Full-screen paging container ----
        self.full_wrap = lv.obj(content)
        self.full_wrap.set_size(self.w, self.h)
        self.full_wrap.align(lv.ALIGN.TOP_MID, 0, 0)
        self.full_wrap.set_style_bg_opa(lv.OPA.TRANSP, 0)
        self.full_wrap.set_style_border_width(0, 0)
        self.full_wrap.set_style_pad_all(0, 0)
        self.full_wrap.set_flex_flow(lv.FLEX_FLOW.ROW)
        paging.setup_horiz_scroller(self.full_wrap, self.w)
        if len(self.photos) <= 1:
            self.full_wrap.clear_flag(lv.obj.FLAG.SCROLLABLE)

        self._full_pages = []
        for i in range(len(self.photos)):
            page = lv.obj(self.full_wrap)
            page.set_size(self.w, self.h)
            page.set_style_bg_color(lv.color_hex(0x000000), 0)
            page.set_style_bg_opa(lv.OPA.COVER, 0)
            page.set_style_border_width(0, 0)
            page.set_style_pad_all(0, 0)
            page.clear_flag(lv.obj.FLAG.SCROLLABLE)
            img = lv.img(page)
            img.align(lv.ALIGN.CENTER, 0, 0)
            img.clear_flag(lv.obj.FLAG.CLICKABLE)
            beep.on_click(page, self._make_back_cb())
            self._full_pages.append({"page": page, "img": img, "loaded": False})
        self.full_wrap.add_flag(lv.obj.FLAG.HIDDEN)

        self.full_pager = paging.SnapPager(
            self.full_wrap, self.w, max(1, len(self.photos)),
            on_page_changed=self._on_full_page,
            is_pressed=self._is_pressed)

        print("[Gallery] {} photos, {} pages | cell {}x{} full {}x{} | cache {}".format(
            len(self.photos), self.pages, CELL_W, CELL_H, FULL_W, FULL_H,
            THUMB_CACHE_DIR))

        # CANCEL goes back layer by layer: full screen -> grid -> desktop
        self.ctx.set_back_handler(self._on_back)

        if not self.photos:
            self.empty.clear_flag(lv.obj.FLAG.HIDDEN)
            self.grid_wrap.add_flag(lv.obj.FLAG.HIDDEN)
        else:
            self._ensure_thumbs(self.grid_pager.page)
            self._update_title()

    # ---------- Helpers ----------
    def _is_pressed(self):
        """Whether a finger is (still considered) on the screen. Snap settling uses this to
        tell "dragged halfway" apart from "released"."""
        touch = getattr(self.ctx, "touch", None)
        if touch is None:
            return False
        try:
            return bool(touch.pressed)
        except BaseException:
            return False

    def _make_open_cb(self, idx):
        def _cb(event, i=idx):
            self._open_full(i)
        return _cb

    def _make_back_cb(self):
        def _cb(event):
            self._close_full()
        return _cb

    # ---------- Thumbnails (disk cache + memory LRU + safe unbind) ----------
    def _unbind_thumb(self, idx):
        """Clear the widget's src first, then allow the Image to be freed (otherwise the
        widget holds a dangling pointer)."""
        widget = self._thumb_binding.pop(idx, None)
        if widget is not None:
            try:
                widget.set_src(None)
            except BaseException:
                pass
            try:
                widget.add_flag(lv.obj.FLAG.HIDDEN)
            except BaseException:
                pass
        if self._thumb_mem.pop(idx, None) is not None and idx < len(self.photos):
            # The Image is no longer referenced by a widget, so its buffer can go
            self.thumb_cache.drop(self.photos[idx])

    def _bind_thumb(self, idx, widget):
        dsc = self._thumb_mem.get(idx)
        if dsc is None:
            return
        self._thumb_binding[idx] = widget
        try:
            widget.set_src(dsc)
            widget.set_size(CELL_W, CELL_H)
            widget.align(lv.ALIGN.CENTER, 0, 0)
            widget.clear_flag(lv.obj.FLAG.HIDDEN)
        except BaseException as error:
            print("[Gallery] thumb bind failed: {}".format(error))

    def _ensure_thumbs(self, page):
        """Plan thumbnails for the three pages [page-1, page+1]: bind what can be bound now
        and queue the rest for loading across frames.

        Never decode synchronously before returning: this function is called from
        SnapPager's on_page_changed while the snap animation has just started (see the
        ordering note in paging.SnapPager.go), and a synchronous decode would stall that
        animation - which is exactly the "a few hundred ms pause after release" the user
        perceives. Decoding is spread over step(), at most one image per frame.
        """
        if not self.photos:
            return
        lo = max(0, page - 1)
        hi = min(self.pages - 1, page + 1)
        want = set()
        for p in range(lo, hi + 1):
            for idx in range(p * PER_PAGE,
                             min((p + 1) * PER_PAGE, len(self.photos))):
                want.add(idx)

        for idx in list(self._thumb_mem.keys()):
            if idx not in want:
                self._unbind_thumb(idx)
        self._thumb_order = [i for i in self._thumb_order if i in want]

        # Bind what is already cached first and collect the misses as we go.
        # Priority: current page > next page > previous page.
        queue = []
        order = [page]
        if page + 1 <= hi:
            order.append(page + 1)
        if lo <= page - 1:
            order.append(page - 1)
        for p in order:
            for cell in self._cells[p]:
                idx = cell["index"]
                if idx >= len(self.photos):
                    continue
                if idx in self._thumb_mem:
                    self._bind_thumb(idx, cell["img"])
                else:
                    queue.append(idx)
        self._thumb_queue = queue

        # LRU cap: evict the least recently used entry that is not currently bound
        while len(self._thumb_order) > MAX_THUMBS:
            old = self._thumb_order.pop(0)
            if old in self._thumb_binding:
                self._thumb_order.append(old)
                break
            self._thumb_mem.pop(old, None)

    def _pump_thumbs(self, budget=1):
        """Generate missing thumbnails across frames: at most `budget` per call."""
        if not self.photos or not self._thumb_queue:
            return
        done = 0
        while self._thumb_queue and done < budget:
            idx = self._thumb_queue.pop(0)
            if idx in self._thumb_mem:
                continue
            t0 = time.ticks_ms()
            dsc = self.thumb_cache.get(
                self.photos[idx],
                lambda path: _produce(path, CELL_W, CELL_H))
            if dsc is not None:
                self._thumb_mem[idx] = dsc
                self._thumb_order.append(idx)
                cell = self._cell_for(idx)
                if cell is not None:
                    self._bind_thumb(idx, cell["img"])
            done += 1
            if GALLERY_DIAG:
                print("[Gallery] thumb {} took {}ms (hit={} miss={})".format(
                    idx, time.ticks_diff(time.ticks_ms(), t0),
                    self.thumb_cache.hits, self.thumb_cache.misses))

    def _cell_for(self, idx):
        p = idx // PER_PAGE
        if 0 <= p < len(self._cells):
            i = idx % PER_PAGE
            if i < len(self._cells[p]):
                return self._cells[p][i]
        return None

    # ---------- Grid ----------
    def _on_grid_page(self, page):
        self.grid_page = page
        self._ensure_thumbs(page)
        self._update_title()

    def _update_title(self):
        if not self.photos:
            self.title.set_text("")
            return
        if self.mode == "full":
            self.title.set_text("{}/{}".format(self.full_idx + 1, len(self.photos)))
            self._update_filename()
            return
        start = self.grid_page * PER_PAGE
        last = min(start + PER_PAGE, len(self.photos))
        if self.pages > 1:
            self.title.set_text("{}-{} / {}   ({}/{})".format(
                start + 1, last, len(self.photos),
                self.grid_page + 1, self.pages))
        else:
            self.title.set_text("{} photos".format(len(self.photos)))

    def _update_filename(self):
        """Show the current photo's file name at the bottom in full-screen mode (with its
        size, so the file can be located)."""
        if self.mode != "full" or not self.photos:
            self.filename.add_flag(lv.obj.FLAG.HIDDEN)
            return
        if not (0 <= self.full_idx < len(self.photos)):
            self.filename.add_flag(lv.obj.FLAG.HIDDEN)
            return
        path = self.photos[self.full_idx]
        name = path.rsplit("/", 1)[-1]
        try:
            kb = paging.file_size(path) // 1024
            text = "{}   ({}KB)".format(name, kb)
        except BaseException:
            text = name
        self.filename.set_text(text)
        # Cap the width for long paths (longer text wraps automatically), so it never runs
        # off the screen
        try:
            self.filename.set_style_max_width(self.w - 40, 0)
        except BaseException:
            pass
        self.filename.clear_flag(lv.obj.FLAG.HIDDEN)
        try:
            self.filename.move_foreground()
        except BaseException:
            pass

    # ---------- Full screen ----------
    def _open_full(self, idx):
        if not self.photos:
            return
        self.mode = "full"
        self.full_idx = idx
        self.grid_wrap.add_flag(lv.obj.FLAG.HIDDEN)
        self.full_wrap.clear_flag(lv.obj.FLAG.HIDDEN)
        # The title sits on top of the full-screen image, so give it a translucent
        # background to stay readable
        try:
            self.title.move_foreground()
            self.title.set_style_bg_color(lv.color_hex(0x000000), 0)
            self.title.set_style_bg_opa(lv.OPA._50, 0)
            self.title.set_style_radius(10, 0)
            self.title.set_style_pad_all(6, 0)
        except Exception:
            pass
        self.full_pager.page = idx
        self.full_pager.go(idx, animate=False)
        self._ensure_full(idx)
        # Load the current image synchronously: entering full screen is not a "page swipe",
        # so the cost of one image is acceptable; neighbouring pages are left to step() to
        # prefetch across frames, keeping later swipes decode-free.
        self._load_full_now(idx)
        self._update_title()
        print("[Gallery] open full {}".format(idx))

    def _load_full_now(self, j):
        """Load a single full-screen image synchronously (only for the target image when
        entering full screen)."""
        if j in self._full_mem or j >= len(self.photos):
            return
        try:
            self._full_queue.remove(j)
        except ValueError:
            pass
        dsc = self.full_cache.get(
            self.photos[j], lambda path: _produce(path, FULL_W, FULL_H))
        if dsc is None:
            return
        self._full_mem[j] = dsc
        slot = self._full_pages[j]
        try:
            slot["img"].set_src(dsc)
            slot["img"].set_size(FULL_W, FULL_H)
            slot["img"].align(lv.ALIGN.CENTER, 0, 0)
            slot["loaded"] = True
        except BaseException as error:
            print("[Gallery] full bind failed: {}".format(error))

    def _close_full(self):
        self.mode = "grid"
        self.full_wrap.add_flag(lv.obj.FLAG.HIDDEN)
        self.grid_wrap.clear_flag(lv.obj.FLAG.HIDDEN)
        try:
            self.title.set_style_bg_opa(lv.OPA.TRANSP, 0)
            self.title.set_style_pad_all(0, 0)
        except Exception:
            pass
        self.filename.add_flag(lv.obj.FLAG.HIDDEN)
        self._release_full_window()
        page = self.full_idx // PER_PAGE
        self.grid_pager.go(page)
        self._update_title()

    def _on_full_page(self, idx):
        self.full_idx = idx
        if self.mode == "full":
            self._ensure_full(idx)
            self._update_title()

    def _ensure_full(self, idx):
        """Plan full-screen images for [idx-FULL_WINDOW, idx+FULL_WINDOW]: bind what can be
        bound now and queue the rest.

        Same reasoning as thumbnails: this function is called from on_page_changed (the
        snap animation has just started), so it must never decode synchronously before
        returning (one image takes 247 ms).
        The window is +/-2 rather than +/-1: a one-page swipe only needs the neighbour, and
        +/-2 guarantees the target image is already fully prefetched by then, so a page
        swipe triggers no decoding at all.
        """
        lo = max(0, idx - FULL_WINDOW)
        hi = min(len(self.photos) - 1, idx + FULL_WINDOW)
        want = set()
        for j in range(lo, hi + 1):
            want.add(j)

        for j in list(self._full_mem.keys()):
            if j in want:
                continue
            self._unbind_full(j)

        # Priority: current > next > previous > further away
        queue = []
        for d in (0, 1, -1, 2, -2):
            j = idx + d
            if not (lo <= j <= hi):
                continue
            if j in self._full_mem:
                continue
            queue.append(j)
        self._full_queue = queue

    def _pump_full(self, budget=1):
        """Generate missing full-screen images across frames: at most `budget` per call."""
        if not self._full_queue:
            return
        done = 0
        while self._full_queue and done < budget:
            j = self._full_queue.pop(0)
            if j in self._full_mem or j >= len(self.photos):
                continue
            t0 = time.ticks_ms()
            dsc = self.full_cache.get(
                self.photos[j], lambda path: _produce(path, FULL_W, FULL_H))
            if dsc is not None:
                self._full_mem[j] = dsc
                slot = self._full_pages[j]
                try:
                    slot["img"].set_src(dsc)
                    slot["img"].set_size(FULL_W, FULL_H)
                    slot["img"].align(lv.ALIGN.CENTER, 0, 0)
                    slot["loaded"] = True
                except BaseException as error:
                    print("[Gallery] full bind failed: {}".format(error))
            done += 1
            if GALLERY_DIAG:
                print("[Gallery] full {} took {}ms (hit={} miss={})".format(
                    j, time.ticks_diff(time.ticks_ms(), t0),
                    self.full_cache.hits, self.full_cache.misses))

    def _unbind_full(self, j):
        """Clear the widget's src first, then release the Image and its buffer."""
        if j >= len(self._full_pages):
            return
        slot = self._full_pages[j]
        try:
            slot["img"].set_src(None)
        except BaseException:
            pass
        slot["loaded"] = False
        if self._full_mem.pop(j, None) is not None and j < len(self.photos):
            self.full_cache.drop(self.photos[j])

    def _release_full_window(self):
        for j in list(self._full_mem.keys()):
            self._unbind_full(j)
        self._full_mem = {}
        self._full_queue = []
        gc.collect()

    # ---------- Layered back (CANCEL) ----------
    def _on_back(self):
        """CANCEL: full screen -> grid (consumed); grid -> return False so the launcher exits
        the app."""
        if self.mode == "full":
            self._close_full()
            return True
        return False

    # ---------- Main loop ----------
    def step(self):
        if not self.photos:
            return
        # Snap settling: poll-driven, does not rely on SCROLL_END (that event is unreliable
        # on this firmware)
        if self.mode == "full":
            self.full_pager.poll()
        else:
            self.grid_pager.poll()

        # Preloading across frames: only on idle frames ("no finger down + no animation
        # running"), so the visible swipe/snap never stalls. Budget: 1 image per frame.
        if not self._is_pressed():
            if self.mode == "full":
                if not self.full_pager.busy():
                    self._pump_full(1)
            else:
                if not self.grid_pager.busy():
                    self._pump_thumbs(1)

        # OK key: full screen advances to the next image; grid opens the first image of the
        # current page
        if self.ctx.take_ok():
            if self.mode == "full":
                self.full_pager.go((self.full_idx + 1) % len(self.photos))
            else:
                self._open_full(self.grid_page * PER_PAGE)

    def destroy(self):
        for idx in list(self._thumb_mem.keys()):
            self._unbind_thumb(idx)
        self._release_full_window()
        self._thumb_mem = {}
        self._thumb_order = []
        self.thumb_cache.release()
        self.full_cache.release()
        self._thumb_queue = []
        self._full_queue = []
        gc.collect()
        print("[Gallery] destroy (thumb hit={} miss={} | full hit={} miss={})".format(
            self.thumb_cache.hits, self.thumb_cache.misses,
            self.full_cache.hits, self.full_cache.misses))


def create_app(content, ctx):
    return GalleryApp(content, ctx)
