# Click-tone feedback: a passive buzzer on IO43 gives a short "bi" when a button,
# a keyboard key, or a physical key is clicked.
#
# ---------------------------------------------------------------------------
# Measured on board (2026-10-28, this board, PWM direct drive)
#
#   PWM(43, freq=2000, duty=50)   create + output both fine, duty(0) is silent
#   Response: the 2kHz resonance point is the loudest and cleanest; 1kHz is very
#             weak by ear (1kHz is the Linux PWM sysfs default, so copying that
#             default over ends up "almost inaudible")
#   60ms pulse  a clean, short "dip" by ear
#   200ms pulse clearly drawn out; right for a prompt/alarm, wrong for a click
#
# So the click tone uses 2kHz / 50% duty / 60ms, throttled to a 60ms minimum gap.
# ---------------------------------------------------------------------------
#
# [Why not LVGL indev-level events]
# This firmware binding (LVGL 9.0.0) does **not** expose lv_indev_add_event_cb -
# measured, indev.add_event / add_event_cb / send_event are all None. So the
# "attach one global callback covering every widget" shape does not work here;
# widgets have to be registered one at a time. obj.add_event cannot be
# monkey-patched either (lv.obj is a type object and patching it raises
# AttributeError).
#
# [Why not sleep inside the callback]
# The callback runs synchronously inside lv.task_handler(), so a 60ms sleep
# freezes the whole frame (touch stops polling, nothing redraws) and repeated
# taps stack on top of each other. tap() therefore only sets duty and records
# the deadline, and the main loop's per-frame poll() clears it on time, at the
# cost of a few register writes.
#
# [Why PWM instead of the codec/speaker]
# The audio path on this board (codec -> NS4150B) costs about 47ms per open and a
# fixed ~1.1s for stop_stream, so using it for a click tone would necessarily
# slow the interaction down. The PWM buzzer is the only low-latency path.
#
# Failure fallback: when PWM is unavailable init() prints one line and
# tap()/poll() all become no-ops; the GUI must still start normally.
#
# [Why an off-switch thread alone is not enough - GIL exclusivity trap
#  (measured 2026-10-28)]
# The MicroPython GIL is held exclusively across a C call: once any thread enters
# a long blocking C call (wlan.scan ~1.5s, failed connect ~9s, kpu.load_kmodel
# ~1.5s, audio stop_stream ~1.1s), no Python at all can run - the off-switch
# thread and machine.Timer callbacks are all starved (measured: scan holds the
# lock for 1482ms and the beep only went silent at +1483ms; a Timer ISR behaved
# the same at +1482ms). A resident thread therefore cannot fix the very blocking
# case it was meant to fix. Two things actually work:
#   1) on_click sleeps sleep_ms(BEEP_MS+5) before calling the callback: sleep_ms
#      releases the GIL, so the off-switch thread can zero duty before the
#      callback enters a long blocking call (measured: silent at +35ms).
#   2) set_busy_check(fn): when a worker already holds the lock inside a C call
#      (e.g. another button tapped while connecting), tap() simply makes no
#      sound - sleep_ms cannot even be entered in that state, so any output would
#      turn into a long beep; better to stay silent.
# init() still starts one resident thread that only switches the output off: it
# polls every 2ms while a tone window is open and every 50ms when idle
# (negligible cost). The main loop's poll() stays as the backstop for when the
# thread cannot start.
#
# Usage:
#   import beep
#   beep.init()                          # once at boot (in main())
#   beep.on_click(btn, self._on_click)   # replaces btn.add_event(cb, CLICKED, None)
#   beep.bind(kb, lv.EVENT.VALUE_CHANGED)  # sound only, no callback wrap (keyboard keys)
#   beep.poll()                          # every frame in the main loop
#   beep.shutdown()                      # on exit

import time

import lvgl as lv

try:
    from machine import PWM
except BaseException:
    PWM = None

# Buzzer pin: IO43 (alt2 = PWM1), the same pin the factory self-test drives.
PIN = 43
# Click-tone frequency: the passive buzzer resonance point; measured 2kHz is the
# loudest and cleanest.
FREQ = 2000
# Click-tone duration / minimum gap (milliseconds).
# 10ms: shorter than 30ms and closer to a tactile "tick" (30ms still sounded
# long). The worker polls every 2ms to switch off, so real output is ~10-12ms.
# The minimum gap stays at 60ms (> duration) so rapid taps do not smear into one
# continuous tone.
BEEP_MS = 10
MIN_GAP_MS = 60
# Duty cycle (percent). The passive buzzer is square-wave driven; 50% is loudest.
DUTY = 50

_pwm = None
_off_ms = 0         # 0 = no tone window is currently open
_last_ms = 0
_stop = False       # off-switch thread exit flag
_thread_ok = False
_busy_check = None  # True = a worker holds the GIL in a C call, so tap stays silent


def _worker():
    """Do one thing only: set duty to 0 once the tone window expires."""
    global _off_ms, _pwm
    while not _stop:
        deadline = _off_ms
        if deadline:
            if time.ticks_diff(time.ticks_ms(), deadline) >= 0:
                pwm = _pwm
                try:
                    if pwm is not None:
                        pwm.duty(0)
                except BaseException:
                    _pwm = None
                _off_ms = 0
            time.sleep_ms(2)
        else:
            time.sleep_ms(50)


