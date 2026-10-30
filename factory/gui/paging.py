# Shared paging/scroll module: reused by the desktop launcher and every app that
# pages horizontally.
#
# Why a separate module: the desktop (gui.py) and the gallery used to carry their
# own copies of the scroll-container setup and snap logic, with thresholds and
# styles kept in sync by comments, which drifted easily. They are collapsed into
# one copy here.
#
# This module **depends on lvgl only** and never imports gui.py (gui.py runs via
# exec(open().read()), so a reverse import would execute it again and create a
# second Launcher).
#
# Deployment note: MicroPython caches imported modules. After editing paging.py,
# re-exec'ing gui.py on the board may still use the old module -- a soft reset or a
# power cycle is needed.
#
# Measured parameters reusable on board (2026-10, RockBase/K230):
#   - with SCROLL_MOMENTUM disabled, releasing does **not** guarantee a SCROLL_END
#     event -> snapping must poll
#   - a flex container's pad_column defaults to 11px (DPI scaling) and pad_all(0)
#     does not clear it; left alone, every page accumulates an 11px offset so page i
#     starts i*11px early on the left (measured stride 811)
#   - f.read() of 200KB in one call takes 92ms, chunked f.readinto() of the same data
#     takes only 7ms

import time

import lvgl as lv


# A release only turns the page when the displacement exceeds this many pixels;
# otherwise it snaps back. **The desktop and every app must share one value**,
# otherwise the feel differs between screens (the desktop once used 120 and the
# gallery 400).
SWIPE_THRESHOLD_PX = 120

# Snap animation duration. LVGL's default is ~300ms with ease-in, which feels
# sluggish; 180ms is the value measured and tuned on the desktop (shorter looks
# like a hard cut, longer shows the slow start).
SNAP_ANIM_MS = 180


def setup_horiz_scroller(wrap, page_w=None):
    """Configure a container for horizontal paging: drags follow the finger,
    momentum off, elastic off, no scrollbar.

    Parameters:
        wrap    : an lv.obj already sized and set to set_flex_flow(FLEX_FLOW.ROW)
        page_w  : page width (unused for now; kept for future scroll-range checks)
    """
    try:
        wrap.set_scroll_dir(lv.DIR.HOR)
    except Exception:
        pass

    # pad_column / pad_row must be zeroed explicitly! LVGL's flex default is 11px
    # (DPI scaling), not 0. Clearing only pad_all leaves an 11px page gap, so even
    # after scrolling to i*page_w the page actually sits at i*(page_w+11) and page i
    # shows i*11px of the previous page on its left.
    for api in ("set_style_pad_column", "set_style_pad_row"):
        fn = getattr(wrap, api, None)
        if fn is not None:
            try:
                fn(0, 0)
            except Exception:
                pass

    # Momentum and elasticity off: momentum lets a fast flick overshoot the page
    # boundary and breaks the 120px threshold decision; elastic bounce fights our
    # snap animation for control.
    for name in ("SCROLL_MOMENTUM", "SCROLL_ELASTIC"):
        flag = getattr(lv.obj.FLAG, name, None)
        if flag is not None:
            try:
                wrap.clear_flag(flag)
            except Exception:
                pass

    # Scrollbar off (firmware bindings differ in attribute names)
    for cls in ("SCROLLBAR_MODE", "SCROLLBAR"):
        off = getattr(getattr(lv, cls, None), "OFF", None)
        if off is not None:
            try:
                wrap.set_scrollbar_mode(off)
                break
            except Exception:
                continue

    # Snap animation duration: LVGL v8 calls it anim_time, v9 anim_duration
    for api in ("set_style_anim_time", "set_style_anim_duration"):
        fn = getattr(wrap, api, None)
        if fn is None:
            continue
        try:
            fn(SNAP_ANIM_MS, 0)
            break
        except Exception:
            continue


