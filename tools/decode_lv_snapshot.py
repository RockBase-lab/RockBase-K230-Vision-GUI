# Decode an LVGL snapshot exported from the board (RLE + base64) into a PNG.
#
# Use case: when there is no camera or photographing the screen is inconvenient,
# pull "what the current screen looks like" over the serial port to verify the UI.
# Flat-colour UIs compress extremely well with RLE (a full 800x480 frame is about
# 46KB, a 33x reduction).
#
# On-board capture code (goes in the script that tools/_run_remote.py runs):
#
#     import ubinascii, uctypes
#     dsc = lv.snapshot_take(lv.scr_act(), lv.COLOR_FORMAT.ARGB8888)
#     w, h = dsc.header.w, dsc.header.h
#     ptr = int.from_bytes(bytes(memoryview(dsc.data)), "little")
#     raw = uctypes.bytearray_at(ptr, w * h * 4)     # ARGB8888 memory order = B,G,R,A
#     dsc.buf_free()
#     out = bytearray()
#     for y in range(h):                              # per-row RLE, run 1..255
#         ...                                          # (len-1, B, G, R)
#     print("SHOT <tag> %d %d %d %d" % (w, h, len(raw), len(out)))
#     b64 = ubinascii.b2a_base64(bytes(out)).decode().strip()
#     for i in range(0, len(b64), 800):               # chunked, avoids an over-long line
#         print("B64 " + b64[i:i + 800])
#     print("SHOTEND <tag>")
#
# Usage:
#   python tools/decode_lv_snapshot.py <capture output.txt> [output template]
#   the template uses %s for the tag, defaulting to "tmp/shot_%s.png"
#
# Note: PowerShell's `>` redirection writes UTF-16LE, which breaks parsing.
#   Capture stdout with a Python subprocess and write it in binary (see the capture
#   scripts under tmp), or use `| Out-File -Encoding utf8` directly.

import base64
import re
import sys

from PIL import Image


def decode(src_path, out_template="tmp/shot_%s.png"):
    shots = {}
    cur = None
    size = None
    buf = []
    for line in open(src_path, encoding="utf-8", errors="replace"):
        line = line.strip()
        m = re.match(r"^SHOT (\w+) (\d+) (\d+) (\d+) (\d+)$", line)
        if m:
            cur = m.group(1)
            size = (int(m.group(2)), int(m.group(3)))
            buf = []
            continue
        if line.startswith("B64 ") and cur:
            buf.append(line[4:])
            continue
        if line.startswith("SHOTEND") and cur:
            shots[cur] = (size, "".join(buf))
            cur = None

    if not shots:
        raise SystemExit("no SHOT/B64/SHOTEND block found; check the capture output")

    written = []
    for tag, ((w, h), b64) in shots.items():
        data = base64.b64decode(b64)
        img = Image.new("RGB", (w, h))
        px = img.load()
        off = 0
        for y in range(h):
            x = 0
            while x < w:
                if off + 4 > len(data):
                    raise SystemExit("data truncated at row %d, x=%d" % (y, x))
                n = data[off] + 1
                b = data[off + 1]
                g = data[off + 2]
                r = data[off + 3]
                off += 4
                for k in range(n):
                    if x + k < w:
                        px[x + k, y] = (r, g, b)
                x += n
        path = out_template % tag
        img.save(path)
        written.append((tag, w, h, len(data) // 4, path))
        print("%-12s %dx%d runs=%d -> %s" % (tag, w, h, len(data) // 4, path))
    return written


if __name__ == "__main__":
    src = sys.argv[1] if len(sys.argv) > 1 else "tmp/_shot_out.txt"
    tpl = sys.argv[2] if len(sys.argv) > 2 else "tmp/shot_%s.png"
    decode(src, tpl)
