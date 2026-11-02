# Can the zh TTS frontend speak pinyin directly? Compare envelopes.
# Envelope = peak %FS per 100 ms, so a segment with no audio is visible.
import os
import sys

sys.path.insert(0, "/sdcard/factory/gui")
for _m in list(sys.modules):
    if _m.startswith("audio."):
        del sys.modules[_m]

import media.wave as wave
from audio import tts

out = []


def log(m):
    out.append(m)
    print(m)


VOICE = "/data/face/recognize/voice"


def envelope(path, step_ms=100):
    wf = wave.open(path, "rb")
    try:
        raw = wf.read_frames(1 << 20)
    finally:
        wf.close()
    chunk = 2400 * 2 if step_ms == 100 else 2400 * 2
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


cases = [
    ("你好", "c_zh"),
    ("你好，nina", "c_latin"),
    ("你好，ni na", "c_py_plain"),
    ("你好，nǐ nà", "c_py_tone"),
    ("你好，ní ná", "c_py_tone2"),
    ("你好，pinyin", "c_py_word"),
    ("你好，妮娜", "c_han"),
    ("你好，六共与", "c_map"),
]

synth = None
try:
    synth = tts.Synth()
    for text, tag in cases:
        p = VOICE + "/_c_" + tag + ".wav"
        try:
            size = synth.text_to_wav(text, p)
            env = envelope(p)
            voiced = [v for v in env if v >= 3]
            log("{} '{}' bytes={} segs={} voiced_segs={}".format(
                tag, text, size, len(env), len(voiced)))
            log("   env={}".format(env))
        except BaseException as e:
            log("{} '{}' FAILED: {}".format(tag, text, e))
        finally:
            try:
                os.remove(p)
            except BaseException:
                pass
finally:
    if synth is not None:
        synth.close()

out.append("PROBE_DONE")
f = open("/sdcard/_py.txt", "w")
f.write("\n".join(out))
f.close()
print("WROTE")
