# Restore the greeting switch and verify a greeting plays again.
import json
import os
import sys

out = []


def log(m):
    out.append(m)
    print(m)


sys.path.insert(0, "/sdcard/factory/gui")

DB_INDEX = "/data/face/recognize/db.json"

f = open(DB_INDEX, "r")
raw = f.read()
f.close()
data = json.loads(raw)
log("V1 before voice_on={}".format(data.get("voice_on", "<absent>")))

data["voice_on"] = True
text = json.dumps(data)
tmp = DB_INDEX + ".tmp"
f = open(tmp, "w")
f.write(text)
f.close()
try:
    os.remove(DB_INDEX)
except BaseException:
    pass
os.rename(tmp, DB_INDEX)
log("V2 wrote voice_on=True")

# Read it back through the app's own loader to prove the app will see it.
for _m in list(sys.modules):
    if _m == "apps" or _m.startswith("apps."):
        del sys.modules[_m]
try:
    import apps.app_face_recognize as R
    log("V3 module VOICE_DIR={} DB_INDEX={}".format(R.VOICE_DIR, R.DB_INDEX))
except BaseException as e:
    log("V3 import failed: {}".format(e))

# Verify the file parses and the flag is visible.
f = open(DB_INDEX, "r")
again = json.loads(f.read())
f.close()
log("V4 after voice_on={}".format(again.get("voice_on")))

# Confirm every identity still has a playable WAV and report its level.
import media.wave as wave
VOICE = "/data/face/recognize/voice"
names = []
for it in again.get("items", []):
    nm = it.get("name")
    if nm not in names:
        names.append(nm)
log("V5 identities={}".format(len(names)))
for nm in names:
    p = VOICE + "/" + nm + ".wav"
    try:
        wf = wave.open(p, "rb")
        try:
            n = len(wf.read_frames(1 << 20))
        finally:
            wf.close()
        log("   {:<12} {} bytes".format(nm, n * 2))
    except BaseException as e:
        log("   {:<12} MISSING ({})".format(nm, e))

out.append("PROBE_DONE")
f = open("/sdcard/_vrestore.txt", "w")
f.write("\n".join(out))
f.close()
print("WROTE")
