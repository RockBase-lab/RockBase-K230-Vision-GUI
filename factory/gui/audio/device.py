# Audio capture/playback layer: PDM/DMIC recording, streaming WAV writes,
# differential stereo playback.
#
# This module **does not import lvgl**, so it can run headless on the board over
# the remote runner to verify the signal path before wiring up the UI (the Audio
# app's step() advances it one pump() at a time).
#
# ---------------------------------------------------------------------------
# Measured on board (RockBase / K230 CanMV v1.21, 2026-10)
#
# [How to open]
#   input_device_index=1 is mandatory. dev=0 raises
#   kd_mpi_ai_set_pub_attr failed:-1.
#   channels may be 2/4/6/8 and the group count is channels // 2.
#   channels=4 is chosen: only 2 groups, half the read cost of channels=8 (4
#   groups), and group 1 happens to be PDM_IN1 = the microphone.
#
# [Which group / which lane is the microphone]
#   GPIO28 = PDM_IN1 -> group 1. The microphone's L/R pin is tied high -> right
#   channel (lane 1).
#   Measured per-group peaks on board (s16 domain):
#       group0 = 32749 (floating saturated noise, PDM_IN0 unconnected)
#       group1 =  2063  <- the real microphone, about 6.3% of full scale
#       group2 = 32749 (floating)
#       group3 = 32749 (floating)
#   So **only group 1 lane 1 may be kept**; everything else is read and dropped.
#
# [All groups must be read]
#   The PDM hardware produces data for every group at once, and unread groups
#   pile up in the lower buffer until the whole capture pipeline jams. Hence
#   pump() calls read() for every group.
#
# [Data format]
#   paInt32, left aligned: the **high 16 bits** of a 32-bit sample are the s16
#   value.
#   Take lane1 of each frame (2 lanes x 4 bytes) = offsets 4..7, whose high 16
#   bits are byte[6],[7]. No shift and no multiply/divide is needed; two bytes
#   are taken directly (truncation error <= 1 LSB, about -67 dBFS after x28).
#
# [Timing]
#   frames_per_buffer=320 -> each read() blocks 20.0ms (hardware pace, not CPU)
#   frames_per_buffer=640 -> 40.0ms
#   320 is used: the main loop still runs at ~45fps while recording.
#   Conversion (per-frame Python loop plus peak scan) takes about 1ms for 320
#   frames, the WAV write about 1.1ms.
#
# [WAV]
#   media.wave's close() backfills the RIFF header correctly (measured:
#   set_frames to a large value, write only 1000 frames, and after close
#   data_size equals the real byte count). So recording can stream straight to
#   disk: an early STOP does not leave a file whose duration is the maximum, and
#   no manual header patch is needed.
#
# [PDM handle leak = power cycle required]
#   If an abnormal exit skips close/terminate, the next open raises
#   "kd_mpi_ai_set_pdm_clk failed:-1"; a soft reset (Ctrl-D) cannot reclaim that
#   native handle; mpp.ai.kd_mpi_ai_disable(dev) rescues it once and locks the
#   channel for good if called again.
#   Therefore every path must close the stream through try/finally -- see stop()
#   and abort().
# ---------------------------------------------------------------------------

import gc
import os
import time

# ---- PDM capture parameters (measured on board; measure again before changing) ----
PDM_CLK_PIN = 26
PDM_DATA_PIN = 28
AUDIO_DEV_INDEX = 1
CAPTURE_GROUP = 1
PDM_CHANNELS = 4
SAMPLE_RATE = 16000
CHUNK_FRAMES = 320            # 20ms
WARMUP_READS = 10             # discard PDM startup transients (including gain-switch glitches)

# ---- Passthrough switch (debugging) ----
# True = fully transparent chain: finalising skips compression/normalisation and the
#        noise gate (PCM is copied into the WAV verbatim), to compare against the raw
#        hardware level. False = normal chain (all processing below).
PASSTHROUGH = False