class SnapPager:
    """Horizontal pager: native scrolling plus a snap to whole pages on release.

    Driven by poll() every main-loop frame; it does **not** listen for SCROLL_END --
    with SCROLL_MOMENTUM disabled this firmware does not guarantee that event on
    release, and a listener shows up as "stopped between two pages".

    The decision matches the logic the desktop already used:
        press   -> record start_page (~ SCROLL_BEGIN)
        release -> delta = x - start_page*page_w (~ SCROLL_END)
                   |delta| > SWIPE_THRESHOLD_PX turns a page, otherwise snap back

    Parameters:
        wrap            : container already set up by setup_horiz_scroller
        page_w          : page width (px)
        page_count      : page count (<=1 means never intervene)
        on_page_changed : called as soon as the target page is decided (not when the
                          animation ends), so data can be preloaded
        is_pressed      : callable returning "is a finger on the screen"; passing
                          TouchScreen.pressed is recommended (it includes the 45ms
                          holdoff, which is exactly what detects a real lift)
    """

    def __init__(self, wrap, page_w, page_count, on_page_changed=None,
                 is_pressed=None):
        self.wrap = wrap
        self.page_w = page_w
        self.page_count = page_count
        self.on_page_changed = on_page_changed
        self.is_pressed = is_pressed or (lambda: False)
        self.page = 0
        self._animating = False
        self._anim_x = None
        self._was_pressed = False
        self._start_page = 0

    # ---------- Internal ----------
    def _scroll_x(self):
        try:
            x = self.wrap.get_scroll_x()
        except Exception:
            return 0
        return x if x > 0 else 0

    # ---------- Public ----------
    def set_enabled(self, enabled):
        """Temporarily disable or restore scrolling (disabled while a desktop
        overlay is open: FT5316 jitter read as scrolling cancels the CLICKED of
        overlay widgets -- the same trap as single-page scroll locking). Disabling
        snaps back to the current page too.
        """
        if enabled:
            self.wrap.add_flag(lv.obj.FLAG.SCROLLABLE)
        else:
            self.wrap.clear_flag(lv.obj.FLAG.SCROLLABLE)
            self.reset_gesture()

    def set_page_count(self, count):
        """Update when the page count changes (for example after the gallery gains
        a photo)."""
        self.page_count = max(1, count)
        if self.page > self.page_count - 1:
            self.page = self.page_count - 1

    def reset_gesture(self):
        """Drop the current gesture tracking (called when jumping pages
        programmatically)."""
        self._was_pressed = False
        self._animating = False
        self._anim_x = None

    def poll(self):
        """Called every main-loop frame: track the drag and snap to a whole page on
        release."""
        if self.page_count <= 1:
            return
        pressed = bool(self.is_pressed())

        if self._animating:
            if pressed:
                # A finger landing takes over scrolling and LVGL stops the animation
                # itself
                self._animating = False
                self._anim_x = None
            elif self._anim_x is not None and \
                    abs(self._scroll_x() - self._anim_x) <= 2:
                self._animating = False
                self._anim_x = None
            return

        if pressed:
            if not self._was_pressed:
                x = self._scroll_x()
                page = int(round(x / float(self.page_w)))
                self._start_page = max(0, min(self.page_count - 1, page))
            self._was_pressed = True
            return                      # still dragging, do not fight for the scroll position

        if not self._was_pressed:
            return                      # idle

        # Just released: settle with the shared threshold
        self._was_pressed = False
        delta = self._scroll_x() - self._start_page * self.page_w
        if delta > SWIPE_THRESHOLD_PX:
            target = self._start_page + 1
        elif delta < -SWIPE_THRESHOLD_PX:
            target = self._start_page - 1
        else:
            target = self._start_page
        self.go(target)

    def go(self, target, animate=True):
        """Scroll to a page. animate=False is for programmatic positioning (e.g.
        opening a full-screen view).

        Order matters: **start the scroll animation first, then call
        on_page_changed**. Reversed, the costly work inside the callback (gallery
        decoding / cache reads, measured 56~900ms) blocks the animation from
        starting and shows up as "a pause after release before the page changes".
        Measured the hard way.
        """
        target = max(0, min(self.page_count - 1, target))
        prev_page = self.page
        target_x = target * self.page_w
        need_scroll = abs(self._scroll_x() - target_x) > 2

        if need_scroll:
            self._anim_x = target_x
            self._animating = True
            try:
                # LVGL clamps itself when the target exceeds the scroll range. Do
                # not clamp here: a failed range probe would pin the target to the
                # current position and the snap would never complete.
                self.wrap.scroll_to_x(target_x,
                                      lv.ANIM.ON if animate else lv.ANIM.OFF)
            except Exception:
                self._animating = False
                self._anim_x = None
        else:
            self._animating = False
            self._anim_x = None

        if not animate:
            self.reset_gesture()

        # The animation is running now, so send the possibly costly page-change
        # notification
        if target != prev_page:
            self.page = target
            if self.on_page_changed is not None:
                try:
                    self.on_page_changed(target)
                except BaseException as error:
                    print("[paging] on_page_changed error:", error)
        else:
            self.page = target

    def busy(self):
        """Whether a snap animation is running (apps can avoid costly work
        meanwhile)."""
        return self._animating


