# Pick the compression threshold for TTS by measurement: synthesise one phrase
# once, then apply the compressor at several thresholds and report the resulting
# RMS and crest factor. The recording path reaches RMS 24.3%FS at crest 1.98.
import math
import sys

sys.path.insert(0, "/sdcard/factory/gui")
for _m in list(sys.modules):
    if _m == "audio" or _m.startswith("audio."):
        del sys.modules[_m]

import ulab.numpy as np
from audio import tts

out = []


def log(m):
    out.append(m)
    print(m)


log("L0 raw TTS waveform, one phrase")
synth = None
try:
    synth = tts.Synth()
    text = "你好，逆纳"
    enc_in, dur = synth.enc.run(text)
    dec_out = synth.dec.run(enc_in)
    mel = synth.hifi.generate(dec_out, dur)
    clip = np.clip(np.array(mel[:dur * 256], dtype=np.float), -1.0, 1.0)
    peak = tts._peak_of(clip)
    log("L1 samples={} peak_abs={:.4f}".format(len(clip), peak))
    x0 = clip / peak

    def report(tag, y):
        n = len(y)
        ms = float(np.sum(y * y)) / n
        rms = math.sqrt(ms) if ms > 0 else 0.0
        pk = tts._peak_of(y)
        crest = (pk / rms) if rms else 0.0
        log("{} peak={:.1f}%FS rms={:.1f}%FS crest={:.2f}".format(
            tag, 100.0 * pk * 32767.0 / 32767.0, 100.0 * rms, crest))

    # Reference: no compression, pure normalisation (what the first version did).
    y = x0 * (tts.PEAK_FS / tts._peak_of(x0))
    report("L2 no-compress      ", y)

    for frac in (0.50, 0.35, 0.25, 0.18, 0.12, 0.08):
        x = np.array(x0)
        tts._compress(x, 160, frac, 64, 64)
        p2 = tts._peak_of(x)
        if p2 <= 1e-6:
            log("L3 frac={:.2f} silent".format(frac))
            continue
        y = x * (tts.PEAK_FS / p2)
        report("L3 frac={:.2f}         ".format(frac), y)
finally:
    if synth is not None:
        synth.close()

out.append("PROBE_DONE")
f = open("/sdcard/_frac.txt", "w")
f.write("\n".join(out))
f.close()
print("WROTE")
