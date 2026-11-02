# Why is there no voice? Compare the greeting files against the identities the
# database now holds, and report the persisted switches.
import json
import os
import sys

out = []


def log(m):
    out.append(m)
    print(m)


sys.path.insert(0, "/sdcard/factory/gui")

DB_DIR = "/data/face/recognize"
VOICE = DB_DIR + "/voice"
DB_INDEX = DB_DIR + "/db.json"

# Persisted switches.
try:
    f = open(DB_INDEX, "r")
    raw = f.read()
    f.close()
    data = json.loads(raw)
    log("D1 photos_allowed={} voice_on={}".format(
        data.get("photos_allowed"), data.get("voice_on", "<absent>")))
    names = []
    srcs = {}
    for it in data.get("items", []):
        nm = it.get("name")
        if nm not in names:
            names.append(nm)
        s = it.get("src", "live")
        srcs.setdefault(nm, set()).add(s)
    log("D2 identities={} total_samples={}".format(
        len(names), len(data.get("items", []))))
except BaseException as e:
    log("D1/D2 failed: {}".format(e))
    names = []
    srcs = {}

# Greeting files.
try:
    files = sorted(os.listdir(VOICE))
except BaseException as e:
    files = []
    log("D3 voice dir unreadable: {}".format(e))
log("D3 voice files={} ({} entries)".format(len(files), files))

have = set()
for fn in files:
    if fn.endswith(".wav"):
        have.add(fn[:-4])
for nm in names:
    tag = "/".join(sorted(srcs.get(nm, {"?"})))
    log("D4 {:<12} src={:<10} wav={}".format(
        nm, tag, "YES" if nm in have else "MISSING"))

orphan = sorted(have - set(names))
if orphan:
    log("D5 files with no identity: {}".format(orphan))

# Is the TTS stack still present and importable?
for p in ("/sdcard/factory/gui/audio/tts.py",
          "/sdcard/factory/gui/audio/greet.py",
          "/sdcard/factory/gui/audio/pinyin_map.py",
          "/sdcard/factory/gui/audio/device.py"):
    try:
        log("D6 {} {}".format(os.stat(p)[6], p.split("/")[-1]))
    except BaseException as e:
        log("D6 MISSING {} ({})".format(p, e))

# Does the app still carry the voice wiring?
try:
    src = open("/sdcard/factory/gui/apps/app_face_recognize.py").read()
    for token in ("_queue_voices", "_step_voice", "take_missing", "voice_on",
                  "greeter.update", "_make_greeting"):
        log("D7 {}={}".format(token, token in src))
except BaseException as e:
    log("D7 read failed: {}".format(e))

out.append("PROBE_DONE")
f = open("/sdcard/_nvoice.txt", "w")
f.write("\n".join(out))
f.close()
print("WROTE")
