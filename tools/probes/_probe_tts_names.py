# Regenerate every greeting with the pinyin conversion and verify that the name
# segment actually carries audio, and that the level reaches the red line.
import os
import sys

sys.path.insert(0, "/sdcard/factory/gui")
# The parent package keeps its own reference to the submodule, so dropping only
# "audio.tts" leaves `from audio import tts` returning the stale object. The
# whole package has to go.
for _m in list(sys.modules):
    if _m == "audio" or _m.startswith("audio."):
        del sys.modules[_m]

import media.wave as wave
from audio import tts

VOICE = "/data/face/recognize/voice"
NAMES = ["nina", "liuhongyu", "pony", "lily", "tony", "yanjing",
         "maomao", "xiaotong"]

out = []


def log(m):
    out.append(m)
    print(m)


def envelope(path):
    wf = wave.open(path, "rb")
    try:
        raw = wf.read_frames(1 << 20)
    finally:
        wf.close()
    segs = []
    peak_all = 0
    acc = 0
    n = 0
    for o in range(0, len(raw), 4800):
        seg = raw[o:o + 4800]
        peak = 0
        for i in range(0, len(seg) - 1, 2):
            v = seg[i] | (seg[i + 1] << 8)
            if v >= 32768:
                v -= 65536
            a = -v if v < 0 else v
            if a > peak:
                peak = a
            acc += a * a
            n += 1
        if peak > peak_all:
            peak_all = peak
        segs.append(int(100.0 * peak / 32767.0))
    rms = (acc / n) ** 0.5 if n else 0
    return segs, peak_all, rms


# Remove old greetings so every one is rebuilt with the new text path.
try:
    for f in os.listdir(VOICE):
        if f.endswith(".wav") and not f.startswith("_"):
            os.remove(VOICE + "/" + f)
except BaseException as e:
    log("clean failed: {}".format(e))

synth = None
try:
    synth = tts.Synth()
    for name in NAMES:
        text = tts.speak_text(name)
        p = VOICE + "/" + name + ".wav"
        try:
            size = synth.text_to_wav(text, p)
            segs, peak, rms = envelope(p)
            # The greeting prefix is the first ~3 segments; the tail is the name.
            tail = segs[4:]
            voiced_tail = [v for v in tail if v >= 3]
            log("{} '{}' bytes={} peak={:.1f}%FS rms={:.1f}%FS".format(
                name, text, size, 100.0 * peak / 32767.0,
                100.0 * rms / 32767.0))
            log("   env={} tail_voiced={}/{}".format(
                segs, len(voiced_tail), len(tail)))
        except BaseException as e:
            log("{} FAILED: {}".format(name, e))
finally:
    if synth is not None:
        synth.close()

out.append("PROBE_DONE")
f = open("/sdcard/_names.txt", "w")
f.write("\n".join(out))
f.close()
print("WROTE")
