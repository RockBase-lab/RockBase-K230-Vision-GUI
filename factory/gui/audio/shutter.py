# Camera shutter sound ("click-clack"): synthesized on board and played back
# through a long-lived stream, so no deployed audio asset is required.
#
# ---------------------------------------------------------------------------
# Measured on board (2026-10-27, 16k / 2ch / 320 frames) - read this before you
# change the playback timing
#
#   pa_enable          10ms
#   get_pa (first)     27ms
#   pa.open            47ms
#   write one block (20ms)  ~9ms (the first block after a cold start is slower)
#   write the whole 150ms effect 72ms
#   stop_stream      1095ms  <- fixed cost, independent of the bytes written and
#                               of how long the stream has lived
#   close               0ms
#
#   Two hard constraints follow:
#   1. **Do not open and close the stream per shot**. stop_stream's 1.1s would
#      stretch the time from pressing OK to the still image out to 1.8s. So the
#      stream is opened once on entering the camera app, reused for the whole
#      session, and closed once on exit (that 1.1s lands on the exit animation,
#      the same cost the Audio app already pays to stop playback).
#   2. **The close order must be stop_stream -> close**. Skipping stop_stream and
#      closing directly was measured to hard-hang the whole board: no response to
#      Ctrl-C / Ctrl-D, no echo at either baud rate, power cycle required.
#
#   Also measured: an open stream with no data fed to it (during the 500ms shot
#   block) is **silent** and produces no clicks or pops, so no background
#   silence blocks are needed between shots. Feeding silence instead would make
#   every write block for ~9ms, halving the camera main loop for no benefit.
#   Moving stop_stream into a thread does not help either: it does not release
#   the GIL, so the main thread's worst gap is still 1094ms.
# ---------------------------------------------------------------------------
#
# Resource policy: the first entry into the camera app synthesizes
# /data/audio/shutter.wav on board (about 4.8KB); every later entry reuses that
# file and **never deletes it**. It lives in /data (a large partition), so it
# survives re-entering the app and board restarts; only deleting it by hand
# triggers re-synthesis.
#
# Volume: normalized to SFX_PEAK_PCT%FS after synthesis. 35 rather than the 48
#   used for recorded playback - broadband transient noise sounds noticeably
#   louder than a sine or speech at the same peak, so 35%FS is already "crisp"
#   while keeping margin from the codec's analog saturation point (near 100%FS).
#
# Timbre: two bursts of band-passed noise. The first is stronger and decays more
#   slowly, matching the shutter curtain opening; the second is half as strong
#   and shorter, matching the curtain closing. All timbre parameters are by-ear
#   design values (no spectrum measurement was made); retuning the sound only
#   touches the constants below, the playback path stays unchanged.

import gc
import os
import sys
import time

from audio import device

# ---- resource location ----
SFX_DIR = "/data/audio"
SFX_NAME = "shutter.wav"

# ---- synthesis parameters ----
SFX_RATE = 16000              # same rate as recording/playback, path already proven
SFX_MS = 150                  # total duration
SFX_PEAK_PCT = 35             # peak as a percentage of full scale (see header)
# Two bursts: (start ms, amplitude, attack ms, decay tau ms)
SFX_BURSTS = (
    (0.0, 1.00, 1.2, 7.0),
    (48.0, 0.62, 0.9, 4.2),
)
SFX_LP = 0.85                 # first-order low-pass (about 4.8kHz), removes the high "hiss"
SFX_HP = 0.45                 # first-order high-pass feedback (about 1.4kHz), removes the low "thump"
SFX_FADE_MS = 6               # tail fade-out so the cut-off does not click

# ---- playback parameters ----
SFX_BLOCK = 320               # must == frames_per_buffer (20ms)
SFX_VOLUME = device.PLAY_VOLUME   # 95, matches playback calibration, do not tune separately
PA_SETTLE_MS = 50             # amplifier power-up wait, avoids a start-up pop (same as stream)

# AO open retry. Measured: after running Vision (sensor+KPU+Ai2d) first and then
# entering the camera, pa.open occasionally reports
# kd_mpi_ao_enable_chn failed:-1 (the same sequence does not reproduce reliably
# in a headless probe, so this is transient resource pressure rather than a
# permanently occupied channel). Each retry runs gc.collect and waits first so
# mpp has time to reclaim; total failure still leaves capture working, and every
# OK press retries anyway.
OPEN_RETRY_DELAYS_MS = (80, 240, 600)

# Muted through the Settings app (config key "camera.shutter_sound"). The camera
# calls set_muted() while constructing, before opening the stream, so a muted
# session never powers the amplifier. The WAV and PCM cache are kept, so turning
# the switch back on needs no re-synthesis.
MUTED = False


