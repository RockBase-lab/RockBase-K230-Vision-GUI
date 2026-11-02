# Greeting trigger matrix around identity switches. The old code only re-armed
# after a stretch with no face, so A -> B (with a face in every frame) greeted
# nobody, and an equality test on the streak meant a trigger frame skipped while
# a clip was playing never fired again.
#
# Needs two WAVs under the voice directory; it copies one to the second name.
import os
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _m in list(sys.modules):
    if _m == "audio" or _m.startswith("audio."):
        del sys.modules[_m]

out = []


def log(m):
    out.append(m)
    print(m)


from audio import greet

VOICE = "/data/face/recognize/voice"
A = "jay"
B = "maomao"

for nm in (A, B):
    p = VOICE + "/" + nm + ".wav"
    try:
        log("M0 {} {} bytes".format(nm, os.stat(p)[6]))
    except BaseException as e:
        log("M0 {} MISSING ({})".format(nm, e))

g = greet.Greeter(VOICE)


def feed(name, frames, enabled=True):
    """Feed frames, report the frame index a greeting started on (or None)."""
    started = None
    for i in range(frames):
        if g.update(name, enabled):
            if started is None:
                started = i + 1
            # Let the clip finish so the next case is not blocked by it.
            g.stop()
    return started


# T1: A arrives from scratch, then stays. Exactly one greeting.
g.reset()
s1 = feed(A, greet.TRIGGER_FRAMES + 20)
log("T1 first_arrival at_frame={} (expect {})".format(s1, greet.TRIGGER_FRAMES))

# T2: A -> B with no empty frame between them. B must be greeted.
g.reset()
feed(A, greet.TRIGGER_FRAMES)
s2 = feed(B, greet.TRIGGER_FRAMES + 5)
log("T2 switch_A_to_B at_frame={} (expect {})".format(s2, greet.TRIGGER_FRAMES))

# T3: A -> B -> A. The second A is inside the per-name cooldown, so it is
# blocked; after the cooldown expires it must speak again.
g.reset()
feed(A, greet.TRIGGER_FRAMES)
feed(B, greet.TRIGGER_FRAMES)
s3 = feed(A, greet.TRIGGER_FRAMES + 5)
log("T3 A_B_A_back at_frame={} (expect None, cooldown)".format(s3))
time.sleep_ms(greet.COOLDOWN_MS + 300)
for _ in range(greet.LOST_FRAMES + 2):
    g.update(None, True)
s3b = feed(A, greet.TRIGGER_FRAMES + 5)
log("T3b A_after_cooldown at_frame={} (expect {})".format(
    s3b, greet.TRIGGER_FRAMES))

# T4: same person stays for a long time. Must not repeat.
g.reset()
feed(A, greet.TRIGGER_FRAMES)
gone = False
for _ in range(60):
    if g.update(A, True):
        gone = True
g.stop()
log("T4 repeated_while_staying={} (expect False)".format(gone))

# T5: the trigger frame is skipped because a clip is playing. It must still fire
# once that clip ends.
g.reset()
feed(A, greet.TRIGGER_FRAMES)
# Start B while explicitly keeping the player busy.
g.player.start(gain=1.0, path=VOICE + "/" + B + ".wav")
g.player.unmute()
g.active = True
g._t0 = time.ticks_ms()
blocked = None
for i in range(greet.TRIGGER_FRAMES + 3):
    if g.update(B, True):
        blocked = i + 1
log("T5 fired_while_busy={} (expect None)".format(blocked))
g.stop()
after = feed(B, 20)
log("T5b fired_after_free at_frame={} (expect a number)".format(after))

# T6: disabled frames (keyboard open) then re-enabled with the same person.
# Same person, so the cooldown decides; the point is that it does not hang.
g.reset()
feed(A, greet.TRIGGER_FRAMES)
for _ in range(15):
    g.update(A, False)
s6 = feed(A, greet.TRIGGER_FRAMES + 5)
log("T6 after_overlay_closed at_frame={} (cooldown expected)".format(s6))
time.sleep_ms(greet.COOLDOWN_MS + 300)
for _ in range(greet.LOST_FRAMES + 2):
    g.update(None, True)
s6b = feed(A, greet.TRIGGER_FRAMES + 5)
log("T6b after_cooldown at_frame={} (expect {})".format(
    s6b, greet.TRIGGER_FRAMES))

# T7: disabled then a different person appears.
g.reset()
feed(A, greet.TRIGGER_FRAMES)
for _ in range(15):
    g.update(B, False)
s7 = feed(B, greet.TRIGGER_FRAMES + 5)
log("T7 B_appeared_while_frozen at_frame={} (expect {})".format(
    s7, greet.TRIGGER_FRAMES))

g.stop()
out.append("PROBE_DONE")
f = open("/sdcard/_switch.txt", "w")
f.write("\n".join(out))
f.close()
print("WROTE")
