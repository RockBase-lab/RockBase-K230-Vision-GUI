# Diagnose OCR against a poem on screen: which punctuation the dict can emit,
# what the charset is missing, and the raw recognised strings per box.
#
# Run with --lvgl. Prints the dict's punctuation inventory so the font subset can
# be extended with exactly the characters the model can produce.

import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
# The serial REPL is persistent: a cached module would shadow the deployed file.
for _name in ("apps.app_ocr", "apps", "apps.error", "ui.beep", "launcher",
              "camera_fov"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv

DICT_PATH = "/sdcard/examples/utils/dict.txt"

words = open(DICT_PATH, "r").read(100000).split("\r\n")
print("D1 dict entries={} last={!r}".format(len(words), words[-1]))

# Classify what the dictionary can emit, so the font subset covers it.
groups = {"ascii_print": "", "cjk_punct": "", "latin1": "", "other": ""}
punct_sample = "，。、！？：；“”‘’『』「」《》【】（）—-…·"
for w in words:
    if not w:
        continue
    for ch in w:
        o = ord(ch)
        if 0x20 <= o <= 0x7E:
            if ch not in groups["ascii_print"]:
                groups["ascii_print"] += ch
        elif ch in punct_sample or 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF:
            if ch not in groups["cjk_punct"]:
                groups["cjk_punct"] += ch
        elif o < 0x2E80:
            if ch not in groups["latin1"]:
                groups["latin1"] += ch
        else:
            if ch not in groups["other"]:
                groups["other"] += ch

for name in ("ascii_print", "cjk_punct", "latin1"):
    val = groups[name]
    print("D2 {} n={} {!r}".format(name, len(val), val[:80]))
print("D3 other(hanzi etc) n={}".format(len(groups["other"])))

# Which of these are absent from the baked charset (source of box glyphs)?
try:
    charset = open("/sdcard/factory/gui/tools/ocr_charset.txt", "r").read()
    print("D4 charset file on board n={}".format(len(charset)))
except BaseException:
    charset = None
    print("D4 charset file not deployed (expected; it is a host-side input)")

# Recognise one frame through the real app.
src = open("/sdcard/factory/gui/launcher.py").read()
G = {"__name__": "ocr_diag"}
exec(compile(src, "launcher.py", "exec"), G)
G["_ensure_cache_dir"]()

meta = None
for item in G["FOLDER_MEMBERS"]["vision"]:
    if item["id"] == "ocr":
        meta = item
        break

launcher = G["Launcher"]()
launcher.open_app(meta)
app = launcher.active_app
if app is None:
    print("RESULT: FAIL - app did not open")
else:
    # settle exposure
    t0 = time.ticks_ms()
    while time.ticks_diff(time.ticks_ms(), t0) < 1200:
        app.step()
        lv.task_handler()
        time.sleep_ms(20)

    t0 = time.ticks_ms()
    app._scan()
    print("D5 scan {}ms lines={}".format(
        time.ticks_diff(time.ticks_ms(), t0), len(app.texts)))
    for i, (coords, text) in enumerate(app.texts):
        xs = [coords[0], coords[2], coords[4], coords[6]]
        ys = [coords[1], coords[3], coords[5], coords[7]]
        print("D6.{} x={}-{} y={}-{} h={} {!r}".format(
            i, int(min(xs)), int(max(xs)), int(min(ys)), int(max(ys)),
            int(max(ys) - min(ys)), text))

    launcher.close_app()
    print("RESULT: DONE")