def set_muted(flag):
    global MUTED
    MUTED = bool(flag)


def muted():
    return MUTED

_SILENCE = b"\x00\x00\x00\x00" * SFX_BLOCK
_PCM_CACHE = None             # differential stereo PCM (module-level resident, kept after app exit)


def wav_path():
    return SFX_DIR + "/" + SFX_NAME


# =========================================================================
# Synthesis
# =========================================================================
def _synth():
    """Synthesize a mono s16 sequence and return bytes (little endian). The noise
    is deterministic, so every run produces the same result."""
    import math

    n = SFX_RATE * SFX_MS // 1000
    # Precompute the start indices instead of deriving them for every sample
    starts = []
    for burst in SFX_BURSTS:
        starts.append((int(burst[0] * SFX_RATE / 1000), burst[1], burst[2], burst[3]))

    vals = [0.0] * n
    lp = 0.0
    lp_prev = 0.0
    hp = 0.0
    seed = 0x2A5F13C7
    for i in range(n):
        env = 0.0
        for start, amp, attack_ms, decay_ms in starts:
            if i >= start:
                ms = (i - start) * 1000.0 / SFX_RATE
                e = amp * (1.0 - math.exp(-ms / attack_ms)) * math.exp(-ms / decay_ms)
                if e > env:
                    env = e
        # deterministic LCG -> [-1, 1)
        seed = (seed * 1103515245 + 12345) & 0x7FFFFFFF
        white = seed / 1073741824.0 - 1.0
        x = white * env
        lp += (x - lp) * SFX_LP
        y = lp - lp_prev + SFX_HP * hp     # first-order high-pass
        lp_prev = lp
        hp = y
        vals[i] = y

    peak = 0.0
    for v in vals:
        a = -v if v < 0 else v
        if a > peak:
            peak = a
    if peak <= 0.0:
        peak = 1.0
    k = 32767.0 * SFX_PEAK_PCT / 100.0 / peak

    fade_n = SFX_RATE * SFX_FADE_MS // 1000
    fade_from = n - fade_n
    out = bytearray(n * 2)
    j = 0
    for i in range(n):
        v = vals[i] * k
        if i >= fade_from:
            v *= (n - i) / float(fade_n)
        iv = int(v)
        if iv > 32767:
            iv = 32767
        elif iv < -32768:
            iv = -32768
        out[j] = iv & 0xFF
        out[j + 1] = (iv >> 8) & 0xFF
        j += 2
    return bytes(out)


def _write_wav(path, samples):
    """Write mono s16 as WAV. media.wave's close() backfills the RIFF length."""
    import media.wave as wave
    wf = wave.open(path, "wb")
    try:
        wf.set_channels(1)
        wf.set_sampwidth(2)
        wf.set_framerate(SFX_RATE)
        wf.write_frames_raw(samples)
    finally:
        try:
            wf.close()
        except BaseException:
            pass


def _read_wav(path):
    """Read back the mono s16 byte stream. Returns None on a format mismatch so
    the caller re-synthesizes."""
    import media.wave as wave
    wf = wave.open(path, "rb")
    try:
        if wf.get_channels() != 1 or wf.get_sampwidth() != 2 \
                or wf.get_framerate() != SFX_RATE:
            return None
        total = wf.get_frames()
        if not total or total < 0:
            return None
        return wf.read_frames(total)
    finally:
        try:
            wf.close()
        except BaseException:
            pass


def _stereo_diff(mono):
    """Mono s16 -> differential stereo (L=+s, R=-s), padded to a SFX_BLOCK boundary."""
    n = len(mono) // 2
    pad = (-n) % SFX_BLOCK
    out = bytearray((n + pad) * 4)
    j = 0
    for i in range(0, n * 2, 2):
        v = mono[i] | (mono[i + 1] << 8)
        if v >= 32768:
            v -= 65536
        r = -v
        if r > 32767:
            r = 32767
        elif r < -32768:
            r = -32768
        out[j] = v & 0xFF
        out[j + 1] = (v >> 8) & 0xFF
        out[j + 2] = r & 0xFF
        out[j + 3] = (r >> 8) & 0xFF
        j += 4
    return bytes(out)


def _file_ok(path):
    try:
        return os.stat(path)[6] > 44
    except OSError:
        return False


