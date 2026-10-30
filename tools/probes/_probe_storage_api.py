# Verifies the filesystem and numpy APIs the Recognize database relies on.
#
# Everything here is cheap and non-blocking: no Sensor, no kmodel, no display
# objects beyond the two constants that are looked up. It creates and then
# removes a scratch directory so it leaves nothing behind.

import json
import os

import ulab.numpy as np

DIR = "/data/face/_probe_tmp"
print("A1 os.rename:", hasattr(os, "rename"))
print("A2 os.remove:", hasattr(os, "remove"))
print("A3 os.listdir:", hasattr(os, "listdir"))
print("A4 json dumps/loads:", hasattr(json, "dumps"), hasattr(json, "loads"))

# os.mkdir creates one level only and raises ENOENT when the parent is missing,
# so every ancestor must be created in order. This mirrors what the app does.
def ensure_dir(path):
    parts = path.split("/")
    for i in range(2, len(parts) + 1):
        level = "/".join(parts[:i])
        try:
            os.mkdir(level)
        except BaseException:
            pass

ensure_dir(DIR)
print("A5 mkdir nested ok, exists={}".format(
    os.stat(DIR)[0] if hasattr(os.stat(DIR), "__getitem__") else "?"))

# float32 round-trip through a list, which is how a feature is written.
vals = [0.125 * i - 1.5 for i in range(512)]
arr = np.array(vals, dtype=np.float)
raw = arr.tobytes()
print("A6 array dim={} bytes={}".format(len(arr), len(raw)))

path = DIR + "/f001.bin"
tmp = path + ".tmp"
f = open(tmp, "wb")
f.write(raw)
f.close()
del f, arr, raw

try:
    os.remove(path)
except BaseException:
    pass
os.rename(tmp, path)
print("A7 rename ok, size={}".format(os.stat(path)[6]))

f = open(path, "rb")
back = f.read()
f.close()
del f
arr2 = np.frombuffer(back, dtype=np.float)
print("A8 readback dim={} first={:.4f} last={:.4f}".format(
    len(arr2), float(arr2[0]), float(arr2[511])))
err = 0.0
for i in range(512):
    d = float(arr2[i]) - vals[i]
    if d < 0:
        d = -d
    if d > err:
        err = d
print("A9 max round-trip error={:.6f}".format(err))

idx = DIR + "/db.json"
text = json.dumps({"next": 2, "items": [{"file": "f001.bin", "name": "ab"}]})
f = open(idx, "w")
f.write(text)
f.close()
del f
f = open(idx, "r")
again = json.loads(f.read())
f.close()
del f
print("A10 index round-trip: next={} n={} name={}".format(
    again["next"], len(again["items"]), again["items"][0]["name"]))

import lvgl as lv
print("A11 lv.TEXT_ALIGN:", hasattr(lv, "TEXT_ALIGN"))

for name in os.listdir(DIR):
    try:
        os.remove(DIR + "/" + name)
    except BaseException as e:
        print("A12 cleanup", name, e)
try:
    os.rmdir(DIR)
    print("A13 rmdir ok")
except BaseException as e:
    print("A13 rmdir:", e)

print("RESULT: DONE")
