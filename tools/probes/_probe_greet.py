# Verify the greeting feature end to end without the GUI:
#   G1 Synth test: text -> WAV at the configured peak
#   G2 Greeter trigger matrix: arrival speaks once, staying silent, leave+return
#   G3 Playback: the WAV is audible through the differential stereo path
#   G4 Cleanup: no stream left open
import gc
import os
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _m in list(sys.modules):
    if _m.startswith("audio.") or _m.startswith("apps."):
        del sys.modules[_m]

out = []


def log(m):
    out.append(m)
    print(m)


from audio import tts
from audio import greet

VOICE = tts.VOICE_DIR
name = "zzprobe"
path = VOICE + "/" + name + ".wav"

log("G0 heap start={}".format(gc.mem_free()))

# ---- G1 synthesis
try:
    t0 = time.ticks_ms()
    s = tts.Synth()
    load_ms = time.ticks_diff(time.ticks_ms(), t0)
    log("G1a models loaded {}ms free={}".format(load_ms, gc.mem_free()))
    t0 = time.ticks_ms()
    size = s.text_to_wav(tts.speak_text(name), path)
    log("G1b synth {}ms bytes={} text='{}'".format(
        time.ticks_diff(time.ticks_ms(), t0), size, tts.speak_text(name)))
    s.close()
    log("G1c closed free={}".format(gc.mem_free()))
except BaseException as e:
    log("G1 FAILED: {}".format(e))
    sys.print_exception(e)

try:
    log("G1d wav on disk={}".format(os.stat(path)[6]))
except BaseException as e:
    log("G1d stat failed: {}".format(e))

# A second name needs its own file for the per-identity cooldown test.
other_path = VOICE + "/zzother.wav"
play_path = VOICE + "/zzplay.wav"
try:
    src = open(path, "rb")
    raw = src.read()
    src.close()
    for dst_path in (other_path, play_path):
        dst = open(dst_path, "wb")
        dst.write(raw)
        dst.close()
        del dst
    del raw, src
    log("G1e second wav ready={} play wav={}".format(
        os.stat(other_path)[6], os.stat(play_path)[6]))
except BaseException as e:
    log("G1e copy failed: {}".format(e))

# ---- G2 trigger logic
g = greet.Greeter(VOICE)
results = []

# Arrive: 3 frames must not trigger, frame 8 must.
for i in range(1, 8):
    r = g.update(name, True)
    results.append((i, r))
log("G2a frames 1-7 triggered={}".format([i for i, r in results if r]))
log("G2b frame8 triggered={} busy={}".format(
    g.update(name, True), g.busy()))
# Cut the playback short so the rest of the matrix tests the state machine
# rather than "a clip is already playing".
g.stop()
log("G2b2 stopped, busy={}".format(g.busy()))

# Stay in view: no repeat (armed is False until the face leaves).
stay = False
for i in range(30):
    if g.update(name, True):
        stay = True
        g.stop()
log("G2c repeated while staying={} (expect False)".format(stay))

# Leave briefly (fewer than LOST_FRAMES), then come back: still no repeat.
for i in range(3):
    g.update(None, True)
back = False
for i in range(12):
    if g.update(name, True):
        back = True
        g.stop()
log("G2d brief loss re-armed={} (expect False)".format(back))

# Leave long enough to re-arm, then return inside the cooldown: blocked by time.
for i in range(greet.LOST_FRAMES + 2):
    g.update(None, True)
rereturn = False
for i in range(12):
    if g.update(name, True):
        rereturn = True
        g.stop()
log("G2e return within cooldown spoke={} (expect False)".format(rereturn))

# A different identity has its own cooldown slot and speaks immediately.
other = False
for i in range(12):
    if g.update("zzother", True):
        other = True
        g.stop()
        break
log("G2f different name spoke={} (expect True)".format(other))

# ---- G3 playback
log("G3a playing test")
# Re-arm first: the matrix above left an identity in view, and the greeter only
# re-arms after LOST_FRAMES frames without a face. A new person walking up is
# exactly that sequence.
for i in range(greet.LOST_FRAMES + 2):
    g.update(None, True)
started = False
for i in range(12):
    if g.update("zzplay", True):
        started = True
        break
log("G3b started={} busy={} player_active={}".format(
    started, g.busy(), g.player.active))
t0 = time.ticks_ms()
# Simulate a recognition frame: pump once, then spend the frame budget on the
# AI work. This is what decides whether the audio keeps up or starves.
while g.busy() and time.ticks_diff(time.ticks_ms(), t0) < 15000:
    g.pump()
    time.sleep_ms(39)
elapsed = time.ticks_diff(time.ticks_ms(), t0)
log("G3c finished busy={} elapsed={}ms frames_written={} stream_seconds={}".format(
    g.busy(), elapsed, g.player.frames,
    round(g.player.frames / 24000.0, 3)))
# A 0.92 s clip must report about that many seconds, not zero and not forever.
log("G3d frames>0={} elapsed<8000={}".format(
    g.player.frames > 0, elapsed < 8000))

# ---- G4 cleanup
g.stop()
log("G4a after stop busy={} player.active={}".format(
    g.busy(), g.player.active))
try:
    os.remove(path)
    log("G4b removed test wav")
except BaseException as e:
    log("G4b remove failed: {}".format(e))
try:
    os.remove(other_path)
    log("G4c removed second wav")
except BaseException as e:
    log("G4c remove failed: {}".format(e))
try:
    os.remove(play_path)
    log("G4d removed play wav")
except BaseException as e:
    log("G4d remove failed: {}".format(e))
gc.collect()
log("G5 heap end={}".format(gc.mem_free()))

out.append("PROBE_DONE")
f = open("/sdcard/_greetmeas.txt", "w")
f.write("\n".join(out))
f.close()
print("WROTE")
