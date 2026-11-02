# Why is the greeting quiet, and is the ASCII name actually voiced?
#   W1 peak/RMS of the existing greeting WAVs
#   W2 amplitude envelope of "你好，nina" so the name segment is visible
#   W3 compare with a Chinese transliteration
import os
import sys

sys.path.insert(0, "/sdcard/factory/gui")

out = []


def log(m):
    out.append(m)
    print(m)


VOICE = "/data/face/recognize/voice"
import media.wave as wave


def stats(path):
    wf = wave.open(path, "rb")
    try:
        rate = wf.get_framerate()
        raw = wf.read_frames(1 << 20)
    finally:
        wf.close()
    n = len(raw) // 2
    peak = 0
    acc = 0
    for i in range(0, len(raw) - 1, 2):
        v = raw[i] | (raw[i + 1] << 8)
        if v >= 32768:
            v -= 65536
        a = -v if v < 0 else v
        if a > peak:
            peak = a
        acc += a * a
    rms = (acc / n) ** 0.5 if n else 0
    return rate, n, peak, rms


log("W1 existing greeting levels")
for name in ("nina.wav", "tony.wav", "liuhongyu.wav"):
    p = VOICE + "/" + name
    try:
        rate, n, peak, rms = stats(p)
        log("  {} rate={} samples={} peak={} ({:.1f}%FS) rms={} ({:.1f}%FS)".format(
            name, rate, n, peak, 100.0 * peak / 32767.0,
            int(rms), 100.0 * rms / 32767.0))
    except BaseException as e:
        log("  {} failed: {}".format(name, e))


def envelope(path, step_ms=100):
    wf = wave.open(path, "rb")
    try:
        rate = wf.get_framerate()
        raw = wf.read_frames(1 << 20)
    finally:
        wf.close()
    chunk = int(rate * step_ms / 1000.0) * 2
    segs = []
    for o in range(0, len(raw), chunk):
        seg = raw[o:o + chunk]
        peak = 0
        for i in range(0, len(seg) - 1, 2):
            v = seg[i] | (seg[i + 1] << 8)
            if v >= 32768:
                v -= 65536
            a = -v if v < 0 else v
            if a > peak:
                peak = a
        segs.append(int(100.0 * peak / 32767.0))
    return segs


log("W2 envelope of nina.wav (peak %FS per 100ms)")
try:
    log("  " + str(envelope(VOICE + "/nina.wav")))
except BaseException as e:
    log("  failed: {}".format(e))

# W3: does a Chinese name produce more/different audio than an ASCII one?
from audio import tts

cases = [("你好", "zh_hi"),
         ("你好，nina", "zh_ascii"),
         ("你好，妮娜", "zh_cn")]
synth = None
try:
    synth = tts.Synth()
    for text, tag in cases:
        p = VOICE + "/_w3_" + tag + ".wav"
        try:
            size = synth.text_to_wav(text, p)
            rate, n, peak, rms = stats(p)
            env = envelope(p)
            log("W3 '{}' bytes={} samples={} peak={:.1f}%FS rms={:.1f}%FS".format(
                text, size, n, 100.0 * peak / 32767.0,
                100.0 * rms / 32767.0))
            log("   env={}".format(env))
        except BaseException as e:
            log("W3 '{}' failed: {}".format(text, e))
finally:
    if synth is not None:
        synth.close()

for tag in ("zh_hi", "zh_ascii", "zh_cn"):
    try:
        os.remove(VOICE + "/_w3_" + tag + ".wav")
    except BaseException:
        pass

out.append("PROBE_DONE")
f = open("/sdcard/_wv.txt", "w")
f.write("\n".join(out))
f.close()
print("WROTE")
