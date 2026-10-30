# Sha256 of the timezone wheel bitmaps on the board, for comparing
# against the host after a re-bake.

import hashlib, os

base = "/sdcard/factory/gui/assets/cache"

def hexs(b):
    return "".join("{:02x}".format(c) for c in b)

for name in ("tz_t0.bin", "tz_t1.bin", "tz_t2.bin"):
    p = base + "/" + name
    h = hashlib.sha256()
    total = 0
    with open(p, "rb") as f:
        while True:
            b = f.read(4096)
            if not b:
                break
            total += len(b)
            h.update(b)
    try:
        d = hexs(h.digest())[:16]
    except Exception:
        d = "?"
    print(name, total, d)
