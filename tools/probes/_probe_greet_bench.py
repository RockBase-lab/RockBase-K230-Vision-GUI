# Where does greeting playback spend its time: the stereo conversion, the WAV
# read, or the blocking stream write?
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _m in list(sys.modules):
    if _m.startswith("audio."):
        del sys.modules[_m]

out = []


def log(m):
    out.append(m)
    print(m)


from audio import device
import media.wave as wave

WAV = "/data/face/recognize/voice/_bench.wav"

# Build a 1-second 24 kHz mono WAV directly so this probe does not need TTS.
import ulab.numpy as np
pcm = bytes(48000)
wf = wave.open(WAV, "wb")
try:
    wf.set_channels(1)
    wf.set_sampwidth(2)
    wf.set_framerate(24000)
    wf.write_frames(pcm)
finally:
    wf.close()
log("B0 built 1s wav {}".format(len(pcm)))

# --- conversion cost alone
blk = bytes(640)
t0 = time.ticks_ms()
for _ in range(10):
    device._to_diff_stereo(blk, 1.0, None)
log("B1 _to_diff_stereo x10 = {}ms ({:.2f}ms/block)".format(
    time.ticks_diff(time.ticks_ms(), t0),
    time.ticks_diff(time.ticks_ms(), t0) / 10.0))

# --- read cost
wf = wave.open(WAV, "rb")
t0 = time.ticks_ms()
n = 0
while True:
    d = wf.read_frames(320)
    if not d:
        break
    n += 1
wf.close()
log("B2 read {} blocks of 320 = {}ms".format(
    n, time.ticks_diff(time.ticks_ms(), t0)))

# --- write cost through the real player
p = device.WavPlayer()
t0 = time.ticks_ms()
p.start(gain=1.0, path=WAV)
p.unmute()
open_ms = time.ticks_diff(time.ticks_ms(), t0)
log("B3 start+unmute={}ms".format(open_ms))

marks = []
t0 = time.ticks_ms()
blocks = 0
while True:
    t1 = time.ticks_ms()
    r = p.pump()
    marks.append(time.ticks_diff(time.ticks_ms(), t1))
    if r == 0:
        break
    blocks += 1
    if blocks >= 40:
        break
total = time.ticks_diff(time.ticks_ms(), t0)
log("B4 pumped {} blocks in {}ms -> {:.2f}ms/block, frames={}".format(
    blocks, total, total / float(blocks), p.frames))
log("B5 first 8 block times={}".format(marks[:8]))
p.stop()
log("B6 stopped")

import os
try:
    os.remove(WAV)
except BaseException:
    pass
out.append("PROBE_DONE")
f = open("/sdcard/_bench.txt", "w")
f.write("\n".join(out))
f.close()
print("WROTE")
