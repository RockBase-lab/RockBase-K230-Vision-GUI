# Single-frequency sine generator (used by the Audio app's sweep page to measure
# the codec -> NS4150B amplifier path quantitatively).
#
# Shipped as a module of its own so the tone path stays measurable at any time.
# Output is differential stereo (L=+s, R=-s, required by the BTL stage) at a 44100
# sample rate (8kHz would have enough headroom), with the codec volume pinned to
# TONE_VOLUME - the analog gain is part of the path, so a quantitative test has
# to hold it fixed.
#
# [Amplitude -> voltage calibration] see FS_MV/AMP_MAX_MV below: oscilloscope
#   measurements taken 2026-10-27, where the panel mV reading is the real
#   differential voltage at the amplifier input. The full-chain calibration is in
#   the header block of the stream module.
#
# Real time: same as WavPlayer, step() calls pump() every frame to adaptively
# write until playback progress catches up with the wall clock, otherwise AO
# underruns (choppy audio / pops). start/stop are strictly paired and never leak
# an output stream (a leak can only be recovered by a power cycle).

import math
import time

from audio import device


TONE_RATE = 44100
# [Block length must == frames_per_buffer, do not change] Learned the hard way:
# shrinking the block (220) or growing frames_per_buffer (2205) leaves the driver
# short of a full buffer -> underrun gaps -> the speaker stutters "da-da-da"
# (measured with a silent probe: with fpb=2205, writing 220-byte blocks blocks
# for 50ms per write). 882 is the value measured to play continuously.
TONE_BLOCK = 882              # ~20ms @44100, matches the write blocking rhythm (= frames_per_buffer)
TONE_VOLUME = 95              # codec analog gain (fixed; = the settled playback value)
PREBUFFER_BLOCKS = 3

FREQ_MIN = 100.0
FREQ_MAX = 8000.0
# [Measured 2026-10-27] with vol=95: 50%FS -> 360mV per side -> differential full
# scale ~= 1440mV. Amplifier x12 red line: <=350mV per side -> <=700mV
# differential (=48.6%FS). The slider ceiling is that red line, so the panel mV
# reading is now the real differential input voltage and doubles as a
# verification tool.
AMP_MAX_MV = 700.0            # differential peak ceiling (= 350mV per-side red line x2)
FS_MV = 1440.0                # differential peak mV at digital full scale (measured)


def amp_digital(mv):
    """Target mV -> digital peak. Returns (digital, clamped)."""
    d = int(round(mv / FS_MV * 32767.0))
    if d > 32767:
        return 32767, True
    return d, False


class ToneOut:
    """Continuous sine output: phase accumulation + differential stereo, adaptively
    writing to track the wall clock."""

    def __init__(self):
        self.active = False
        self.fail = None
        self._pa = None
        self._stream = None
        self._pin = None
        self.phase = 0.0
        self.frames_written = 0
        self._t0 = 0
        self.freq = 1000.0
        self.amp = 0.0            # current actual amplitude (digital peak, ramping toward target)
        self.amp_target = 0.0
        self.blocks_written = 0   # diagnostics: total blocks written

    def start(self):
        self.stop()
        self.fail = None
        from media.pyaudio import paInt16
        pin = device._pa_enable()
        if pin is not None:
            time.sleep_ms(50)     # wait for amplifier power-up, avoids a start-up "pop"
        stream = None
        try:
            pa = device._get_pa()   # shared PyAudio instance, never terminated per op (root cause of AO leaks)
            stream = pa.open(format=paInt16, channels=2, rate=TONE_RATE,
                             output=True, frames_per_buffer=TONE_BLOCK)
            try:
                stream.volume(vol=TONE_VOLUME)
            except BaseException:
                pass
            silence = b"\x00\x00\x00\x00" * TONE_BLOCK
            for _ in range(PREBUFFER_BLOCKS):
                stream.write(silence)
        except BaseException:
            if stream is not None:
                try:
                    stream.stop_stream()
                    stream.close()
                except BaseException:
                    pass
            device._pa_disable(pin)
            raise
        self._pa = pa
        self._stream = stream
        self._pin = pin
        self.phase = 0.0
        self.frames_written = 0
        self.blocks_written = 0
        self.amp = self.amp_target
        self._t0 = time.ticks_ms()
        self.active = True

    def stop(self):
        self.active = False
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                silence = b"\x00\x00\x00\x00" * TONE_BLOCK
                for _ in range(2):
                    stream.write(silence)
            except BaseException:
                pass
            try:
                stream.stop_stream()
            except BaseException:
                pass
            try:
                stream.close()
            except BaseException:
                pass
        self._pa = None          # shared PyAudio: dropping the reference is enough, never terminate
        device._pa_disable(self._pin)
        self._pin = None

    def _gen_block(self):
        # Ramp the amplitude toward the target (at most 8% per block) so dragging
        # the slider does not click or step
        tgt = self.amp_target
        if self.amp < tgt:
            self.amp = min(tgt, self.amp + tgt * 0.08 + 1.0)
        elif self.amp > tgt:
            self.amp = max(tgt, self.amp - (self.amp * 0.08 + 1.0))
        amp = self.amp
        inc = self.freq / TONE_RATE
        phase = self.phase
        sin = math.sin
        two_pi = 6.283185307179586
        out = bytearray(TONE_BLOCK * 4)
        j = 0
        for _ in range(TONE_BLOCK):
            phase += inc
            if phase >= 1.0:
                phase -= math.floor(phase)
            v = int(amp * sin(two_pi * phase))
            if v > 32767:
                v = 32767
            elif v < -32768:
                v = -32768
            r = -v
            if r > 32767:
                r = 32767
            out[j] = v & 0xFF
            out[j + 1] = (v >> 8) & 0xFF
            out[j + 2] = r & 0xFF
            out[j + 3] = (r >> 8) & 0xFF
            j += 4
        self.phase = phase
        return bytes(out)

    def pump(self):
        """Keep writing until audio progress catches up with the wall clock (at
        most 4 blocks per call, so a single frame cannot stall for long)."""
        if not self.active:
            return
        for _ in range(4):
            elapsed = time.ticks_diff(time.ticks_ms(), self._t0) / 1000.0
            if self.frames_written / float(TONE_RATE) >= elapsed:
                break
            try:
                self._stream.write(self._gen_block())
            except BaseException as error:
                # Tear the stream down here on a write failure: otherwise the AO
                # channel leaks when the session never exits.
                self.fail = "write failed: {}".format(error)
                self.active = False
                stream = self._stream
                self._stream = None
                if stream is not None:
                    try:
                        stream.stop_stream()
                    except BaseException:
                        pass
                    try:
                        stream.close()
                    except BaseException:
                        pass
                self._pa = None
                device._pa_disable(self._pin)
                self._pin = None
                return
            self.frames_written += TONE_BLOCK
            self.blocks_written += 1