# =========================================================================
# Playback
# =========================================================================
class ShutterSfx:
    """Shutter sound: prepare() readies the data, open() opens the stream, play()
    writes one effect buffer, close() tears down.

    Typical use (camera app):
        __init__:   prepare(); open()     # open once, reuse for the session
        each shot:  play()                # PCM write only, about 72ms
        destroy:    close()               # stop_stream(1.1s) + close
    """

    def __init__(self):
        self._blocks = None       # pre-sliced 1280-byte blocks, avoids slicing per shot
        self._pa_pin = None
        self._stream = None
        self.fail = None

    # ---------- resources ----------
    def prepare(self):
        """Make sure the WAV exists and slice the differential stereo PCM into
        whole blocks (idempotent, about 60ms)."""
        global _PCM_CACHE
        if self._blocks is not None:
            return self._blocks
        if _PCM_CACHE is not None:
            self._set_pcm(_PCM_CACHE)
            return self._blocks

        path = wav_path()
        try:
            device.ensure_dir(SFX_DIR)
        except BaseException:
            pass

        mono = None
        if _file_ok(path):
            try:
                mono = _read_wav(path)
            except BaseException as error:
                print("[Sfx] read {} failed: {}".format(path, error))
                mono = None
        if mono is None:
            try:
                t0 = time.ticks_ms()
                samples = _synth()
                _write_wav(path, samples)
                gen_ms = time.ticks_diff(time.ticks_ms(), t0)
                mono = samples
                print("[Sfx] generated {} ({} bytes, {}ms)".format(
                    path, os.stat(path)[6], gen_ms))
            except BaseException as error:
                self.fail = error
                print("[Sfx] generate failed: {}".format(error))
                sys.print_exception(error)
                return None

        _PCM_CACHE = _stereo_diff(mono)
        self._set_pcm(_PCM_CACHE)
        print("[Sfx] ready: {} blocks x {} frames".format(
            len(self._blocks), SFX_BLOCK))
        return self._blocks

    def _set_pcm(self, pcm):
        step = SFX_BLOCK * 4
        self._blocks = [pcm[i:i + step] for i in range(0, len(pcm), step)]

    # ---------- stream ----------
    def open(self):
        """Open the amplifier and the output stream (idempotent). Failures are only
        recorded; capture keeps working."""
        if self._stream is not None:
            return True
        if self.prepare() is None:
            return False
        attempts = len(OPEN_RETRY_DELAYS_MS) + 1
        for i in range(attempts):
            if i:
                gc.collect()
                time.sleep_ms(OPEN_RETRY_DELAYS_MS[i - 1])
            if self._open_once():
                if i:
                    print("[Sfx] open ok on attempt {}/{}".format(i + 1, attempts))
                return True
            print("[Sfx] open attempt {}/{} failed: {!r} | free={}".format(
                i + 1, attempts, self.fail, gc.mem_free()))
        return False

    def _open_once(self):
        """One attempt: amplifier on -> output stream open. Cleans up in place on
        failure so no half-dead state is left."""
        from media.pyaudio import paInt16
        pin = None
        stream = None
        try:
            pin = device._pa_enable()
            if pin is not None:
                time.sleep_ms(PA_SETTLE_MS)
            pa = device._get_pa()     # shared instance, never terminate (root cause of AO leaks)
            stream = pa.open(format=paInt16, channels=2, rate=SFX_RATE,
                             output=True, frames_per_buffer=SFX_BLOCK)
            try:
                stream.volume(vol=SFX_VOLUME)
            except BaseException:
                pass
        except BaseException as error:
            self.fail = error
            if stream is not None:
                try:
                    stream.close()
                except BaseException:
                    pass
            device._pa_disable(pin)
            return False
        self._pa_pin = pin
        self._stream = stream
        self.fail = None
        return True

    def play(self):
        """Write the shutter sound once (about 72ms), opening the stream first if
        needed. Returns whether it succeeded."""
        if MUTED:
            return False
        if self._stream is None and not self.open():
            return False
        stream = self._stream
        try:
            stream.write(_SILENCE)      # prime one silent block, less chance of
                                        # first-block underrun
            for block in self._blocks:
                stream.write(block)
            return True
        except BaseException as error:
            # After a write failure the stream state cannot be trusted: tear it
            # down in the safe order so no half-dead stream is left behind
            self.fail = error
            print("[Sfx] play failed: {}".format(error))
            self.close()
            return False

    def close(self):
        """Close the stream in stop_stream -> close order (idempotent, fixed ~1.1s)."""
        stream = self._stream
        self._stream = None
        if stream is not None:
            try:
                stream.stop_stream()
            except BaseException as error:
                print("[Sfx] stop_stream error: {}".format(error))
            try:
                stream.close()
            except BaseException as error:
                print("[Sfx] close error: {}".format(error))
        pin = self._pa_pin
        self._pa_pin = None
        device._pa_disable(pin)