# ---------------------------------------------------------------------------
# Drag-following pager (moves the pages itself instead of using native scrolling)
# ---------------------------------------------------------------------------

# Release decision: turn the page once the displacement exceeds this fraction of
# the page width, otherwise bounce back.
# Why not fixed pixels: page width varies, while a ratio keeps the feel the same at
# any size. 0.28 is the intuitive "clearly pushed out" point (about 224px on an
# 800px screen).
DRAG_DECIDE_RATIO = 0.28
# Flick: a short but fast swipe should also turn the page, otherwise a quick flick
# feels dead.
DRAG_FLICK_PX_MS = 0.55
DRAG_FLICK_MIN_PX = 40         # a flick needs at least this displacement (filters out tap jitter)
DRAG_VEL_CLAMP = 4.0           # velocity estimate clamp (px/ms)


class DragPager:
    """Drag-following horizontal pager with release decision and wrap-around.

    Difference from SnapPager: SnapPager uses LVGL's native scrolling, which can
    only drag within the scroll range and is clamped at both ends, so it cannot do
    two things:
      1) leave the page halfway while a finger holds it -- native scrolling can
         actually do that too, but the "decide on release" rule has to be computed
         here anyway;
      2) **wrap-around paging**: the native range is finite, so the first and last
         page cannot be dragged past.
    This class moves the page containers itself (set_x), so both work.

    Pages must be siblings under the same parent, sized page_w x page_h, and must
    **not** live in a flex container (flex overrides x).

    Usage (every main-loop frame):
        pager.poll(now_ms, pressed, x, y)
    pressed/x come from the touch layer; IMU uses its own parsed raw point stream
    (with a silence timeout that filters the FT5316's fake Up mid-drag), audio uses
    TouchScreen.pressed + pos().

    blocked(x, y) -> bool: returns True when a gesture starts on an interactive
    widget, and the whole gesture is handed to that widget (dragging a slider must
    not page). Evaluated once per gesture.
    """

    def __init__(self, pages, page_w, circular=True, on_page_changed=None,
                 blocked=None, decide_ratio=DRAG_DECIDE_RATIO,
                 snap_ms=SNAP_ANIM_MS, page_h=None):
        self.pages = pages
        self.count = len(pages)
        self.page_w = page_w
        self.page_h = page_h
        self.circular = circular
        self.on_page_changed = on_page_changed
        self.blocked = blocked
        self.decide_ratio = decide_ratio
        self.snap_ms = snap_ms
        self.page = 0
        self._drag = False
        self._ignored = False
        self._anchor = 0.0
        self._dx = 0.0
        self._peer = None
        self._last_x = 0.0
        self._last_ms = 0
        self._vel = 0.0
        # [t0, cur_from, cur_to, peer, peer_from, peer_to, target]
        self._anim = None
        self._show_only(0)

    # ---------- Internal ----------
    def _flag(self, name):
        return getattr(lv.obj.FLAG, name, None)

    def _show_only(self, index):
        for i, p in enumerate(self.pages):
            try:
                p.set_x(0)
            except Exception:
                pass
            hid = self._flag("HIDDEN")
            if hid is None:
                continue
            try:
                if i == index:
                    p.clear_flag(hid)
                else:
                    p.add_flag(hid)
            except Exception:
                pass

    def _peer_index(self, dx):
        """For dx<0 (dragging left) the neighbour is the next page; otherwise the
        previous one."""
        if self.count <= 1:
            return None
        step = 1 if dx < 0 else -1
        idx = self.page + step
        if self.circular:
            return idx % self.count
        if 0 <= idx < self.count:
            return idx
        return None

    def _clamp(self, dx):
        w = self.page_w
        if dx > w:
            dx = w
        elif dx < -w:
            dx = -w
        if not self.circular:
            # Without wrap-around an edge has no neighbour, so the displacement must
            # be zero, otherwise a blank area is dragged in
            if dx > 0 and self.page <= 0:
                dx = 0
            elif dx < 0 and self.page >= self.count - 1:
                dx = 0
        return dx

    def _layout(self, dx):
        """Position the current page and its neighbour for the current
        displacement, hiding the rest."""
        hid = self._flag("HIDDEN")
        cur = self.pages[self.page]
        peer = self._peer_index(dx) if dx != 0 else None
        if peer == self.page:
            peer = None
        sign = -1.0 if dx < 0 else 1.0
        try:
            cur.set_x(int(dx))
            if hid is not None:
                cur.clear_flag(hid)
        except Exception:
            pass
        for i, p in enumerate(self.pages):
            if i == self.page or i == peer:
                continue
            if hid is not None:
                try:
                    p.add_flag(hid)
                except Exception:
                    pass
        self._peer = peer
        if peer is not None:
            try:
                self.pages[peer].set_x(int(dx - sign * self.page_w))
                if hid is not None:
                    self.pages[peer].clear_flag(hid)
            except Exception:
                self._peer = None

    def _peer_x(self, dx):
        """x of the neighbour page at displacement dx."""
        sign = -1.0 if dx < 0 else 1.0
        return dx - sign * self.page_w

    def _start_anim(self, now, cur_to, peer_to, target):
        self._anim = [now, self._dx, float(cur_to), self._peer,
                      self._peer_x(self._dx), float(peer_to), target]
        # Notify the target page as the animation starts (same as SnapPager): the
        # app can prepare its data during the 180~250ms animation so it slides in
        # already filled.
        if target != self.page and self.on_page_changed is not None:
            try:
                self.on_page_changed(target)
            except BaseException as error:
                print("[paging] on_page_changed error:", error)

    def _tick(self, now):
        a = self._anim
        el = time.ticks_diff(now, a[0])
        p = el / float(self.snap_ms) if self.snap_ms > 0 else 1.0
        if p < 0.0:
            p = 0.0
        elif p > 1.0:
            p = 1.0
        e = 1.0 - (1.0 - p) * (1.0 - p)      # ease-out
        c0, c1 = a[1], a[2]
        try:
            self.pages[self.page].set_x(int(c0 + (c1 - c0) * e))
        except Exception:
            pass
        peer = a[3]
        if peer is not None:
            p0, p1 = a[4], a[5]
            try:
                self.pages[peer].set_x(int(p0 + (p1 - p0) * e))
            except Exception:
                pass
        if p >= 1.0:
            self._finish(a)

    def _finish(self, a):
        target = a[6]
        self._anim = None
        self._dx = 0.0
        self._peer = None
        self.page = target
        self._show_only(target)

    def _decide(self, now):
        dx = self._dx
        adx = abs(dx)
        if adx < 1.0:
            self._dx = 0.0
            self._peer = None
            self._show_only(self.page)
            return
        adv = adx >= self.page_w * self.decide_ratio
        if not adv and adx >= DRAG_FLICK_MIN_PX and \
                abs(self._vel) >= DRAG_FLICK_PX_MS and \
                (self._vel > 0) == (dx > 0):
            adv = True
        sign = -1.0 if dx < 0 else 1.0
        if adv:
            peer = self._peer_index(dx)
            if peer is None:
                self._start_anim(now, 0, -sign * self.page_w, self.page)
            else:
                self._start_anim(now, sign * self.page_w, 0, peer)
        else:
            self._start_anim(now, 0, -sign * self.page_w, self.page)

    # ---------- Public ----------
    def poll(self, now, pressed, x, y=0):
        if self.count <= 1:
            return
        if pressed:
            if not self._drag:
                self._drag = True
                self._ignored = False
                self._anchor = float(x)
                self._dx = 0.0
                self._peer = None
                self._last_x = float(x)
                self._last_ms = now
                self._vel = 0.0
                self._anim = None
                if self.blocked is not None:
                    try:
                        self._ignored = bool(self.blocked(x, y))
                    except BaseException:
                        self._ignored = False
                return
            # Velocity estimate: decoupled from position updates, kept up to date
            # even when ignored (does not affect the result)
            dt = time.ticks_diff(now, self._last_ms)
            if dt > 0:
                v = (x - self._last_x) / float(dt)
                if v > DRAG_VEL_CLAMP:
                    v = DRAG_VEL_CLAMP
                elif v < -DRAG_VEL_CLAMP:
                    v = -DRAG_VEL_CLAMP
                self._vel = self._vel * 0.5 + v * 0.5
                self._last_x = float(x)
                self._last_ms = now
            if self._ignored:
                return
            self._dx = self._clamp(x - self._anchor)
            self._layout(self._dx)
            return

        # ---- Release ----
        if self._drag:
            self._drag = False
            ignored = self._ignored
            self._ignored = False
            if not ignored:
                self._decide(now)
            else:
                self._dx = 0.0
                self._peer = None
                self._show_only(self.page)
            return
        if self._anim is not None:
            self._tick(now)

    def go(self, target, animate=True, now=None):
        """Jump to a page. animate=False is for programmatic positioning
        (initialisation / going back to the first page).

        When now is omitted the current time is taken: the animation needs a time
        base, and without one it can only switch instantly (otherwise animate=True
        would silently degrade to an instant cut, which callers would not notice).
        """
        if self.count <= 1:
            return
        if now is None:
            now = time.ticks_ms()
        if self.circular:
            target = target % self.count
        else:
            target = max(0, min(self.count - 1, target))
        self._drag = False
        self._ignored = False
        self._anim = None
        self._dx = 0.0
        self._peer = None
        if target == self.page:
            self._show_only(self.page)
            return
        if not animate or now is None:
            old = self.page
            self.page = target
            self._show_only(target)
            if old != target and self.on_page_changed is not None:
                try:
                    self.on_page_changed(target)
                except BaseException as error:
                    print("[paging] on_page_changed error:", error)
            return
        # Animated: treat it as pushing the current page out toward the target,
        # taking the circular shortest path
        step = 1 if ((target - self.page) % self.count) == 1 else -1
        self._peer = target
        self._dx = 0.0
        sign = 1.0 if step > 0 else -1.0
        self._start_anim(now, sign * self.page_w, 0, target)

    def busy(self):
        """Whether dragging or animating a snap (apps pause redraws / costly work
        while true)."""
        return self._drag or self._anim is not None

    def dragging(self):
        return self._drag

    def reset_gesture(self):
        self._drag = False
        self._ignored = False
        self._anim = None
        self._dx = 0.0
        self._peer = None
        self._show_only(self.page)



