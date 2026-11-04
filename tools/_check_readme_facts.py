"""Check the numeric claims that README.md and README_zh.md make.

Reads only source files, so it runs on the PC without a board. Each line prints the
claim, the value found in the source, and OK/MISMATCH. Exits non-zero on any
mismatch so it can gate a commit.
"""

import io
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
GUI = os.path.join(ROOT, "factory", "gui")

failures = []


def read(path):
    with io.open(path, encoding="utf-8") as handle:
        return handle.read()


def check(name, expected, actual):
    ok = expected == actual
    print("{:<34} expected={!r:<8} actual={!r:<8} {}".format(
        name, expected, actual, "OK" if ok else "MISMATCH"))
    if not ok:
        failures.append(name)


launcher = read(os.path.join(GUI, "launcher.py"))
paging = read(os.path.join(GUI, "ui", "paging.py"))

# Registry counts
apps_block = re.search(r"\nAPPS = \[(.*?)\n\]", launcher, re.S).group(1)
members_block = re.search(r"\nFOLDER_MEMBERS = \{(.*?)\n\}", launcher, re.S).group(1)
top = len(re.findall(r'^\s*\{"id"', apps_block, re.M))
members = len(re.findall(r'^\s*\{"id"', members_block, re.M))
folders = len(re.findall(r'"folder": True', apps_block))
check("top-level entries", 11, top)
check("folder entries", 2, folders)
check("folder members", 14, members)
check("registered app modules", 23, top - folders + members)

# Preload tables
check("boot kmodels", 16, len(re.findall(r'^\s*\(".*\.kmodel", \d+\),',
                                        launcher, re.M)))
check("boot fonts", 5, len(re.findall(r'^\s*\(".*\.bin", \d+\),', launcher, re.M)))

# Layout / feel constants
def const(path, name):
    return int(re.search(r"^%s = (\d+)" % name, path, re.M).group(1))


check("status bar height", 56, const(launcher, "STATUS_H"))
check("tile size", 164, const(launcher, "TILE_SIZE"))
check("icon size", 112, const(launcher, "ICON_SIZE"))
check("swipe threshold", 120, const(paging, "SWIPE_THRESHOLD_PX"))
check("snap animation ms", 180, const(paging, "SNAP_ANIM_MS"))
check("wifi boot budget ms", 15000, const(launcher, "BOOT_WIFI_TIMEOUT_MS"))
check("backlight pin", 25, const(launcher, "BACKLIGHT_PIN"))

# App-specific constants
vision = read(os.path.join(GUI, "apps", "app_vision.py"))
coco = re.search(r"COCO = \[(.*?)\]", vision, re.S).group(1)
check("COCO classes", 80, len(re.findall(r'"[a-z][a-z ]*"', coco)))
check("detect switchable models", 4, len(re.findall(r'"id": "v8n320"|"id": "v8n224"|'
                                                   r'"id": "fruitv8"|"id": "yolo26n"', vision)))

points = read(os.path.join(GUI, "apps", "app_face_points.py"))
check("face landmark points", 106, int(re.search(r"N_POINTS = (\d+)", points).group(1)))

gesture = read(os.path.join(GUI, "apps", "app_gesture.py"))
labels = re.findall(r'return "(\w+)"', gesture)
check("gesture labels", 9, len(labels))

plate = read(os.path.join(GUI, "apps", "app_plate.py"))
check("plate streak for a new caption", 4,
      const(plate, "STREAK_N"))
check("plate streak to change a caption", 8,
      const(plate, "STREAK_SWITCH"))

imu = read(os.path.join(GUI, "apps", "app_imu.py"))
check("imu pages", 3, const(imu, "PAGE_COUNT"))

print()
if failures:
    print("MISMATCH: " + ", ".join(failures))
    sys.exit(1)
print("all checks passed")