# =====================================================================
# [Hardware chain calibration -- oscilloscope measurements, 2026-10-27. Read this
# before touching any loudness parameter]
# Chain: digital samples --(vol=95 codec analog gain)--> codec differential out
#        --(power amp x12 BTL)--> speaker
#   . BTL differential = 2x single-ended; codec output = power amp input.
#   . 1kHz sweep measurement (vol=95):
#       50%FS  -> 360mV single-ended (linear region)
#                    => slope ~720mV differential/FS = 360mV single-ended/FS
#       100%FS -> 390mV single-ended with hard clipping
#                    => the codec's analog stage saturates at ~390mV
#                       (the first clipping point in the whole chain)
#   . User red line: the speaker does not saturate while the codec stays at
#     <=350mV single-ended (x12 amp, ~4.2V single-ended swing ceiling).
#   . Calibration: 350mV / 720mV/FS ~ 48.6% -> TARGET_PEAK_PCT=48 (with margin).
#       A digital peak of 48%FS gives ~346mV single-ended, ~692mV differential and
#       ~8.3V at the speaker, linear through both codec and amp.
#   . Key lesson: the old TARGET_PEAK_PCT=98 put 100%FS well past the codec's
#     ~390mV saturation point, so the codec was clipping the whole time (only on
#     peaks, so it sounded subtle). The digital peak is the only free variable for
#     loudness; setting it to the %FS that matches the red line is the "loud but
#     not clipped" solution, and playback must not add gain.
#   . On-board regression (real WavFinalizer): out peak=48.0%FS, clipped=0, RMS
#     +19.7dB versus raw.
# =====================================================================

# ---- Normalisation (lift each clip's peak to TARGET_PEAK_PCT%FS) ----
TARGET_PEAK_PCT = 48          # see the calibration above = the %FS matching the 350mV red line.
                              # Raising it clips the codec.
MAX_GAIN = 40.0               # normalisation gain ceiling (raw microphone peaks are
                              # often only ~5%FS and need large gain). At 40 the s16
                              # truncation error is ~-63dBFS, still well below the
                              # microphone noise floor.

# ---- Capture-side dynamic compression (raises speech average loudness, stays
# under the red line) ----
# Speech peak/RMS is about 2.9~3.4: peaks hit the ceiling while the average level
# is low, so it sounds "quiet". Finalising pulls loud blocks down and then
# normalises everything to TARGET_PEAK_PCT -- scale only, never reshape, so zero
# harmonics (no "hiss") and zero clipping.
#   Scan the peak every COMP_BLOCK samples; when a block peak exceeds
#   global peak x TARGET_FRAC, pull it down proportionally to that line (compress
#   only, never boost); gain falls instantly and rises rate-limited (release) to
#   avoid pumping; linear interpolation between blocks on apply, so there is no
#   boundary click.
#   Compression lowers the peak, so normalisation targets the "post-compression
#   peak" instead and still lands exactly on TARGET_PEAK_PCT.
COMP_ENABLED = True
COMP_BLOCK = 160              # 10ms
COMP_TARGET_FRAC = 0.50       # block peak target line = global peak x 0.50 (about 2:1, fuller voice)
COMP_RELEASE = 64             # max gain rise per block (fixed point 256=1.0, ~40ms recovery)
COMP_MIN_GAIN = 64            # compression floor 0.25, protects isolated strong transients

# Noise gate (per sample + hold): stretches below the threshold after normalisation
# are zeroed, removing the floor noise of silent parts.
NOISE_GATE_PCT = 2            # threshold as a percentage of s16 full scale, 0 = off
GATE_HOLD_SAMPLES = 800       # 50ms at 16kHz

# ---- Playback parameters ----
PA_CTRL_PIN = 71              # NS4150B enable (GPIO71; measured active high)
PLAY_VOLUME = 95              # codec analog gain 0..100 -> -43..+7 dB. **Calibration
                              # reference, do not change**: the mV/FS slope above was
                              # measured at vol=95, so changing it requires recalibration.
PLAY_GAIN = 1.0               # playback software gain. **Must stay 1.0**: finalising
                              # already calibrated the peak to 48%FS (= the 350mV red
                              # line), so anything >1.0 here pushes the signal past the
                              # codec's ~390mV saturation point -> clipped distortion
                              # (on-board A/B). Above 1.0 _to_diff_stereo enables the
                              # dynamic limiter as a safety net, but raising it is still
                              # not advised.
PLAY_CHUNK_FRAMES = 320
PREBUFFER_BLOCKS = 3          # prefill before start, prevents an underrun pop on the first block

# ---- Storage ----
AUDIO_DIR = "/data/audio"
WAV_NAME = "rec.wav"
PCM_NAME = "rec.pcm"          # capture intermediate (raw s16 stream, not normalised)