# ---------------------------------------------------------------------------
# On-disk raw pixel cache
# ---------------------------------------------------------------------------

CHUNK_SIZE = 8192


def chunked_read(path, size, chunk=CHUNK_SIZE):
    """Read a file in chunks with readinto, returning a bytearray.

    Measured (203KB file): a single f.read() takes 92ms, this function 14ms. The
    difference is that one big read must allocate a large object, while readinto
    writes into a preallocated buffer.
    """
    buf = bytearray(size)
    mv = memoryview(buf)
    with open(path, "rb") as f:
        off = 0
        while off < size:
            got = f.readinto(mv[off:off + chunk])
            if not got:
                break
            off += got
        if off != size:
            raise OSError("short read {} != {}".format(off, size))
    return buf


def write_file(path, data, chunk=CHUNK_SIZE):
    """Write a file in chunks (one large write is just as slow)."""
    mv = memoryview(data)
    total = len(mv)
    with open(path, "wb") as f:
        off = 0
        while off < total:
            f.write(mv[off:off + chunk])
            off += chunk


def file_exists(path):
    """CanMV's os module has no os.path, so probe with open."""
    try:
        with open(path, "rb"):
            return True
    except OSError:
        return False


def ensure_dir(path):
    """Create directories level by level (CanMV has no os.makedirs)."""
    current = ""
    for part in path.strip("/").split("/"):
        current += "/" + part
        try:
            os_mkdir(current)
        except BaseException:
            pass


