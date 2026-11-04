# machine.TOUCH -> LVGL input device, plus the raw high-rate point queue apps read.
#
# Sampling runs along two paths:
# - poll(): called by the main loop every ~5ms and the only place that reads the
#   hardware. It updates the shared state LVGL consumes (_pressed/_last_x/_last_y)
#   and appends raw points to the _raw queue.
# - callback(): called by LVGL every ~30ms; it reads no hardware and only reports
#   the shared state maintained by poll(). That way LVGL always sees fresh state and
#   the PRESSED hold for quick taps is more reliable.
# Apps (Touch, for one) consume _raw directly through read_raw(), sampling at
# ~5ms, far above LVGL's 30ms.

import time

import lvgl as lv
from machine import TOUCH

from hardware import panel
from shell.perf import BOOST_TAIL_MS, PERF
from shell.theme import SWIPE_DIAG

TOUCH_INPUT = None  # the live instance, so apps and the key poll can reach it


def instance():
    """The live TouchScreen, or None before one was constructed."""
    return TOUCH_INPUT


class TouchScreen:
    """Display rotation is handled by the Display flag and the FT5316 driver's
    default rotation already matches it, so coordinates line up with the display
    coordinate system directly.

    Multi-touch: raw points carry track_id so an app can draw each finger as its own
    stroke. LVGL itself is single-pointer, so only the "primary contact" drives
    _pressed/_last_x/_last_y; other contacts are merely queued.
    """

    # Wait this long after an Up with no new contact before really releasing (bridges
    # the FT5316's phantom Up during a drag).
    # Measured on board (2026-10): while dragging, the gap between a phantom Up and
    # the next contact is p50=20-26ms / max=40ms. This value sets the dead time
    # between release and the start of the snap, but it must exceed the measured max,
    # otherwise mid-drag is mistaken for a release (snap fires early -> wrong
    # start_page -> two pages per swipe). 45ms gives 5ms of headroom and stays under
    # the perceptual threshold. Do not lower it to <=40.
    RELEASE_HOLDOFF_MS = 45

    # ---- Tap jitter dead zone ----
    # LVGL's default scroll_limit is 10px: once the finger moves more than 10px
    # after touch-down it enters scroll mode, and entering scroll mode makes LVGL
    # cancel the child's CLICKED event -- which looks like "tapping the icon does
    # nothing and the page wobbles before snapping back". Measured FT5316 jitter
    # during a normal tap reaches 15-20px, so 10px is far too tight.
    # This binding does **not** expose indev.set_scroll_limit (measured MISSING on
    # board), so the touch layer implements an equivalent software dead zone.
    # Note this is **not** the page-turn threshold (that is still
    # paging.SWIPE_THRESHOLD_PX = 120); it only decides when a tap becomes a drag.
    SCROLL_LIMIT_PX = 24

    # ---- Coast bridging (removes the "pause after release") ----
    # Doing nothing during the 45ms holdoff leaves the page completely still and the
    # eye catches the hiccup. Fix: keep advancing the reported coordinate at the
    # last velocity for the duration of the wait, and LVGL keeps scrolling, which
    # reads as inertia with no still gap. This only feeds coordinates; it never
    # fires SCROLL_BEGIN/END and never fights the snap animation for control.
    # Note this is not re-enabling LVGL's SCROLL_MOMENTUM (that inflates the
    # displacement and breaks the threshold test); the coast distance is bounded by
    # the holdoff window at 45ms of movement at most.
    COAST_MIN_V = 0.2       # px/ms; below this there is no coasting (tap / slow drag)
    COAST_MAX_V = 3.0       # px/ms; clamp so a fast flick cannot fly off
    COAST_WINDOW_MS = 80    # velocity estimation window

    def __init__(self):
        global TOUCH_INPUT
        TOUCH_INPUT = self
        # Shared state for LVGL: the FT5316 only raises events at the instant of a
        # down/move, so with no new event the previous state/coordinate is held,
        # which keeps the press continuous until a real up arrives.
        self._pressed = False
        self._last_x = 0
        self._last_y = 0
        # Release holdoff: a continuous FT5316 drag is interleaved with phantom Ups
        # that split one swipe into several down/up segments, which makes LVGL treat
        # each segment as a tap and never scroll. An Up therefore does not release
        # immediately: a new contact within HOLDOFF counts as a continuous press
        # (bridging the phantom Up), and only a full HOLDOFF without contacts really
        # releases.
        self._up_pending = False
        self._up_ms = 0
        # Diagnostic: timestamp of the last real release (_pressed True->False) for
        # swipe latency analysis.
        self._release_ms = 0
        # Diagnostic: time of the first Up in this gesture / number of Up samples /
        # snapshot of _up_ms at release time.
        self._first_up_ms = 0
        self._up_n = 0
        self._rel_up_ms = 0
        # Diagnostic: gaps (ms) between repeated Ups and the next contact while
        # dragging, used to size the holdoff.
        self._gap_ms = []
        # Coast bridging: velocity samples (ms, x), current velocity in px/ms, and
        # the timestamp of the last sample
        self._samples = []
        self._vel = 0.0
        self._last_ms = 0
        # Time of the last contact or release: decides whether the high frame rate
        # is needed (frame boost)
        self._active_ms = 0
        # Diagnostic: total distance (px) added by coast bridging in one gesture and
        # the number of advance steps
        self._coast_px = 0
        self._coast_n = 0
        # Raw point queue: (x, y, event, track_id) for high-rate consumption by apps.
        # event 2=Down 3=Move anything else=Up; track_id is a stable id for one
        # finger across a single press, letting an app split contacts into strokes.
        self._raw = []
        # LVGL is single-pointer and can only follow one finger: _primary_id is the
        # current primary contact and _down_ids is the set of contacts still on
        # screen (added by Down/Move, removed by Up). When the primary lifts, another
        # contact is promoted immediately, so the pointer never hangs in mid-air and
        # never jumps back and forth between fingers.
        self._primary_id = None
        self._down_ids = set()
        self._track_xy = {}
        # Tap dead-zone state: the anchor is the touch-down point; _dz_gate=True
        # means the finger is still inside the dead zone.
        # See the SCROLL_LIMIT_PX comment: this binding has no
        # indev.set_scroll_limit, so the dead zone lives here.
        self._dz_x0 = 0
        self._dz_y0 = 0
        self._dz_gate = False
        try:
            self.touch = TOUCH(0)
            print("[GUI] Touch initialized.")
        except Exception as error:
            print("[GUI] Touch init failed: {}".format(error))
            self.touch = None
            return
        indev = lv.indev_create()
        indev.set_type(lv.INDEV_TYPE.POINTER)
        indev.set_read_cb(self.callback)
        print("[GUI] tap deadzone = {}px (software)".format(self.SCROLL_LIMIT_PX))
        print("[GUI] touch holdoff = {}ms".format(self.RELEASE_HOLDOFF_MS))

    def poll(self):
        """Called by the main loop every ~5ms: read the hardware, update the shared
        state, append raw points."""
        if self.touch is None:
            return
        t0 = time.ticks_ms()
        try:
            # The panel supports 5 points (FT5316 info.point_num=5); ask for exactly
            # that, never more than the hardware can deliver.
            tp = self.touch.read(5)
            now = time.ticks_ms()
            if tp:
                self._active_ms = now
                down = self._down_ids
                touched = False
                # Whether a gesture was already in progress before this batch
                # decides if this press needs a new dead-zone anchor.
                new_press = not self._pressed
                for p in tp:
                    if len(self._raw) < 64:
                        self._raw.append((p.x, p.y, p.event, p.track_id))
                    if p.event in (2, 3):  # Down / Move
                        self._track_xy[p.track_id] = (p.x, p.y)
                        down.add(p.track_id)
                        # Primary contact: keep the current one; if there is none (or
                        # it just lifted) take the first finger pressed in this batch.
                        if self._primary_id is None:
                            self._primary_id = p.track_id
                        if p.track_id == self._primary_id:
                            if new_press:
                                # touch-down point = dead-zone anchor, once per batch
                                self._dz_x0 = p.x
                                self._dz_y0 = p.y
                                self._dz_gate = True
                                new_press = False
                            px, py = p.x, p.y
                            if self._dz_gate:
                                lim = self.SCROLL_LIMIT_PX
                                dx = px - self._dz_x0
                                dy = py - self._dz_y0
                                if -lim < dx < lim and -lim < dy < lim:
                                    # inside the dead zone: pin the coordinate to the
                                    # anchor so LVGL sees no displacement at all
                                    px, py = self._dz_x0, self._dz_y0
                                else:
                                    # left the dead zone: subtract it on each axis
                                    # separately so the drag does not jump 24px at once
                                    self._dz_gate = False
                                    if dx > lim:
                                        dx -= lim
                                    elif dx < -lim:
                                        dx += lim
                                    else:
                                        dx = 0
                                    if dy > lim:
                                        dy -= lim
                                    elif dy < -lim:
                                        dy += lim
                                    else:
                                        dy = 0
                                    px = self._dz_x0 + dx
                                    py = self._dz_y0 + dy
                            self._last_x = px
                            self._last_y = py
                            touched = True
                    else:                  # Up
                        down.discard(p.track_id)
                        if p.track_id == self._primary_id:
                            self._primary_id = None
                # The primary contact lifted but other fingers are still on the
                # panel: pick one and let the LVGL pointer keep following it, so it
                # neither hangs in mid-air nor gets mistaken for a release.
                if self._primary_id is None and down:
                    for tid in down:
                        self._primary_id = tid
                        break
                    xy = self._track_xy.get(self._primary_id)
                    if xy is not None:
                        self._last_x = xy[0]
                        self._last_y = xy[1]
                    # A different finger does not inherit the previous anchor; it
                    # simply follows the coordinates that arrive from now on
                    self._dz_gate = False
                    self._up_pending = False
                if touched:
                    # This batch has a press/move: keep the press alive and keep
                    # feeding velocity samples (used by coast bridging).
                    # The test must be "did this batch contain Down/Move", not "is the
                    # down set non-empty at the end of the batch": the FT5316
                    # normally splits a tap into a Down+Up pair inside the same
                    # batch, and looking at the set would drop the tap entirely.
                    if self._up_pending and SWIPE_DIAG:
                        # A phantom Up was bridged: record the gap to the next
                        # contact (only gaps under 1s count as the same drag)
                        gap = time.ticks_diff(now, self._up_ms)
                        if gap < 1000:
                            self._gap_ms.append(gap)
                    if not self._pressed:
                        self._pressed = True
                        self._first_up_ms = 0
                        self._up_n = 0
                        self._samples = []
                        self._vel = 0.0
                        self._coast_px = 0
                        self._coast_n = 0
                    self._up_pending = False
                    self._add_sample(now, self._last_x)
                    self._last_ms = now
                    self._update_velocity(now)
                elif not down and self._pressed and not self._up_pending:
                    # Not a single finger left on the panel: enter the holdoff test
                    self._up_pending = True
                    self._up_ms = now
                    self._first_up_ms = now
                    self._up_n += 1
                    self._last_ms = now
                elif self._pressed and self._up_pending:
                    self._coast(now)
            elif self._pressed and self._up_pending:
                self._coast(now)
        except Exception:
            pass
        # Holdoff debounce: after an Up, wait until no new contact arrives within
        # HOLDOFF before really releasing (bridges a phantom Up mid-drag).
        # HOLDOFF must exceed both the measured worst-case phantom-Up gap (33ms) and
        # the LVGL indev read period (30ms); otherwise a drag gets split into
        # segments, or a quick tap loses PRESSED.
        # The _down_ids condition is there too: with several fingers any finger still
        # on the panel means this is not a release.
        if self._pressed and self._up_pending and not self._down_ids and \
                time.ticks_diff(time.ticks_ms(), self._up_ms) >= self.RELEASE_HOLDOFF_MS:
            self._pressed = False
            self._up_pending = False
            self._release_ms = time.ticks_ms()
            self._rel_up_ms = self._up_ms
            # Refresh the activity time at release too, so the whole snap animation
            # gets the high frame rate
            self._active_ms = self._release_ms
        t = time.ticks_diff(time.ticks_ms(), t0)
        if t > PERF["touch_max"]:
            PERF["touch_max"] = t

    def gap_stats(self):
        """Diagnostic: drain the Up->next-contact gap samples and return
        (n, p50, p90, max); None when there are no samples."""
        gaps = sorted(self._gap_ms)
        self._gap_ms = []
        if not gaps:
            return None
        n = len(gaps)
        return n, gaps[n // 2], gaps[min(n - 1, (n * 9) // 10)], gaps[-1]

    def busy(self):
        """For the main loop: True while a holdoff decision is pending (only this
        short stretch needs 1ms granularity).

        Without it, "holdoff expired -> real release" would be delayed by the
        main-loop period (5-22ms) and turn into a perceptible extra gap after the
        finger lifts. _pressed is deliberately not included: 5ms granularity is
        plenty while dragging (LVGL's indev itself only reads every 30ms).
        """
        return self._up_pending

    @property
    def pressed(self):
        """For apps: whether a finger is currently (still considered) down.

        This includes the holdoff window, so it stays True for up to 45ms after the
        finger lifts. That is exactly what "settle after release" style checks want.
        """
        return self._pressed

    def pos(self):
        """For apps: current contact coordinate (x, y) in screen coordinates.

        With no finger down it returns the last position rather than (0, 0): an app
        that implements drag-tracking together with pressed needs the final position
        before the lift at the moment of release.
        """
        return (self._last_x, self._last_y)

    def active(self):
        """For the main loop: whether the high frame rate is needed (touching, or
        just released while the snap animation may still be running)."""
        if self._pressed or self._up_pending:
            return True
        return time.ticks_diff(time.ticks_ms(), self._active_ms) < BOOST_TAIL_MS

    # ---- Coast bridging internals ----
    def _add_sample(self, now, x):
        """Record one velocity sample, keeping only the most recent few in the
        window (constant memory)."""
        samples = self._samples
        samples.append((now, x))
        while len(samples) > 8:
            samples.pop(0)

    def _update_velocity(self, now):
        """Estimate velocity (px/ms) from the first and last sample in the window,
        then clamp it. Stale samples reset it to zero so that "drag halfway, pause,
        then release" is not mistaken for a fast flick."""
        samples = self._samples
        while len(samples) > 2 and \
                time.ticks_diff(now, samples[0][0]) > self.COAST_WINDOW_MS:
            samples.pop(0)
        if len(samples) < 2:
            self._vel = 0.0
            return
        t0, x0 = samples[0]
        t1, x1 = samples[-1]
        dt = time.ticks_diff(t1, t0)
        if dt <= 0:
            self._vel = 0.0
            return
        vel = (x1 - x0) / float(dt)
        if vel > self.COAST_MAX_V:
            vel = self.COAST_MAX_V
        elif vel < -self.COAST_MAX_V:
            vel = -self.COAST_MAX_V
        self._vel = vel

    def _coast(self, now):
        """Keep advancing the reported coordinate at the last velocity during the
        holdoff wait so LVGL keeps scrolling.

        Only _last_x changes (callback reads it); the LVGL scroll API is never
        touched, so no SCROLL_BEGIN/END fires and the snap animation is never
        contested.
        """
        dt = time.ticks_diff(now, self._last_ms)
        if dt <= 0:
            return
        self._last_ms = now
        self._update_velocity(now)
        vel = self._vel
        if -self.COAST_MIN_V < vel < self.COAST_MIN_V:
            return
        if dt > 60:      # the main loop occasionally stalls; cap the step
            dt = 60
        nx = self._last_x + vel * dt
        if nx < 0:
            nx = 0.0
        elif nx > panel.WIDTH - 1:
            nx = float(panel.WIDTH - 1)
        nx = int(nx)
        if nx != self._last_x:
            self._coast_px += abs(nx - self._last_x)
            self._coast_n += 1
            self._last_x = nx

    def read_raw(self):
        """For apps: take and clear the raw point list accumulated since the last
        call. Each item is (x, y, event, track_id); event 2=Down 3=Move else Up."""
        if not self._raw:
            return None
        pts = self._raw
        self._raw = []
        return pts

    def clear_raw(self):
        """Drop the queued raw points. Called when opening or closing an app: the
        down/up of "tap the icon to enter the app" would otherwise stay in the queue
        and be mistaken for a tap by the app's own tap detection."""
        self._raw = []

    def callback(self, driver, data):
        # No hardware access here (poll already read it); only report the shared
        # state that poll maintains.
        # The coordinate keeps advancing during a continuous press (including a drag
        # bridged across phantom Ups), which is what lets LVGL recognise a swipe.
        if self.touch is None:
            data.state = lv.INDEV_STATE.RELEASED
            return
        data.point.x = self._last_x
        data.point.y = self._last_y
        data.state = lv.INDEV_STATE.PRESSED if self._pressed else lv.INDEV_STATE.RELEASED