# =========================================================================
# Utilities
# =========================================================================
def ensure_dir(path):
    """CanMV has no os.path / os.makedirs, so mkdir level by level and ignore
    existing ones."""
    cur = ""
    for part in path.strip("/").split("/"):
        cur += "/" + part
        try:
            os.mkdir(cur)
        except OSError:
            pass


def wav_path():
    return AUDIO_DIR + "/" + WAV_NAME


def pcm_path():
    return AUDIO_DIR + "/" + PCM_NAME


def gain_for_peak(peak):
    """Normalise the peak to TARGET_PEAK_PCT of full scale, clamped to
    [1.0, MAX_GAIN]."""
    if peak <= 0:
        return 1.0
    gain = 32767.0 * TARGET_PEAK_PCT / 100.0 / peak
    if gain < 1.0:
        return 1.0
    if gain > MAX_GAIN:
        return MAX_GAIN
    return gain


def fmt_time(seconds):
    """Seconds -> M:SS (still M:SS beyond 10 minutes)."""
    s = int(seconds)
    if s < 0:
        s = 0
    return "{}:{:02d}".format(s // 60, s % 60)


def _init_pdm_pins():
    from machine import FPIOA
    fpioa = FPIOA()
    fpioa.set_function(PDM_CLK_PIN, FPIOA.PDM_CLK, oe=0x1, ie=0x0)
    fpioa.set_function(PDM_DATA_PIN, FPIOA.PDM_IN1, oe=0x0, ie=0x1)


# ---- Shared PyAudio instance (critical: avoid per-op create/terminate) ----
# The whole GUI session keeps **one** PyAudio instance and only opens/closes
# streams per operation. Previously every record/play/tone call did PyAudio() +
# terminate(), repeatedly enabling/disabling the mpp audio channel; on K230 that
# exhausts the AO channel into kd_mpi_ao_enable_chn failed:-1 with only a power
# cycle to recover. Sharing one instance per GUI session and only opening or
# closing streams avoids it.
_shared_pa = None


def _get_pa():
    global _shared_pa
    if _shared_pa is None:
        from media.pyaudio import PyAudio
        _shared_pa = PyAudio()
    return _shared_pa


# Playback dynamic limiting (per-block limiter).
#
# Why not a static curve: a recording at peak/RMS ~ 3.4 needs the peak pushed
# above the red line (the equivalent of 150%FS) to gain +6dB of RMS -- no static
# mapping escapes that, and it inevitably shapes loud passages into harmonics
# (measured: hard clipping = distortion, tanh = muffled, a compression curve with
# a knee = "hiss" as soon as the voice gets loud).
#
# Dynamic limiting scales per block without reshaping the waveform -> zero
# harmonic distortion:
#   each 20ms block finds its peak and the peak position,
#   g = min(gain, HEADROOM*CEIL/peak); within the block the previous gain ramps
#   down to g before the peak position (so the peak is never clipped), and gain
#   rises rate-limited between blocks (release); the final hard clamp is only a
#   safety net (a tiny overshoot during fast attacks).
LIMIT_HEADROOM = 0.97         # ceiling for peak x gain, leaving codec/PA margin
LIMIT_RELEASE = 0.25          # max gain rise per block (~80ms recovery, avoids pumping)


def _new_limiter(gain):
    return {"g": gain}


def _to_diff_stereo(pcm, gain, limiter=None):
    """Mono s16 -> differential stereo (L=+v, R=-v, required by a BTL bridged
    amp).

    The NS4150B on this board is wired BTL across HP_OUTL/HP_OUTR; in-phase
    channels cancel each other out (almost silent). So inverting the right channel
    is mandatory, not a sound-effect tweak.

    With gain > 1 and a limiter passed in, dynamic limiting applies (see above);
    otherwise it is purely linear.
    """
    n = len(pcm) // 2
    out = bytearray(n * 4)
    j = 0
    dyn = limiter is not None and gain > 1.0

    g_to = gain
    g_prev = gain
    peak_at = 0
    ramping = False
    dg = 0.0
    if dyn:
        # Pass 1: find the peak and its position
        peak = 0
        idx = 0
        for i in range(0, len(pcm) - 1, 2):
            v = pcm[i] | (pcm[i + 1] << 8)
            if v >= 32768:
                v -= 65536
            a = -v if v < 0 else v
            if a > peak:
                peak = a
                peak_at = idx
            idx += 1
        g_prev = limiter["g"]
        if peak > 0:
            maxg = 32767.0 * LIMIT_HEADROOM / peak
            if maxg < g_to:
                g_to = maxg
        if g_to > g_prev:                       # release: rate-limited rise
            g_to = g_prev + LIMIT_RELEASE
            if g_to > gain:
                g_to = gain
        limiter["g"] = g_to
        ramping = g_to < g_prev and peak_at > 0
        dg = (g_to - g_prev) / float(peak_at) if ramping else 0.0

    s = 0
    for i in range(0, len(pcm) - 1, 2):
        v = pcm[i] | (pcm[i + 1] << 8)
        if v >= 32768:
            v -= 65536
        if dyn:
            if ramping and s < peak_at:
                v = int(v * (g_prev + dg * s))
            else:
                v = int(v * g_to)
        else:
            v = int(v * gain)
        if v > 32767:                           # safety clamp (should rarely fire)
            v = 32767
        elif v < -32768:
            v = -32768
        r = -v
        if r > 32767:          # -v overflows s16 when v=-32768
            r = 32767
        out[j] = v & 0xFF
        out[j + 1] = (v >> 8) & 0xFF
        out[j + 2] = r & 0xFF
        out[j + 3] = (r >> 8) & 0xFF
        j += 4
        s += 1
    return out


def wav_info(path):
    """Read the WAV header, returning {"rate", "channels", "width", "frames"};
    None on failure.

    Note: CanMV's media.wave method names differ from the standard library -- it
    is `get_frames()`, not `get_nframes()` (measured on board).
    """
    import media.wave as wave
    wf = None
    try:
        wf = wave.open(path, "rb")
        rate = wf.get_framerate()
        channels = wf.get_channels()
        width = wf.get_sampwidth()
        frames = 0
        getter = getattr(wf, "get_frames", None)
        if getter is not None:
            try:
                frames = getter()
            except BaseException:
                frames = 0
        if not frames or frames < 0:
            # Fallback: read the internal field directly
            frames = getattr(wf, "_nframes", 0) or 0
        if not frames or frames < 0:
            # Last resort: derive from the file size
            try:
                frames = (os.stat(path)[6] - 44) // max(1, width * channels)
            except OSError:
                frames = 0
        return {"rate": rate, "channels": channels, "width": width,
                "frames": frames}
    except BaseException as error:
        print("[audio] wav_info failed: {}".format(error))
        return None
    finally:
        if wf is not None:
            try:
                wf.close()
            except BaseException:
                pass


def wav_seconds(path=None):
    """Read the WAV header for the duration without opening a stream."""
    info = wav_info(path or wav_path())
    if not info:
        return 0.0
    rate = info["rate"]
    if not rate or rate <= 0:
        return 0.0
    return info["frames"] / float(rate)


# =========================================================================
# Recording
# =========================================================================
class DmicRecorder:
    """Block-wise recording: each pump() reads 20ms of audio, removes DC and
    writes it into a temporary PCM file.

    Why a temporary file instead of writing the WAV directly: the peak is only
    known once recording ends and normalisation needs it. So write the raw s16
    stream first, and after stop() let WavFinalizer read it block by block, apply
    the gain, run the noise gate and write the final WAV. Memory stays constant
    throughout (640 bytes per block), so even 120s works.

    DC blocking happens during capture so the recorded peak is the true post-DC
    audio peak; otherwise DC inflates the peak by more than 2x and the
    normalisation gain is thrown away for nothing.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self._pa = None
        self._stream = None
        self._wf = None
        self._groups = 0
        self.frames = 0
        self.peak = 0
        self.last_peak = 0        # level of the previous block (s16 domain), for the UI meter
        self.seconds = 0.0
        self.path = None
        self.active = False

    # ---------- Lifecycle ----------
    def start(self):
        """Open PDM and create the temporary PCM. Failure leaves no half-open
        state."""
        self.reset()
        ensure_dir(AUDIO_DIR)
        path = pcm_path()
        # Delete stale files first, otherwise a failed recording passes the
        # previous take off as this one
        for p in (path, wav_path()):
            try:
                os.remove(p)
            except OSError:
                pass

        _init_pdm_pins()

        from media.pyaudio import paInt32

        pa = _get_pa()
        try:
            stream = pa.open(format=paInt32, channels=PDM_CHANNELS,
                             rate=SAMPLE_RATE, input=True,
                             frames_per_buffer=CHUNK_FRAMES,
                             input_device_index=AUDIO_DEV_INDEX)
        except BaseException:
            # Do not terminate the shared instance; a failed open has no stream to
            # close, keep PyAudio for reuse.
            raise

        self._pa = pa
        self._stream = stream
        self._groups = max(1, PDM_CHANNELS // 2)

        # 3A noise suppression stays off: measured, enable_audio3a(ANS) turns the
        # PDM signal into strong tonal noise/hum (peak up ~7x, crest 27) that buries
        # the voice; raw PDM data is the cleanest.

        # File handle: must be closed even on later failure (otherwise one handle
        # leaks)
        wf = None
        try:
            wf = open(path, "wb")
        except BaseException:
            self._release_stream()
            raise

        self._wf = wf
        self.path = path

        # Drop the startup transients; read every group as well so nothing piles up.
        # This can raise too (device fault) and must release as well.
        try:
            for _ in range(WARMUP_READS):
                for g in range(self._groups):
                    stream.read(chn=g)
        except BaseException:
            self.abort()
            raise

        self.active = True

    def pump(self):
        """Read one block of audio (blocks ~20ms), extract s16 and write it,
        returning the block's frame count."""
        if not self.active:
            return 0
        got = 0
        for g in range(self._groups):
            data = self._stream.read(chn=g)     # every group must be read
            if g != CAPTURE_GROUP or not data:
                continue
            pcm, peak = self._extract(data)
            self._wf.write(pcm)
            n = len(pcm) // 2
            self.frames += n
            self.last_peak = peak
            if peak > self.peak:
                self.peak = peak
            got = n
        if got:
            self.seconds = self.frames / float(SAMPLE_RATE)
        return got

    def _extract(self, data):
        """Take lane1's high 16 bits -> (s16 bytes, this block's peak).

        PDM is 32-bit left aligned, so the high 16 bits already are s16: take only
        the right channel of the microphone's group (byte 6 and 7 of each 8-byte
        frame). No filtering happens here; normalisation and the noise gate are
        left to WavFinalizer.
        """
        out = bytearray((len(data) // 8) * 2)
        peak = 0
        j = 0
        for i in range(4, len(data) - 3, 8):
            lo = data[i + 2]
            hi = data[i + 3]
            v = (hi << 8) | lo
            if v >= 32768:
                v -= 65536
            a = -v if v < 0 else v
            if a > peak:
                peak = a
            out[j] = lo
            out[j + 1] = hi
            j += 2
        return out, peak

    def stop(self):
        """Normal finish: close the stream and the temporary PCM.

        Returns {"frames", "seconds", "peak", "path"}; the caller then uses
        WavFinalizer to normalise the temporary PCM into the final WAV.
        """
        info = {
            "frames": self.frames,
            "seconds": self.seconds,
            "peak": self.peak,
            "path": self.path,
        }
        self.active = False
        error = None
        if self._wf is not None:
            try:
                self._wf.close()
            except BaseException as err:
                error = err
            self._wf = None
        self._release_stream()
        gc.collect()
        if error is not None:
            raise error
        return info

    def abort(self):
        """Failure path: close the temporary file (keeping what was recorded)
        and never leak the stream."""
        self.active = False
        if self._wf is not None:
            try:
                self._wf.close()
            except BaseException:
                pass
            self._wf = None
        self._release_stream()
        gc.collect()

    def _release_stream(self):
        # Close only this operation's stream (stop_stream then close, each
        # guarded), and **never terminate the shared PyAudio**. Per-op terminate is
        # one of the causes of repeated enable_chn failures on the AO/AI channels.
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
        self._pa = None          # shared instance: dropping the reference is enough


# =========================================================================
# Normalisation + noise gate (runs in blocks after recording)
# =========================================================================
class WavFinalizer:
    """Normalise the temporary raw s16 stream into the final WAV: per-sample gain
    -> clamp -> per-sample noise gate.

    The peak is normalised to TARGET_PEAK_PCT% of full scale (gain ceiling
    MAX_GAIN), and audio is muted once it stays below the threshold for more than
    GATE_HOLD_SAMPLES samples. Block-wise streaming keeps the heap flat even for
    120s, and each step() spends only budget_ms milliseconds, driven frame by frame
    from the main loop so the UI can show "processing".
    """

    def __init__(self, peak=None, gain=None):
        self.src = None
        self.dst = None
        self.total = 0
        self.done = 0
        self.gain = 1.0           # normalisation gain
        self.gate_level = 0
        self.finished = False
        self.error = None
        self._peak = peak
        self._gain_arg = gain
        self._below = 0           # consecutive samples below the gate threshold (kept across blocks)
        self._cg = None           # compression gain track (array('H'), fixed point 256=1.0)
        self._off = 0             # offset of the current sample inside the compression block
        self._bidx = 0            # index of the current compression block
        self._cg_prev = 256
        self._cg_cur = 256

    def start(self):
        """Open the source PCM and the destination WAV."""
        import media.wave as wave

        ensure_dir(AUDIO_DIR)
        src = pcm_path()
        try:
            self.total = os.stat(src)[6]
        except OSError:
            self.total = 0
        if self.total < 2:
            raise ValueError("no recorded audio")

        peak = self._peak
        if peak is None:
            peak = self._scan_peak(src)
        self._peak = peak
        if PASSTHROUGH:
            # Passthrough: no compression, no normalisation, no gate. In step()
            # gain=1.0, cg=None and gate=0, so every sample is v=int(v*1.0)=v,
            # equivalent to copying the PCM into the WAV.
            self._cg = None
            self.gain = 1.0
            self.gate_level = 0
        else:
            if COMP_ENABLED and self._gain_arg is None:
                self._cg = self._scan_comp_gains(peak)
                norm_peak = int(peak * COMP_TARGET_FRAC)
                if norm_peak < 1:
                    norm_peak = 1
            else:
                self._cg = None
                norm_peak = peak
            self.gain = self._gain_arg if self._gain_arg is not None \
                else gain_for_peak(norm_peak)
            self.gate_level = int(32767 * NOISE_GATE_PCT / 100) if NOISE_GATE_PCT else 0

        self.src = open(src, "rb")
        wf = wave.open(wav_path(), "wb")
        try:
            wf.set_channels(1)
            wf.set_sampwidth(2)
            wf.set_framerate(SAMPLE_RATE)
        except BaseException:
            try:
                wf.close()
            except BaseException:
                pass
            raise
        self.dst = wf
        self.done = 0
        return self

    def _scan_comp_gains(self, peak):
        """Pass 1: scan every block's peak and build the compression gain track
        (fixed point 256=1.0, compress only)."""
        from array import array
        blk = COMP_BLOCK
        target = peak * COMP_TARGET_FRAC
        f = open(pcm_path(), "rb")
        gains = array("H")
        buf = bytearray(blk * 2)
        g_prev = 256
        try:
            while True:
                n = f.readinto(buf)
                if not n:
                    break
                pk = 0
                for i in range(0, n - 1, 2):
                    v = buf[i] | (buf[i + 1] << 8)
                    if v >= 32768:
                        v -= 65536
                    a = -v if v < 0 else v
                    if a > pk:
                        pk = a
                if pk > target and pk > 0:
                    required = int(target * 256.0 / pk)
                    if required < COMP_MIN_GAIN:
                        required = COMP_MIN_GAIN
                else:
                    required = 256
                g = required
                if g > g_prev:                    # release: rate-limited rise, never above what this block needs
                    g = g_prev + COMP_RELEASE
                    if g > 256:
                        g = 256
                    if g > required:
                        g = required
                gains.append(g)
                g_prev = g
        finally:
            f.close()
        return gains

    def _scan_peak(self, path):
        """Scan the peak ourselves when none was given (rarely used, last resort)."""
        f = open(path, "rb")
        buf = bytearray(2048)
        peak = 0
        try:
            while True:
                n = f.readinto(buf)
                if not n:
                    break
                for i in range(0, n - 1, 2):
                    v = buf[i] | (buf[i + 1] << 8)
                    if v >= 32768:
                        v -= 65536
                    a = -v if v < 0 else v
                    if a > peak:
                        peak = a
        finally:
            f.close()
        return peak

    def progress(self):
        if not self.total:
            return 1.0
        p = self.done * 1.0 / self.total
        return 1.0 if p > 1.0 else p

    # Per-sample processing; smaller chunks make step() more granular (about
    # 2048 samples per block ~ 4ms of work) so no single frame stalls too long.
    _CHUNK = 4096

    def step(self, budget_ms=20):
        """Process a slice (per-sample gain plus noise gate). Returns True when
        all done."""
        if self.finished:
            return True
        if self.src is None:
            raise ValueError("finalizer not started")

        t0 = time.ticks_ms()
        buf = bytearray(self._CHUNK)
        gain = self.gain
        gate = self.gate_level
        hold = GATE_HOLD_SAMPLES
        below = self._below
        cg = self._cg
        blk = COMP_BLOCK
        if cg is not None:
            bidx = self._bidx
            off = self._off
            cg_prev = self._cg_prev
            cg_cur = self._cg_cur
        while True:
            n = self.src.readinto(buf)
            if not n:
                self._below = below
                self._close(out_ok=True)
                return True
            if n & 1:                       # s16 alignment: drop a trailing odd byte
                n -= 1
            out = bytearray(n)
            for i in range(0, n, 2):
                v = buf[i] | (buf[i + 1] << 8)
                if v >= 32768:
                    v -= 65536
                if cg is not None:
                    if off == 0:
                        # Entering a new block: start = previous block's end,
                        # end = this block's target.
                        cg_prev = cg_cur
                        cg_cur = cg[bidx] if bidx < len(cg) else cg_cur
                        bidx += 1
                    if cg_cur > cg_prev:
                        # release (gain rising): linear transition inside the
                        # block, prevents a boundary click
                        gfix = cg_prev + (cg_cur - cg_prev) * off // blk
                    else:
                        # attack (gain falling): applies immediately so a loud block
                        # is already below the target line from its first sample --
                        # otherwise the attack transients clip ("hiss").
                        gfix = cg_cur
                    off += 1
                    if off >= blk:
                        off = 0
                    v = int(v * gain * (gfix / 256.0))
                else:
                    v = int(v * gain)
                if v > 32767:
                    v = 32767
                elif v < -32768:
                    v = -32768
                if gate:
                    a = -v if v < 0 else v
                    if a < gate:
                        below += 1
                        if below > hold:
                            v = 0
                    else:
                        below = 0
                out[i] = v & 0xFF
                out[i + 1] = (v >> 8) & 0xFF
            self.dst.write_frames_raw(out)
            self.done += n
            if time.ticks_diff(time.ticks_ms(), t0) >= budget_ms:
                break
        self._below = below
        if cg is not None:
            self._bidx = bidx
            self._off = off
            self._cg_prev = cg_prev
            self._cg_cur = cg_cur
        return False

    def run_all(self, budget_ms=100000):
        """Run to completion synchronously (headless tests / final fallback)."""
        while not self.step(budget_ms):
            pass
        return self

    def _close(self, out_ok):
        if not self.finished:
            self.finished = True
        wf = self.dst
        self.dst = None
        if wf is not None:
            try:
                wf.close()          # media.wave backfills the RIFF length
            except BaseException as error:
                self.error = error
        f = self.src
        self.src = None
        if f is not None:
            try:
                f.close()
            except BaseException:
                pass
        if out_ok:
            try:
                os.remove(pcm_path())
            except OSError:
                pass
        gc.collect()
        return out_ok

    def abort(self):
        """Clean up on error: close the handles and delete the half-written WAV."""
        self.finished = True
        self._close(out_ok=False)
        try:
            os.remove(wav_path())
        except OSError:
            pass


def finalize_sync(peak=None, gain=None):
    """One-call convenience entry point (for headless scripts)."""
    return WavFinalizer(peak, gain).start().run_all()


# =========================================================================
# Playback
# =========================================================================
class WavPlayer:
    """Stream rec.wav: each pump() writes one block (the blocking write naturally
    follows the hardware pace).

    Recording already calibrated the WAV to TARGET_PEAK_PCT%FS (= the 350mV red
    line, see the calibration block at the top), so gain here must stay 1.0.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self._pa = None
        self._stream = None
        self._wf = None
        self._pa_pin = None
        self.frames = 0
        self.total = 0
        self.gain = 1.0
        self.muted = False
        self.active = False

    def start(self, gain=1.0, muted=True):
        """Open the WAV and the output stream.

        muted=True: prefill silence only, then call unmute() when playback really
        starts. The switch exists for a future "pause" (keep writing silent blocks
        to hold the codec clock).
        """
        self.reset()
        import media.wave as wave

        # Try already during header reading: any failure in get_framerate etc. must
        # still close the handle
        wf = None
        try:
            wf = wave.open(wav_path(), "rb")
            rate = wf.get_framerate()
            width = wf.get_sampwidth()
            channels = wf.get_channels()
            # CanMV's media.wave has get_frames(), not get_nframes()
            total = 0
            getter = getattr(wf, "get_frames", None)
            if getter is not None:
                try:
                    total = getter()
                except BaseException:
                    total = 0
            if not total or total < 0:
                total = getattr(wf, "_nframes", 0) or 0
            if not total or total < 0:
                try:
                    total = (os.stat(wav_path())[6] - 44) // max(1, width * channels)
                except OSError:
                    total = 0
        except BaseException:
            if wf is not None:
                try:
                    wf.close()
                except BaseException:
                    pass
            raise
        self._wf = wf
        self.total = total
        self.gain = gain
        self.muted = muted
        self._limiter = _new_limiter(gain)

        stream, pin = None, None
        try:
            pin = _pa_enable()
            if pin is not None:
                time.sleep_ms(50)      # let the amp power up, prevents a startup "pop"
            from media.pyaudio import paInt16
            pa = _get_pa()             # shared PyAudio instance, never terminated per op
            stream = pa.open(format=paInt16, channels=2, rate=rate, output=True,
                             frames_per_buffer=PLAY_CHUNK_FRAMES)
            try:
                stream.volume(vol=PLAY_VOLUME)
            except BaseException:
                pass
            # Prefill silence to reduce the chance of a first-block underrun
            silence = b"\x00\x00\x00\x00" * PLAY_CHUNK_FRAMES
            for _ in range(PREBUFFER_BLOCKS):
                stream.write(silence)
        except BaseException:
            if stream is not None:
                try:
                    stream.close()
                except BaseException:
                    pass
            _pa_disable(pin)
            try:
                wf.close()
            except BaseException:
                pass
            self.reset()
            raise

        self._pa = pa
        self._stream = stream
        self._pa_pin = pin
        self.active = True

    def unmute(self):
        self.muted = False

    def pump(self):
        """Write one block, returning its frame count; 0 means finished (or
        inactive)."""
        if not self.active:
            return 0
        if self.muted:
            return 1        # pretend there is data: paused means no progress and no end
        pcm = self._wf.read_frames(PLAY_CHUNK_FRAMES)
        if not pcm:
            return 0
        n = len(pcm) // 2
        self._stream.write(_to_diff_stereo(pcm, self.gain, self._limiter))
        self.frames += n
        return n

    def stop(self):
        """Append 2 silence blocks before closing to avoid a shutdown pop."""
        self.active = False
        stream = self._stream
        if stream is not None:
            try:
                silence = b"\x00\x00\x00\x00" * PLAY_CHUNK_FRAMES
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
        self._stream = None
        self._pa = None          # shared PyAudio: never terminate, just drop the reference
        pin = self._pa_pin
        self._pa_pin = None
        _pa_disable(pin)
        wf = self._wf
        self._wf = None
        if wf is not None:
            try:
                wf.close()
            except BaseException:
                pass
        gc.collect()

    def seconds(self):
        return self.frames / float(SAMPLE_RATE)

    def total_seconds(self):
        return self.total / float(SAMPLE_RATE)


def _pa_enable():
    """Raise the amp enable pin. Returns a Pin or None (an unavailable Pin does
    not break audio itself)."""
    try:
        from machine import FPIOA, Pin
        name = "GPIO{}".format(PA_CTRL_PIN)
        func = getattr(FPIOA, name, None)
        if func is not None:
            fpioa = FPIOA()
            fpioa.set_function(PA_CTRL_PIN, func)
        pin = Pin(PA_CTRL_PIN, Pin.OUT, pull=Pin.PULL_NONE, drive=7)
        pin.value(1)
        return pin
    except BaseException as error:
        print("[audio] PA enable failed: {}".format(error))
        return None


def _pa_disable(pin):
    if pin is None:
        return
    try:
        pin.value(0)
    except BaseException:
        pass