def os_mkdir(path):
    import os
    os.mkdir(path)


def file_size(path):
    import os
    return os.stat(path)[6]


def swap_rb(data):
    """Convert R,G,B pixel order to B,G,R (the byte order LVGL's RGB888 cf
    expects).

    Why conversion is mandatory: LVGL's LV_IMG_CF_RGB888 reads the buffer as
    **B,G,R** while image.RGB888 stores R,G,B. Feeding it directly to LVGL swaps R
    and B -- measured on board, yellow (R+G) renders cyan and red renders pure
    blue, the classic R/B swap.

    Implementation choices (measured; do not switch back to other forms):
        ulab 1-D stride assignment  14ms / 207KB  (adopted)
        Python memoryview loop     167ms / 207KB
        ulab np.flip / a[:, ::-1]   1ms but the **result is a cyclic shift, not a
                                    reversal** -- wrong!
    """
    n = len(data)
    if n < 3 or n % 3:
        return data
    try:
        import ulab.numpy as np
        a = np.frombuffer(bytes(data), dtype=np.uint8)
        out = np.zeros(n, dtype=np.uint8)
        out[0::3] = a[2::3]
        out[1::3] = a[1::3]
        out[2::3] = a[0::3]
        return bytes(out.tobytes())
    except BaseException as error:
        print("[cache] ulab swap unavailable ({}), slow path".format(error))

    out = bytearray(n)
    src = memoryview(data)
    dst = memoryview(out)
    i = 0
    while i < n - 2:
        dst[i] = src[i + 2]
        dst[i + 1] = src[i + 1]
        dst[i + 2] = src[i]
        i += 3
    return bytes(out)