def init(pin=PIN, freq=FREQ):
    """Call once at boot. Never raises; tap()/poll() degrade to no-ops on failure."""
    global _pwm, _off_ms, _last_ms, _stop, _thread_ok
    if PWM is None:
        print("[BEEP] machine.PWM unavailable, click sound disabled")
        return False
    try:
        _pwm = PWM(pin, freq=freq, duty=0)
    except BaseException as error:
        _pwm = None
        print("[BEEP] init GPIO{} failed: {}".format(pin, error))
        return False
    _off_ms = 0
    _last_ms = 0
    _stop = False
    try:
        import _thread
        _thread.start_new_thread(_worker, ())
        _thread_ok = True
    except BaseException as error:
        _thread_ok = False
        print("[BEEP] timer thread failed ({}), falling back to poll()".format(error))
    print("[BEEP] ready: GPIO{} {}Hz {}ms (thread={})".format(
        pin, freq, BEEP_MS, _thread_ok))
    return True


def ready():
    return _pwm is not None


def set_busy_check(fn):
    """Register a "worker holds the GIL exclusively" predicate. tap() asks it
    first and stays silent when it returns True.

    Why: while a worker (net_mgr connecting/scanning) holds the lock inside a
    wlan C call, the main thread cannot even enter sleep_ms, so any tone would
    necessarily become a long beep - better not to sound at all. gui.py wires
    net_mgr.busy in at boot.
    """
    global _busy_check
    _busy_check = fn


def tap():
    """Fire one click tone. Only sets duty and the deadline, never blocks."""
    global _off_ms, _last_ms, _pwm
    pwm = _pwm
    if pwm is None:
        return
    # A worker holds the GIL inside a C call: no thread could switch this tone
    # off, so stay silent.
    if _busy_check is not None:
        try:
            if _busy_check():
                return
        except BaseException:
            pass
    now = time.ticks_ms()
    # Throttle: one sound per burst of taps, and per CLICKED that reaches both a
    # child and its parent
    if _last_ms and time.ticks_diff(now, _last_ms) < MIN_GAP_MS:
        return
    _last_ms = now
    try:
        pwm.duty(DUTY)
        _off_ms = now + BEEP_MS
    except BaseException:
        # A channel fault disables it permanently so the error log is not spammed
        # every frame
        _pwm = None
        _off_ms = 0


def tone(ms):
    """Emit one tone of the given duration (no throttle, and not mixed with the
    10ms tap click).

    Reuses the same off-switch thread/poll: this only sets duty and the deadline
    and _worker (or the main-loop poll as backstop) zeroes it on time. The IMU
    calibration uses it for prompts: a short double beep on success, a long beep
    on failure.
    """
    global _off_ms, _pwm
    pwm = _pwm
    if pwm is None:
        return
    try:
        pwm.duty(DUTY)
        _off_ms = time.ticks_add(time.ticks_ms(), ms)
    except BaseException:
        _pwm = None
        _off_ms = 0


def double_tone(ms, gap_ms):
    """Two short beeps: ms of tone, gap_ms apart. Blocks for ms+gap_ms in total
    (a bit over one frame); only called on calibration state changes, never on
    the per-frame path."""
    tone(ms)
    try:
        time.sleep_ms(gap_ms)
    except BaseException:
        pass
    poll()
    tone(ms)


def poll():
    """Called every frame by the main loop: switch the output off when due. Idle
    cost is a single integer comparison."""
    global _off_ms, _pwm
    if _off_ms == 0 or _pwm is None:
        return
    if time.ticks_diff(time.ticks_ms(), _off_ms) >= 0:
        _off_ms = 0
        try:
            _pwm.duty(0)
        except BaseException:
            _pwm = None


def shutdown():
    """Switch the output off and release the channel on exit."""
    global _pwm, _off_ms, _stop
    _stop = True
    pwm = _pwm
    _pwm = None
    _off_ms = 0
    if pwm is None:
        return
    try:
        time.sleep_ms(25)   # one cycle for the off-switch thread to exit, so it
                            # cannot touch the channel after deinit
    except BaseException:
        pass
    try:
        pwm.duty(0)
    except BaseException:
        pass
    try:
        pwm.deinit()
    except BaseException:
        pass


def on_click(obj, cb, user_data=None):
    """Register a CLICKED callback and play one beep before it runs.

    Replaces obj.add_event(cb, lv.EVENT.CLICKED, user_data). The callback keeps
    its single-argument signature cb(event), so existing lambdas and bound
    methods work unchanged.
    """
    def _wrapped(event, _cb=cb):
        tap()
        # Sleep through the tone window before entering the callback: sleep_ms
        # releases the GIL and yields the CPU. Once BEEP_MS has passed, poll() is
        # called here to zero duty synchronously - timing must not depend on the
        # off-switch thread (idle it only wakes every 50ms, possibly later than
        # this 35ms window, and it gets starved once the callback enters a long
        # blocking C call; measured, off_ms then stays at the expired deadline
        # instead of becoming 0). poll() runs on the main thread holding the lock,
        # so duty(0) takes effect immediately and the output is muted before the
        # callback runs. Cost: ~35ms (about one frame) per click, acceptable.
        try:
            time.sleep_ms(BEEP_MS + 5)
            poll()
        except BaseException:
            pass
        return _cb(event)

    obj.add_event(_wrapped, lv.EVENT.CLICKED, user_data)
    return _wrapped


def bind(obj, code, user_data=None):
    """Attach the sound only, leaving the existing callback alone (for cases such
    as a keyboard whose widget raises events itself)."""
    def _wrapped(event):
        tap()

    obj.add_event(_wrapped, code, user_data)
    return _wrapped
