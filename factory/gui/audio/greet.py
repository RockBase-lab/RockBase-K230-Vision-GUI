# Greeting playback for Recognize: decides when to speak, then plays one WAV.
#
# The rules exist so a person standing in front of the camera is announced once,
# not on every frame. All thresholds are frame counts at the app's frame rate
# rather than milliseconds, because "one frame" is what the caller actually has;
# measured app rate is 25 fps with a face in view.
#
#   TRIGGER_FRAMES  the name must repeat for 8 frames (about 0.32 s) before it
#                   is spoken, which filters a one-frame misrecognition
#   LOST_FRAMES     a name is only re-armed after 10 frames (about 0.4 s) with
#                   no face, so detector flicker does not repeat the greeting
#   COOLDOWN_MS     the same name is not spoken again within 8 s
#
# Speech is never queued: while a WAV is playing, new triggers are dropped. A
# queue would make the app talk over itself after a group walks past.
#
# The stream is opened per greeting and closed in stop(). That is deliberate and
# bounded: a greeting happens a few times per session, not per sound effect, and
# stop_stream() costs a fixed 1095 ms, so it must not be in the steady loop.

import time

from audio import device

TRIGGER_FRAMES = 8
LOST_FRAMES = 10
COOLDOWN_MS = 8000

# Greeting WAVs are mono 24 kHz (audio/tts.py writes them at that rate).
VOICE_RATE = 24000
# Blocks written per pump() call at most. One block is 13 ms of audio while a
# recognition frame is 39 ms, so catching up needs about three; the cap only
# exists so a single frame cannot stall indefinitely.
MAX_PUMP_BLOCKS = 6


class Greeter:
    """Stateful trigger: feed it each frame's matched name, pump it to play."""

    def __init__(self, voice_dir):
        self.player = device.WavPlayer()
        self.voice_dir = voice_dir
        self._t0 = 0
        self.reset()

    def reset(self):
        self._name = None
        self._streak = 0
        self._lost = 0
        self._armed = True
        self._last_ms = {}
        self._missing = []
        self.active = False

    def take_missing(self):
        """Names whose WAV could not be opened since the last call, then clear.

        The caller rebuilds them: a greeting file can vanish (the board has no
        os.sync(), so a reset soon after writing can lose it), and staying
        silent forever would look like a broken feature.
        """
        if not self._missing:
            return None
        name = self._missing.pop(0)
        if not self._missing:
            self._missing = []
        return name

    def _path_for(self, name):
        return self.voice_dir + "/" + name + ".wav"

    def busy(self):
        return self.active

    def update(self, name, enabled):
        """Advance one frame. `name` is the matched identity or None.

        Returns True on the frame a greeting starts, so the caller can log it.
        """
        if not enabled:
            if self.active:
                self.stop()
            self._name = name
            return False

        if name is None:
            self._lost += 1
            if self._lost >= LOST_FRAMES:
                self._streak = 0
                self._name = None
                # Re-arm only after the face is really gone.
                self._armed = True
            return False

        self._lost = 0
        if name != self._name:
            self._name = name
            self._streak = 1
        else:
            self._streak += 1

        if not self._armed or self._streak != TRIGGER_FRAMES:
            return False
        if self.active:
            return False

        now = time.ticks_ms()
        last = self._last_ms.get(name)
        if last is not None and time.ticks_diff(now, last) < COOLDOWN_MS:
            return False

        try:
            self.player.start(gain=device.PLAY_GAIN,
                              path=self._path_for(name))
            # start() opens muted so the codec clock can stabilise behind
            # silence; without unmute() every pump() reports "data" forever and
            # the greeting never ends (measured: still busy after 12 s for a
            # 0.9 s clip).
            self.player.unmute()
        except BaseException as e:
            print("[Greet] open '{}' failed: {}".format(name, e))
            try:
                self.player.stop()
            except BaseException:
                pass
            if name not in self._missing:
                self._missing.append(name)
            return False

        self._last_ms[name] = now
        self._armed = False
        self.active = True
        self._t0 = now
        return True

    def pump(self):
        """Write enough blocks to keep audio level with the wall clock.

        One 320-frame block is only 13 ms of audio at 24 kHz, while a
        recognition frame is 39 ms, so pumping once per frame leaves the AO
        buffer starved (measured: a 0.92 s clip took 2.7 s to flush and would
        stutter). Writing until progress catches up fixes it; the cap keeps a
        single frame from stalling when a long AI frame put us far behind.
        """
        if not self.active:
            return
        for _ in range(MAX_PUMP_BLOCKS):
            elapsed = time.ticks_diff(time.ticks_ms(), self._t0) / 1000.0
            if self.player.frames / float(VOICE_RATE) >= elapsed:
                break
            if self.player.pump() == 0:
                self.stop()
                return

    def stop(self):
        self.player.stop()
        self.active = False