class RawImageCache:
    """Cache already-scaled picture pixels on disk so decoding and scaling are not
    repeated.

    Why it is worth it (measured on board, 4 photos 1920x1080 -> 385x180):
        decode + crop/scale every time: 898ms (decode 23ms + scale 172ms, ~195ms each)
        read the cached raw RGB888:     56ms (4 x 203KB, chunked)
    Scaling is the absolute bottleneck: decoding takes only 23ms while
    copy(roi+, x_scale+) takes 172ms.

    Cache file format: raw pixels in **B,G,R order** (what LVGL's RGB888 cf needs),
    with `_bgr` in the name, e.g. IMG_0001_385x180_bgr.raw.
    A changed size or byte order naturally selects another file, so stale content is
    never read. Photo file names are increasing ids (never overwritten), so no extra
    mtime validation is needed.

    What is returned is an **lv.img_dsc_t**, not an Image. Reason (the root cause of
    "occasional colour shift"):
        binding with image.as_lvgl_img_src(widget) leaves the colour format up to
        that function, and the way it hands 3bpp RGB888 pixels to LVGL is unreliable
        on this firmware -- on board the photo occasionally shifted colour overall
        (yellow turned blue) and looked fine again a few views later.
        Building img_dsc_t explicitly lets us set cf=lv.COLOR_FORMAT.RGB888
        ourselves, exactly what the desktop tiles do (those never shifted).
    Also dsc.data references the bytearray directly, still zero-copy (measured
    virtaddr==addressof).

    Usage:
        cache = RawImageCache("/data/cache/thumbs", 385, 180)
        dsc = cache.get(src_path, producer)
        widget.set_src(dsc)
    """

    def __init__(self, cache_dir, width, height, bpp=3, fmt=None,
                 max_buffers=8):
        self.cache_dir = cache_dir
        self.width = width
        self.height = height
        self.bpp = bpp
        self.fmt = fmt
        self.size = width * height * bpp
        # key -> bytearray. dsc.data references these buffers directly, so they
        # must be retained; a dict rather than a list means a picture re-entering
        # the window reuses the same memory, otherwise every disk read adds another
        # ~200KB buffer and memory keeps growing.
        self._buffers = {}
        # key -> lv.img_dsc_t (must be retained too; a collected dsc leaves the
        # widget dangling)
        self._dscs = {}
        # Resident buffer ceiling (count). Multiply by the per-image size for
        # memory: 203KB per thumbnail, 1MB per full-screen image. The app also evicts
        # by window; this is only a backstop.
        self.max_buffers = max_buffers
        self.hits = 0
        self.misses = 0
        ensure_dir(cache_dir)

    def _stem(self, path):
        name = path.rsplit("/", 1)[-1]
        dot = name.rfind(".")
        return name[:dot] if dot > 0 else name

    def cache_path(self, src_path):
        # The _bgr suffix marks B,G,R order. Changing the byte order means changing
        # this suffix so old caches miss.
        return "{}/{}_{}x{}_bgr.raw".format(
            self.cache_dir, self._stem(src_path), self.width, self.height)

    def get(self, src_path, producer):
        """Return an lv.img_dsc_t; a hit reads from disk, a miss calls producer and
        writes to disk.

        producer must "decode and crop/scale to self.width x self.height" and return
        an RGB888 Image. Its Image is **read for pixels only** and is never returned
        or retained (its memory lives in the media pool and later decodes reuse it).
        """
        cpath = self.cache_path(src_path)
        if file_exists(cpath):
            try:
                buf = self._buffers.get(cpath)
                if buf is None:
                    buf = chunked_read(cpath, self.size)
                    self._buffers[cpath] = buf
                    self._trim_buffers()
                self.hits += 1
                return self._dsc_for(cpath, buf)
            except BaseException as error:
                print("[cache] read '{}' failed: {}".format(cpath, error))
                self._forget(cpath)
                self._remove(cpath)

        self.misses += 1
        img = producer(src_path)
        if img is None:
            return None
        if img.width() != self.width or img.height() != self.height:
            # Size mismatch (producer rounding error): must not be used as cache
            # content.
            print("[cache] size mismatch: want {}x{}, got {}x{} (not cached)".format(
                self.width, self.height, img.width(), img.height()))
            return None
        try:
            import uctypes
            raw = bytes(uctypes.bytes_at(img.virtaddr(), self.size))
        except BaseException as error:
            print("[cache] read pixels '{}' failed: {}".format(cpath, error))
            return None

        # Convert to the B,G,R order LVGL expects. Mandatory: image's RGB888 is
        # R,G,B while lv's RGB888 cf reads B,G,R, so skipping it swaps R/B (yellow
        # turns cyan, red turns blue).
        raw = swap_rb(raw)

        # Write to disk, copy into our own bytearray, then build the dsc from it.
        # Never reuse the Image producer returned: its pixels live in the media pool
        # and later decodes overwrite them.
        try:
            write_file(cpath, raw)
        except BaseException as error:
            print("[cache] write '{}' failed: {}".format(cpath, error))
        buf = bytearray(raw)
        self._buffers[cpath] = buf
        self._trim_buffers()
        return self._dsc_for(cpath, buf)

    def _dsc_for(self, cpath, buf):
        """Cache lv.img_dsc_t per key (one each, no cross-talk)."""
        dsc = self._dscs.get(cpath)
        if dsc is not None:
            return dsc
        dsc = lv.img_dsc_t({
            "header": {"cf": lv.COLOR_FORMAT.RGB888,
                       "w": self.width, "h": self.height},
            "data_size": len(buf),
            "data": buf,
        })
        self._dscs[cpath] = dsc
        return dsc

    def _trim_buffers(self):
        """When resident buffers exceed the limit, **warn only, never delete**.

        Why not auto-delete: every buffer is referenced by a dsc, and that dsc may
        be attached to a widget. Deleting a buffer behind its back points the widget
        at freed memory -- on board that shows as garbage or a fully discoloured
        screen, far worse than using a little more memory. The app controls the
        lifetime precisely: call drop() when unbinding a widget.
        """
        if self.max_buffers <= 0:
            return
        n = len(self._buffers)
        if n > self.max_buffers:
            print("[cache] {} buffers alive ({}KB), above the {} warning line "
                  "- app should drop unused keys".format(
                      n, (n * self.size) // 1024, self.max_buffers))

    def _forget(self, cpath):
        self._buffers.pop(cpath, None)
        self._dscs.pop(cpath, None)

    @staticmethod
    def _remove(path):
        try:
            import os
            os.remove(path)
        except BaseException:
            pass

    def release(self):
        """Release all held buffers/dscs (call when the app exits).

        The src of every widget referencing them must be cleared first, otherwise the
        pointers dangle.
        """
        self._buffers = {}
        self._dscs = {}

    def drop(self, src_path):
        """Drop a single key (only when no widget references it)."""
        self._forget(self.cache_path(src_path))
