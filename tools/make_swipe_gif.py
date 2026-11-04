# Stitches the board-captured swipe stills into the README animation.
#
# The frames are real renders from the production pager (see
# tools/probes/_cap_readme_swipe.py), captured at known scroll offsets. This script
# only re-times them: 55 ms per frame, matching the 180 ms snap in 6-7 frames, then a
# 900 ms hold on the last frame so a reader can look at page 2 before the loop
# restarts. It also locks the status bar clock to the first frame, because the frames
# were captured over about a minute and a live clock would jump backwards.
#
# Usage:
#   python tools/make_swipe_gif.py [frames_dir] [out_path]
#   defaults: tmp/swipe  img/swipe.gif

import os
import sys

from PIL import Image

FRAMES = sys.argv[1] if len(sys.argv) > 1 else "tmp/swipe"
OUT = sys.argv[2] if len(sys.argv) > 2 else "img/swipe.gif"

FRAME_MS = 55
HOLD_MS = 900
# Extra hold on page 1 before the swipe starts, so a reader registers the desktop
# before it moves. Kept equal to the end hold so the loop reads as a steady rhythm.
# GIF stores delays in centiseconds, so both values land as 90 and FRAME_MS as 50.
START_HOLD_MS = 900
# Status bar band, taken from launcher.STATUS_H. Everything above this line is frozen
# to the first frame so the clock and wifi/battery glyphs stay put.
STATUS_H = 56
MAX_BYTES = 400 * 1024
# A full-page scroll is the worst case for GIF: nearly every pixel changes every
# frame. At 800x480 even 32 colours stays above 400 KB, so the trade is resolution
# and colour count together. 0.7 keeps the 28 px tile labels legible on GitHub while
# landing around 320 KB.
SCALE = 0.7


def load_frames():
    names = sorted(n for n in os.listdir(FRAMES) if n.lower().endswith(".jpg"))
    if not names:
        raise SystemExit("no frames in %s" % FRAMES)
    frames = [Image.open(os.path.join(FRAMES, n)).convert("RGB") for n in names]
    if SCALE != 1.0:
        size = (int(frames[0].width * SCALE), int(frames[0].height * SCALE))
        frames = [f.resize(size, Image.LANCZOS) for f in frames]
    return frames


def freeze_bar(frames):
    """Copy the first frame's status bar onto every frame."""
    bar_h = max(1, int(round(frames[0].height * STATUS_H / 480.0)))
    bar = frames[0].crop((0, 0, frames[0].width, bar_h))
    for frame in frames[1:]:
        frame.paste(bar, (0, 0))


def build(frames, colours):
    hold = frames[-1].quantize(colors=colours, method=Image.MEDIANCUT)
    out = [f.quantize(colors=colours, method=Image.MEDIANCUT) for f in frames[:-1]]
    out.append(hold)
    durations = [FRAME_MS] * len(out)
    durations[0] = START_HOLD_MS
    durations[-1] = HOLD_MS
    out[0].save(OUT, save_all=True, append_images=out[1:], duration=durations,
                loop=0, optimize=True, disposal=2)
    return os.path.getsize(OUT)


frames = load_frames()
freeze_bar(frames)
print("[GIF] {} frames {}x{}".format(len(frames), frames[0].width, frames[0].height))

for colours in (128, 96, 64, 48, 32):
    size = build(frames, colours)
    print("[GIF] colours={} bytes={}".format(colours, size))
    if size <= MAX_BYTES:
        break

print("[GIF] written {}".format(OUT))
