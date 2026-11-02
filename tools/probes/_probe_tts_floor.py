# The block compressor plateaus near 10.6%FS RMS because COMP_MIN_GAIN (floor
# 0.25) stops it crushing the loudest blocks, which caps the makeup gain after
# normalisation. Sweep the floor to see whether a lower one buys loudness.
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


synth = None
try:
    synth = tts.Synth()
    enc_in, dur = synth.enc.run("你好，逆纳")
    dec_out = synth.dec.run(enc_in)
    mel = synth.hifi.generate(dec_out, dur)
    clip = np.clip(np.array(mel[:dur * 256], dtype=np.float), -1.0, 1.0)
    x0 = clip / tts._peak_of(clip)
    log("F0 samples={}".format(len(x0)))

    def report(tag, y):
        n = len(y)
        ms = float(np.sum(y * y)) / n
        rms = math.sqrt(ms) if ms > 0 else 0.0
        pk = tts._peak_of(y)
        log("{} peak={:.0f}%FS rms={:.1f}%FS crest={:.2f}".format(
            tag, 100.0 * pk, 100.0 * rms, (pk / rms) if rms else 0.0))

    report("F1 raw normalised   ", x0 * (tts.PEAK_FS / tts._peak_of(x0)))

    for frac in (0.30, 0.25):
        for floor in (64, 48, 32, 16):
            x = np.array(x0)
            tts._compress(x, 160, frac, 64, floor)
            p2 = tts._peak_of(x)
            y = x * (tts.PEAK_FS / p2) if p2 > 1e-6 else x
            report("F2 frac={:.2f} floor={:3d}".format(frac, floor), y)
finally:
    if synth is not None:
        synth.close()

out.append("PROBE_DONE")
f = open("/sdcard/_floor.txt", "w")
f.write("\n".join(out))
f.close()
print("WROTE")
