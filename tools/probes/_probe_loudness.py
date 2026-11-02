# Compare the recorded audio (which the user says is loud enough) with a
# greeting WAV: same peak, very different RMS means the recording path applies
# dynamic compression and the TTS path does not.
import os

import media.wave as wave

out = []


def log(m):
    out.append(m)
    print(m)


def stats(path):
    wf = wave.open(path, "rb")
    try:
        rate = wf.get_framerate()
        ch = wf.get_channels()
        raw = wf.read_frames(1 << 21)
    except BaseException:
        wf.close()
        raise
    wf.close()
    n = 0
    peak = 0
    acc = 0.0
    for i in range(0, len(raw) - 1, 2):
        v = raw[i] | (raw[i + 1] << 8)
        if v >= 32768:
            v -= 65536
        a = -v if v < 0 else v
        if a > peak:
            peak = a
        acc += a * a
        n += 1
    rms = (acc / n) ** 0.5 if n else 0.0
    crest = (peak / rms) if rms else 0.0
    return rate, ch, n, peak, rms, crest


log("C1 recorded audio (reference the user says is loud enough)")
for p in ("/data/audio/rec.wav", "/data/audio/rec0.wav"):
    try:
        rate, ch, n, peak, rms, crest = stats(p)
        log("  {} rate={} ch={} n={}".format(p, rate, ch, n))
        log("     peak={:.1f}%FS rms={:.1f}%FS crest={:.2f}".format(
            100.0 * peak / 32767.0, 100.0 * rms / 32767.0, crest))
    except BaseException as e:
        log("  {} -> {}".format(p, e))

log("C2 greeting WAVs")
VOICE = "/data/face/recognize/voice"
try:
    names = sorted(os.listdir(VOICE))
except BaseException:
    names = []
for nm in names:
    p = VOICE + "/" + nm
    try:
        rate, ch, n, peak, rms, crest = stats(p)
        log("  {} rate={} ch={}".format(nm, rate, ch))
        log("     peak={:.1f}%FS rms={:.1f}%FS crest={:.2f}".format(
            100.0 * peak / 32767.0, 100.0 * rms / 32767.0, crest))
    except BaseException as e:
        log("  {} -> {}".format(nm, e))

log("C3 finaliser settings in use")
try:
    import sys
    sys.path.insert(0, "/sdcard/factory/gui")
    from audio import device
    log("  COMP_ENABLED={} FRAC={} BLOCK={} RELEASE={} MIN_GAIN={}".format(
        device.COMP_ENABLED, device.COMP_TARGET_FRAC, device.COMP_BLOCK,
        device.COMP_RELEASE, device.COMP_MIN_GAIN))
    log("  TARGET_PEAK_PCT={} NOISE_GATE_PCT={} PLAY_VOLUME={} PLAY_GAIN={}".format(
        device.TARGET_PEAK_PCT, device.NOISE_GATE_PCT, device.PLAY_VOLUME,
        device.PLAY_GAIN))
except BaseException as e:
    log("  settings failed: {}".format(e))

out.append("PROBE_DONE")
f = open("/sdcard/_loud.txt", "w")
f.write("\n".join(out))
f.close()
print("WROTE")
