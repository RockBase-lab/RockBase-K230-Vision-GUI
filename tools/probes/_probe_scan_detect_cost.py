# find_qrcodes cost at the Scan app's detection size.
#
# Moving the detection channel from 640x480 to 800x480 removed the strips that
# only the preview could see, but it also hands find_qrcodes 56% more pixels.
# Detection runs inside the app loop, so the cost has to stay inside the
# DETECT_PERIOD_MS throttle; this measures both sizes on the same synthetic
# code so the numbers are comparable.
#
# Pure image work: no sensor, no display, no LVGL.

import time

import image

REPS = 5
DATA = "HELLO-ROCKBASE-SCAN-123"


def load_matrix():
    f = open("/sdcard/_qr_matrix.txt")
    lines = f.read().split("\n")
    f.close()
    n = int(lines[0])
    rows = []
    for line in lines[1:]:
        if not line.strip():
            continue
        row = []
        toks = line.split()
        i = 0
        while i < len(toks):
            r = int(toks[i])
            v = 0 if toks[i + 1] == "1" else 255
            row.extend([v] * r)
            i += 2
        rows.append(row)
    return n, rows


def make_frame(n, rows, scale, w, h):
    """Draw the code centred on a w x h GRAYSCALE frame."""
    img = image.Image(w, h, image.GRAYSCALE)
    img.draw_rectangle(0, 0, w, h, color=0xFFFFFF, fill=True)
    size = n * scale
    ox = (w - size) // 2
    oy = (h - size) // 2
    for y in range(n):
        row = rows[y]
        x = 0
        while x < n:
            v = row[x]
            r = 1
            while x + r < n and row[x + r] == v:
                r += 1
            if v == 0:
                img.draw_rectangle(ox + x * scale, oy + y * scale,
                                   r * scale, scale, color=0, fill=True)
            x += r
    return img


def cost(img, label):
    codes = img.find_qrcodes()
    found = len(codes)
    payload = codes[0].payload() if found else ""
    t0 = time.ticks_ms()
    for _ in range(REPS):
        img.find_qrcodes()
    elapsed = time.ticks_diff(time.ticks_ms(), t0)
    print("T {} {:.1f} ms  found={} payload_ok={}".format(
        label, elapsed / float(REPS), found, payload == DATA))


n, rows = load_matrix()
print("P modules={}".format(n))
cost(make_frame(n, rows, 12, 640, 480), "640x480")
cost(make_frame(n, rows, 12, 800, 480), "800x480")
print("P done")
