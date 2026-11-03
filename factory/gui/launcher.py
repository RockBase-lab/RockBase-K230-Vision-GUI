# RockBase K230 factory GUI launcher (Android-phone-style home screen)
# Visual spec: 800x480 dark theme
#   - Three dark levels: desktop darkest < tile next < border brightest (values below)
#   - 56px status bar at the top: Home at the top-left (exit inside an app), clock in
#     the middle, wifi/battery at the right (all bitmap icons);
#     no bottom navigation bar (exit = status-bar Home or the physical CANCEL key)
#   - 164x164 square icons, 4x2 per page; a full page is centred, a partial last page
#     starts at the top-left
#   - Page indicator dots: 12px dot / 30x12 pill when active
#   - Physical keys: GPIO0=CANCEL (active low), GPIO53=OK (active high)
#   - Navigation: CANCEL goes back exactly one level along the path the user came in
#     by (app -> the folder flyout it was opened from -> the page that folder lives on
#     -> page 0). See Launcher.back_out(), which owns the rule.
#
# Performance constraint (do not go back to live text):
#   This firmware runs LVGL in DISP_RENDER_MODE.FULL, so every refresh repaints the
#   whole screen, and tiny_ttf re-rasterises on each repaint (measured ~1.4s for 9
#   labels), which freezes scrolling. Each tile is therefore pre-baked into one
#   XRGB8888 bitmap, so a full-screen repaint during a swipe is just a blit
#   (measured ~0ms). Cache flow:
#     python tools/gen_tile_cache.py   # offline bake on the PC (recommended)
#     on boot _cache_load reads the cache when it hits; when it misses, one on-device
#     snapshot bake writes the tile to disk.
#   Status-bar icons and clock digits are bitmaps for the same reason: the clock ticks
#   every second, and live text would stall the UI once per second.
#
# Run: exec(open('/sdcard/factory/gui/launcher.py').read())
#
# App contract (apps/app_xxx.py):
#   create_app(content, ctx) -> app
#     content: lv.obj the app mounts its UI on (area = full width below the status bar)
#     ctx:     context; the app calls ctx.request_exit() to ask for the home screen
#   app.step()      called once per main-loop iteration (non-blocking, state machine)
#   app.destroy()   called when returning home; releases hardware (sensor/audio, ...)

import os
import sys
import time
import gc

import image
import lvgl as lv
import uctypes
from machine import FPIOA, Pin, PWM
from media.display import Display
from media.media import ALIGN_UP
from machine import TOUCH

DISPLAY_TYPE = Display.ST7701
# ALIGN_UP(800, 16) == 800 because 800 is a multiple of 16. Measured on board:
# FLAG_ROTATION_270 accepts an 800x480 buffer.
DISPLAY_WIDTH = ALIGN_UP(800, 16)
DISPLAY_HEIGHT = 480

# The ST7701 on RockBase is mounted rotated the opposite way to the SDK's default
# landscape orientation, so it is corrected at the display layer with
# Display.init(flag=FLAG_ROTATION_270). The FT5316 touch driver's default rotation
# already matches that display, so TOUCH(0) gets no extra rotate argument.
DISPLAY_FLAG = Display.FLAG_ROTATION_270

# ---- Palette (graphite base + brand cyan, 2026-10-27) ----
# Intent: three cool-tinted dark levels (background < tile < border) keep the screen
# from looking uniformly grey, while colour is spent only on icons and a few
# highlight points. The offline bakers (gen_app_icons / gen_tile_cache /
# gen_clock_digits / gen_status_icons) parse these constants: after changing a value
# you must re-run the matching generator and bump BAKE_VER, otherwise the board keeps
# loading the stale bitmaps.
COL_BG = 0x12141A          # desktop/app background (darkest, blue-black)
COL_BAR = 0x0E1116         # status-bar fill (one level deeper, so the bar recedes)
COL_TEXT = 0xFFFFFF        # primary text
COL_MUTED = 0x6B7484       # secondary text (splash hints etc.)
COL_DOT_OFF = 0x3A4250     # page dot (inactive)
ACCENT = 0x4CC9F0          # brand cyan: active page dot, clock, other highlights
STATUS_H = 56              # status-bar height (fits the large Home button); no bottom bar
# Minimum horizontal inset of a full page (centring pushes the real margin to
# GRID_MARGIN).
GRID_PAD_L = 16
# Target outer margin: icon spacing is derived backwards from it so a page fills up.
GRID_MARGIN = 24
# Grid top/bottom margin (with the dots it makes the vertical layout symmetric).
GRID_V_MARGIN = 28
# Gap from the last icon row down to the page dots.
DOTS_GAP = 16
DOT_H = 12                 # page dot height
# Clock digit cell width (the 32px digit ink is 19px wide, plus slack).
CLOCK_CELL_W = 22
# Clock colon width (the 32px colon ink is 9px wide; 7px would clip it).
CLOCK_COLON_W = 11
CLOCK_H = 36               # clock bitmap height
# Swipe threshold and snap duration come from the paging module so every
# horizontally paged app shares one set of rules; see
# `SWIPE_THRESHOLD_PX = paging.SWIPE_THRESHOLD_PX` below.
# Temporary diagnostic switch: prints release->snap latency and coast distance.
# Off by default because printing every frame costs time; set True while tuning and
# a few swipes will emit [SWIPE] lines.
SWIPE_DIAG = False

# ---- Frame-rate boost (removes the "low refresh / smearing" feel while swiping) ----
# LVGL's refresh period is the compile-time constant LV_DEF_REFR_PERIOD (30ms by
# default), which caps the frame rate at 33fps. Measured on board, a swipe only
# reaches 22-30fps in flush (5-8ms of task_handler plus 2-3ms of flush per loop
# iteration, on top of the sleep), which reads as "not smooth" on an 800x480 panel.
# Fix: while active (touching, or within BOOST_TAIL_MS of release so the snap
# animation is covered) call lv.refr_now() every frame to force an immediate repaint
# and break the 30ms period; stay off while idle so the CPU is not burned for
# nothing. Frames with no dirty area return quickly from refr_now.
FRAME_BOOST = True
BOOST_TAIL_MS = 400   # boost duration after release; covers SNAP_ANIM_MS with margin
# Delay before the network service auto-starts on boot (net_mgr.start()); 0 = now.
# Why the delay: start() synchronously kicks off WLAN init and the connect thread,
# whose driver section holds the GIL and, as measured, stalls first-screen rendering
# and touch response for several seconds. Desktop becomes usable first, WiFi connects
# in the background.
NET_BOOT_DELAY_MS = 1200
TILE_SIZE = 164            # icon block size
ICON_SIZE = 112            # icon image size inside a tile (read by tools/gen_tile_cache.py)
TILE_BG = 0x1E222B         # tile card fill (the offline baker parses this too; never edit one side)
TILE_BORDER = 0x2C3340     # tile hairline border: one level up from the background (same)
TILE_BORDER_W = 1          # border width in px (same)
TILE_RADIUS = 20           # tile corner radius (same)
TILE_PAD = 10              # minimum icon spacing (real spacing is derived from the width; fallback)
NAME_COLOR = 0xC7CDD6      # tile name text colour (parsed by the offline baker)
NAME_BOTTOM_MARGIN = 10    # px from the name ink bottom to the tile bottom (offline baker)
STATUS_BATT_W = 32         # status-bar battery icon width (wifi position depends on it)

# ---- panel backlight ----
# The backlight is a separate GPIO (25). Display.init() brings up the panel and the
# frame pipeline but leaves this pin alone, so a boot without this code renders into a
# dark screen: LVGL runs and the logs look healthy, and a snapshot still succeeds
# because it reads the LVGL buffer rather than the panel. GPIO output state also
# survives a soft reset, so a probe that dimmed the pin leaves the next boot dark.
BACKLIGHT_PIN = 25
BACKLIGHT_PWM_HZ = 1000
BACKLIGHT_FADE_STEPS = 10  # 10 steps x 15ms = 150ms fade-in
BACKLIGHT_FADE_MS = 15
_BACKLIGHT = None          # keep the PWM/Pin object alive; dropped objects stop driving

# Per-app icon tint (app id -> RGB hex). This breaks up the monotone "all icons are
# white" look, but saturation is held at ~35-40% (glazed grey): a high-saturation
# variant was measured as "too loud" by test users, and this de-saturated version is
# the settled one. gen_app_icons.py parses this table to composite the ink in that
# colour; a new app must add a row here or it falls back to white.
APP_ACCENTS = {
    "touch":    0x7FB6D9,
    "camera":   0xC99AAE,
    "gallery":  0x9C92C9,
    "audio":    0x6FB8A4,
    "vision":   0xC990A8,
    "system":   0x94A3B8,
    "battery":  0xC9AE7A,
    "imu":      0x7FA6CC,
    "network":  0x74B5AC,
    "settings": 0x9CA3AF,
    "video":    0xCC8A92,
    "voice":    0xA992C7,
    "talk":     0x79AFCF,
    "scan":     0xA9BE7C,
    "face":     0xD9A76A,
    "gesture":  0x8FBF9F,
    "tracker":  0x9AA8E0,
    # Second-level flyout members (placeholders inside the vision / face folders)
    "pose":     0xC990A8,
    "segment":  0xB79FC9,
    "orient":   0x7FB6D9,
    "person":   0x9C92C9,
    "fall":     0xCC8A92,
    "plate":    0xA9BE7C,
    "ocr":      0x94A3B8,
    "face_det": 0xD9A76A,
    "landmark": 0xC9AE7A,
    "recognize": 0x9AA8E0,
    "liveness": 0x8FBF9F,
    "mask":     0xA992C7,
}

# Container accent colours (rotated per app; only the placeholder badge fallback
# path uses them)
ACCENTS = [0x2D98DA, 0x0FB9B1, 0xEB3B5A, 0x20BF6B, 0xF7B731, 0xFA8231]


def _sym(*names):
    """Look up an LVGL built-in symbol by candidate names, tolerating the symbol
    differences between firmware bindings."""
    for name in names:
        symbol = getattr(lv.SYMBOL, name, None)
        if symbol:
            return symbol
    return ""


def _font(*names):
    """Return the first built-in font that exists among the candidate names."""
    for name in names:
        font = getattr(lv, name, None)
        if font:
            return font
    return None


TILE_FONT = None  # tile label font: assets/Nunito-Bold-700.ttf (tiny_ttf at runtime, OFL)
TILE_FONT_SIZE = 28
_tile_font_tried = False  # load lazily on the first bake; a failure is not retried


def _load_tile_font():
    """Render the Nunito Bold TTF on the SD card through the firmware's own tiny_ttf.

    The built-in fonts have no bold face, and a bin produced by lv_font_conv does not
    match this firmware's LVGL version (metrics read back fine but the bitmap alpha is
    distorted, making the text nearly invisible on a dark background -- measured on
    board), which is why this goes through tiny_ttf instead.
    The path must carry the LVGL FS drive prefix A:; without it the call fails silently
    (measured on board).
    """
    global TILE_FONT, _tile_font_tried
    _tile_font_tried = True
    path = "A:" + _gui_dir + "/assets/Nunito-Bold-700.ttf"
    try:
        font = lv.tiny_ttf_create_file(path, TILE_FONT_SIZE)
        if font is None:
            raise RuntimeError("tiny_ttf returned None")
        TILE_FONT = font
        print("[GUI] tile font loaded: Nunito Bold {}px (line_height={})".format(
            TILE_FONT_SIZE, getattr(font, "line_height", "?")))
    except BaseException as error:
        print("[GUI] tile font load failed ({}), fallback built-in".format(error))
        TILE_FONT = None


def _mem_str():
    """Return current MicroPython heap usage (KB) for diagnostics at key stages."""
    try:
        free = gc.mem_free()
        alloc = gc.mem_alloc()
        return "alloc={}KB free={}KB".format(alloc // 1024, free // 1024)
    except Exception as error:
        return "mem n/a ({})".format(error)


def _no_hit(o):
    """Keep an object out of hit testing: lv.obj is CLICKABLE by default and would
    steal clicks from its parent button."""
    try:
        o.clear_flag(lv.obj.FLAG.CLICKABLE)
    except Exception:
        pass


def _clean(o):
    """Strip the LVGL default theme's border/shadow/focus outline, FOCUSED included,
    otherwise the first button carries a visible outline ring."""
    focused = getattr(lv.STATE, "FOCUSED", 0x100)
    states = (0, lv.STATE.PRESSED, lv.STATE.CHECKED, lv.STATE.CHECKED | lv.STATE.PRESSED,
              focused, focused | lv.STATE.PRESSED)
    for st in states:
        try:
            o.set_style_border_width(0, st)
            o.set_style_shadow_width(0, st)
            o.set_style_outline_width(0, st)
        except Exception:
            pass


# App registry: id / display name / icon PNG (relative to the gui dir, optional) /
# symbol fallback / module.
# An entry with "folder": True is a group tile (iOS-folder style): clicking it opens a
# member picker layer (_open_folder) instead of an app. Members live in FOLDER_MEMBERS
# and use the same meta shape as top-level entries, so flags such as
# immersive/release_home still apply.
# The top level stays at 15 entries = two pages of a 4x2 grid, which keeps paging
# meaningful. Placeholder entries point at apps.app_placeholder; swap in apps.app_xxx
# once the real app exists.
APPS = [
    {"id": "camera", "name": "Camera", "icon": "assets/icon_camera.png",
     "symbol": _sym("CAMERA", "VIDEO"), "module": "apps.app_camera",
     "immersive": True},
    # Gallery: 2x2 thumbnails plus full-screen view, reading /data/photos (written by
    # the Camera app when it captures)
    {"id": "gallery", "name": "Gallery", "icon": "assets/icon_gallery.png",
     "symbol": _sym("IMAGE", "IMAGE_S", "PHOTO"), "module": "apps.app_gallery"},
    # QR scan: full-screen viewfinder plus live find_qrcodes on chn1 greyscale frames;
    # a hit freezes the picture and opens a non-full-screen white panel with the
    # payload. The icon already exists (Lucide qr-code).
    {"id": "scan", "name": "Scan", "icon": "assets/icon_scan.png",
     "symbol": _sym("QR_CODE", "BARCODE"), "module": "apps.app_scan",
     "immersive": True},
    {"id": "network", "name": "Network", "icon": "assets/icon_network.png",
     "symbol": _sym("WIFI"), "module": "apps.app_network"},
    # Vision group: single entry point for every single-model detection app
    # (members in FOLDER_MEMBERS).
    {"id": "vision", "name": "Vision", "icon": "assets/icon_vision.png",
     "symbol": _sym("CPU", "CHARGE"), "folder": True},
    {"id": "audio", "name": "Audio", "icon": "assets/icon_audio.png",
     "symbol": _sym("MUSIC", "LOUD", "VOLUME_MID"), "module": "apps.app_audio"},
    {"id": "touch", "name": "Touch", "icon": "assets/icon_touch.png",
     "symbol": _sym("TOUCHSCREEN", "TOUCH", "EYE_OPEN"), "module": "apps.app_touch"},
    {"id": "settings", "name": "Settings", "icon": "assets/icon_settings.png",
     "symbol": _sym("SETTINGS"), "module": "apps.app_placeholder"},
    # ---- Page 2 ----
    # Face group: chained face pipeline (detect -> align -> embed). Its pipeline
    # differs from the single-model family in Vision and it has to maintain a local
    # face database, so it stands alone as a group.
    {"id": "face", "name": "Face", "icon": "assets/icon_face.png",
     "symbol": _sym("USER"), "folder": True},
    {"id": "gesture", "name": "Gesture", "icon": "assets/icon_gesture.png",
     "symbol": _sym("HAND"), "module": "apps.app_placeholder"},
    # Voice group: KWS wake word plus TTS output (speech in and speech out as a pair).
    {"id": "voice", "name": "Voice", "icon": "assets/icon_voice.png",
     "symbol": _sym("MIC", "MICROPHONE"), "folder": True},
    {"id": "tracker", "name": "Tracker", "icon": "assets/icon_tracker.png",
     "symbol": _sym("TARGET"), "module": "apps.app_placeholder"},
    # IMU: on-board QMI8658A six-axis inertial sensor (I2C3 / GPIO44-45).
    # Three pages: driving dashboard / raw data curves / attitude-angle curves,
    # switched by swiping.
    # Immersive: the app owns the whole screen and none of the three pages shows the
    # status bar (the dashboard wants a full-screen attitude indicator, and the curve
    # pages follow suit so there is no seam where the dashboard covers the bar and the
    # curves expose it).
    # Exit goes through the physical CANCEL key, same as Camera / Scan.
    {"id": "imu", "name": "IMU", "icon": "assets/icon_imu.png",
     "symbol": _sym("MOVE"), "module": "apps.app_imu",
     "immersive": True},
    # System: chip info / memory / sensor state.
    {"id": "system", "name": "System", "icon": "assets/icon_system.png",
     "symbol": _sym("INFORMATION"), "module": "apps.app_placeholder"},
    # Battery: single-cell pack voltage through the ADC5 divider. Sampling runs
    # inside step() one reading at a time so the UI never blocks.
    {"id": "battery", "name": "Battery", "icon": "assets/icon_battery.png",
     "symbol": _sym("BATTERY_FULL", "BATTERY_3", "BATTERY_2"),
     "module": "apps.app_battery"},
]

# Folder member table: folder id -> list of member meta (same shape as APPS entries).
# Member tiles skip the home-screen tile bake: the flyout draws an icon PNG, or an
# accent badge plus a symbol, directly, which saves the cache and memory of 16 extra
# tiles.
FOLDER_MEMBERS = {
    "vision": [
        # Live object detection: yolov8n_320 (80 COCO classes), preview plus boxes drawn
        # on top by LVGL.
        # release_home: on entry the home screen is destroyed and the tile bitmap cache
        # is released (~1.5MB).
        # Measured: with the home screen resident, the MicroPython heap has only ~2.3MB
        # left and is fragmented, so the kpu's to_numpy() output (a 705KB contiguous
        # block) always fails to malloc; with the home screen released the whole chain
        # is stable at ~3.9MB. Returning home rebuilds it from the SD tile cache
        # (measured ~0.7s).
        {"id": "detect", "name": "Detect", "icon": "assets/icon_vision.png",
         "symbol": _sym("CPU"), "module": "apps.app_vision",
         "immersive": True, "release_home": True},
        # Live 17-keypoint skeleton (yolo11n-pose 320). Same immersive shell as
        # detect. release_home: the (1,56,2100) output tensor needs a ~470KB
        # contiguous block, measured to sit at 2.1MB free with the home screen
        # resident -- workable but tight enough that the cached tiles go first.
        {"id": "pose", "name": "Pose", "icon": "assets/icon_pose.png",
         "symbol": _sym("USER"), "module": "apps.app_pose",
         "immersive": True, "release_home": True},
        {"id": "segment", "name": "Segment", "icon": "assets/icon_segment.png",
         "symbol": _sym("CROP"), "module": "apps.app_placeholder"},
        {"id": "orient", "name": "Orient", "icon": "assets/icon_orient.png",
         "symbol": _sym("ROTATE_RIGHT"), "module": "apps.app_placeholder"},
        {"id": "person", "name": "Person", "icon": "assets/icon_person.png",
         "symbol": _sym("USER"), "module": "apps.app_placeholder"},
        {"id": "fall", "name": "Fall", "icon": "assets/icon_fall.png",
         "symbol": _sym("WARNING"), "module": "apps.app_placeholder"},
        # Plate: detection -> crop -> recognition, so two models stay resident and
        # the heap has to be clean before they load. release_home frees the home
        # screen and the tile cache first, the same trade Detect and Pose make.
        # Measured per frame: detection 57-59 ms plus recognition 3-5 ms.
        {"id": "plate", "name": "Plate", "icon": "assets/icon_plate.png",
         "symbol": _sym("CARD"), "module": "apps.app_plate",
         "immersive": True, "release_home": True},
        {"id": "ocr", "name": "OCR", "icon": "assets/icon_ocr.png",
         # Snapshot text scan: preview runs live, OK/tap freezes and runs one
         # det+rec pass (det 230 ms + ~70 ms per box, measured). release_home:
         # both kpUs plus the 256 KB CJK font need the contiguous heap the tile
         # cache occupies.
         "symbol": _sym("TYPE"), "module": "apps.app_ocr",
         "immersive": True, "release_home": True},
    ],
    "face": [
        # The display name is Faces rather than Detect: the Vision group already has a
        # Detect (generic COCO detection), and two members with the same name side by
        # side leave users unsure which one they tapped.
        # Only the name changes; id and icon stay put, because the member icon cache is
        # keyed by icon file name and does not invalidate when the name changes.
        #
        # Live face detection (RetinaFace 320 + 5 landmarks). It shares the same skeleton
        # as detect in Vision (dual-channel capture + preview + box overlay), but its
        # post-processing and coordinate compensation are entirely different.
        # release_home: a conservative start matching Vision so the heap is guaranteed
        # first; measured inference itself is light at 8.5ms/frame, so this can become
        # False later once in-house testing confirms, saving the ~0.7s rebuild on the
        # way back to home.
        {"id": "face_det", "name": "Faces", "icon": "assets/icon_face_det.png",
         "symbol": _sym("USER"), "module": "apps.app_face_det",
         "immersive": True, "release_home": True},
        {"id": "landmark", "name": "Points", "icon": "assets/icon_landmark.png",
         "symbol": _sym("DOTS"), "module": "apps.app_face_points",
         "immersive": True, "release_home": True},
        {"id": "recognize", "name": "Recognize", "icon": "assets/icon_recognize.png",
         "symbol": _sym("ID"), "module": "apps.app_face_recognize",
         "immersive": True, "release_home": True},
        {"id": "liveness", "name": "Liveness", "icon": "assets/icon_liveness.png",
         "symbol": _sym("EYE_OPEN"), "module": "apps.app_face_liveness",
         "immersive": True, "release_home": True},
        {"id": "mask", "name": "Mask", "icon": "assets/icon_mask.png",
         "symbol": _sym("SHIELD"), "module": "apps.app_placeholder"},
    ],
    "voice": [
        {"id": "kws", "name": "Wake", "icon": "assets/icon_voice.png",
         "symbol": _sym("MIC"), "module": "apps.app_placeholder"},
        {"id": "tts", "name": "Talk", "icon": "assets/icon_talk.png",
         "symbol": _sym("VOLUME_HIGH"), "module": "apps.app_placeholder"},
    ],
}

# CanMV's os module has no os.path, and exec(open(...).read()) leaves no __file__ at
# runtime, so fall back to the deployment path on the board.
try:
    _gui_dir = __file__.rsplit("/", 1)[0]
except NameError:
    _gui_dir = "/sdcard/factory/gui"
if _gui_dir not in sys.path:
    sys.path.insert(0, _gui_dir)

# Shared paging module (home screen, gallery and any other horizontally paged app use
# the same thresholds and styles).
# It must be imported after _gui_dir is on sys.path, and it must never import launcher.py
# back: this file runs via exec(open().read()), so a reverse import would execute it a
# second time and build a second Launcher.
from ui import paging
from net import service as net_mgr
# Shared ADC5 access: the status bar and the Battery app hold one channel handle, so
# the app never opens a second one.
import batt
# Signal that an app cannot run (camera missing, ...): the app raises AppUnavailable
# and this file shows the notice panel.
from apps.error import AppUnavailable
# Click sound (passive buzzer on IO43). init/poll failures all degrade silently so
# they never block startup.
from ui import beep

SWIPE_THRESHOLD_PX = paging.SWIPE_THRESHOLD_PX   # 120px, shared with every app
SNAP_ANIM_MS = paging.SNAP_ANIM_MS               # 200ms

disp_imgs = None
disp_drv = None
REFR_NOW = None   # cached lv.refr_now (None = not supported)


def _load_png(path):
    """Read a PNG file into an LVGL image descriptor; None if that fails."""
    try:
        with open(path, "rb") as f:
            data = f.read()
        return lv.img_dsc_t({"data_size": len(data), "data": data})
    except OSError:
        return None


# ---- Tile bake cache (the core of smooth swiping) ----
# Measured on board: tiny_ttf re-rasterises at ~1.4s per 9 labels, and under
# RENDER_MODE.FULL a swipe repaints the whole screen, so the UI locks up.
# Fix: bake each tile into one XRGB8888 bitmap at boot and draw the home screen from
# bitmaps; a full-screen repaint during a swipe then costs a few blits, about 0ms.
# The first boot bakes at ~1.3s per tile (a one-off tiny_ttf cost) and stores the
# result on SD; later boots read the cache and come up almost instantly.
BAKE_VER = 7                       # +1 when tile layout/fonts change, dropping old caches
                                   # v2: added the Gallery tile (APPS order changed, re-bake all)
                                   # v3: placeholders replaced by 10 new app icon tiles (bake all)
                                   # v4: palette A (tile fill/border and per-app icon tint)
                                   # v5: de-saturated icons settled + clock digits back to white
                                   # v6: home reordered to 13 items + folder dot badge;
                                   #     header gained a folder flag byte (folder change invalidates)
CACHE_DIR = None                   # = _gui_dir + "/assets/cache", initialised in main
BAKED_TILES = {}                   # app id -> (img_dsc_t, raw_bytes): kept referenced against GC


def _cache_path(app_id):
    return CACHE_DIR + "/tile_{}.bin".format(app_id)


def _ensure_cache_dir():
    global CACHE_DIR
    CACHE_DIR = _gui_dir + "/assets/cache"
    try:
        os.mkdir(CACHE_DIR)
    except OSError:
        pass


def _snapshot_pixels(dsc):
    """Read the pixel bytes out of the img_dsc returned by snapshot.

    dsc.data is a C_Array whose buffer protocol exposes 8 bytes: the pixel buffer
    pointer (little endian). Take the pointer and batch-read with bytes_at (measured
    on board: 0ms). Calling addressof(dsc.data) instead yields the wrapper struct, not
    the pixels, so the pointer indirection is mandatory.
    """
    w, h = dsc.header.w, dsc.header.h
    size = w * h * 4
    ptr = int.from_bytes(bytes(memoryview(dsc.data)), "little")
    return w, h, dsc.header.cf, bytes(uctypes.bytes_at(ptr, size))


def _make_dsc(w, h, cf, raw):
    return lv.img_dsc_t({"header": {"cf": cf, "w": w, "h": h},
                         "data_size": len(raw), "data": raw})


def _cache_load(meta):
    """Return (dsc, raw) on a cache hit, else None. Validates magic/version/size/name;
    any mismatch invalidates the entry."""
    try:
        with open(_cache_path(meta["id"]), "rb") as f:
            blob = f.read()
    except OSError:
        return None
    if len(blob) < 15 or blob[:5] != b"TILE2" or blob[5] != BAKE_VER:
        return None
    w = blob[6] | (blob[7] << 8)
    h = blob[8] | (blob[9] << 8)
    cf = blob[10] | (blob[11] << 8)
    nlen = blob[12] | (blob[13] << 8)
    name = blob[14:14 + nlen]
    folder_flag = blob[14 + nlen]
    raw = blob[15 + nlen:]
    # The folder flag takes part in validation: an entry switching between "folder"
    # and "plain app" invalidates its old tile automatically.
    if (name != meta["name"].encode() or
            folder_flag != (1 if meta.get("folder") else 0) or
            len(raw) != w * h * 4):
        return None
    return _make_dsc(w, h, cf, raw), raw


def _cache_save(meta, w, h, cf, raw):
    name = meta["name"].encode()
    try:
        with open(_cache_path(meta["id"]), "wb") as f:
            f.write(b"TILE2")
            f.write(bytes([BAKE_VER]))
            f.write(w.to_bytes(2, "little"))
            f.write(h.to_bytes(2, "little"))
            f.write(cf.to_bytes(2, "little"))
            f.write(len(name).to_bytes(2, "little"))
            f.write(name)
            f.write(bytes([1 if meta.get("folder") else 0]))
            f.write(raw)
    except OSError as error:
        print("[GUI] cache save '{}' failed: {}".format(meta["id"], error))


def _bake_tile(parent, meta):
    """Render one tile (background wrapper > rounded button > icon + tiny_ttf label)
    and snapshot it into a bitmap; returns (w, h, cf, raw).

    The outer background wrapper makes the rounded corners fill with the desktop
    colour under opaque XRGB8888, so the tile sits seamlessly on the wallpaper.
    Only called on a cache miss, which is why the TTF is loaded lazily: a cache hit
    never touches the font at all.
    """
    if TILE_FONT is None and not _tile_font_tried:
        _load_tile_font()
    wrap = lv.obj(parent)
    wrap.set_size(TILE_SIZE, TILE_SIZE)
    wrap.set_pos(0, 0)
    wrap.set_style_bg_color(lv.color_hex(COL_BG), 0)
    wrap.set_style_bg_opa(lv.OPA.COVER, 0)
    wrap.set_style_border_width(0, 0)
    wrap.set_style_radius(0, 0)
    wrap.set_style_pad_all(0, 0)
    wrap.clear_flag(lv.obj.FLAG.SCROLLABLE)

    btn = lv.obj(wrap)
    btn.set_size(TILE_SIZE, TILE_SIZE)
    btn.align(lv.ALIGN.CENTER, 0, 0)
    btn.set_style_bg_color(lv.color_hex(TILE_BG), 0)
    btn.set_style_bg_opa(lv.OPA.COVER, 0)
    btn.set_style_radius(TILE_RADIUS, 0)
    btn.set_style_border_width(TILE_BORDER_W, 0)
    btn.set_style_border_color(lv.color_hex(TILE_BORDER), 0)
    btn.set_style_pad_all(0, 0)
    _clean(btn)

    accent = ACCENTS[APPS.index(meta) % len(ACCENTS)]
    icon_src = _load_png(_gui_dir + "/" + meta["icon"]) if meta.get("icon") else None
    if icon_src is not None:
        icon = lv.img(btn)
        icon.set_src(icon_src)
        icon.align(lv.ALIGN.TOP_MID, 0, 2)
    else:
        badge_size = 96
        badge = lv.obj(btn)
        badge.set_size(badge_size, badge_size)
        badge.align(lv.ALIGN.TOP_MID, 0, (TILE_SIZE - 30 - badge_size) // 2)
        badge.set_style_bg_color(lv.color_hex(accent), 0)
        badge.set_style_bg_opa(lv.OPA.COVER, 0)
        badge.set_style_radius(badge_size // 2, 0)
        badge.set_style_border_width(0, 0)
        sym = lv.label(badge)
        sym.set_text(meta["symbol"])
        sym.set_style_text_color(lv.color_hex(COL_TEXT), 0)
        font = _font("font_montserrat_48", "font_montserrat_32", "font_montserrat_24")
        if font:
            sym.set_style_text_font(font, 0)
        sym.center()

    name = lv.label(btn)
    name.set_text(meta["name"])
    name.set_style_text_color(lv.color_hex(NAME_COLOR), 0)
    font = TILE_FONT or _font("font_dejavu_16_persian_hebrew",
                              "font_montserrat_16", "font_montserrat_14")
    if font:
        name.set_style_text_font(font, 0)
    name.align(lv.ALIGN.BOTTOM_MID, 0, -2)

    # Folder tile: three-dot badge in the bottom-right corner (folder semantics). The
    # accent comes from that app's tint so it matches the icon colour; the name is
    # centred at the bottom and the badge sits in the empty bottom-right area, so the
    # two do not collide.
    if meta.get("folder"):
        dot_c = lv.color_hex(APP_ACCENTS.get(meta["id"], 0x9CA3AF))
        for di in range(3):
            dot = lv.obj(btn)
            dot.set_size(10, 10)
            dot.set_pos(TILE_SIZE - 46 + di * 14, TILE_SIZE - 40)
            dot.set_style_bg_color(dot_c, 0)
            dot.set_style_bg_opa(lv.OPA.COVER, 0)
            dot.set_style_radius(5, 0)
            dot.set_style_border_width(0, 0)
            _no_hit(dot)

    try:
        wrap.update_layout()
    except BaseException:
        pass
    lv.task_handler()
    dsc = lv.snapshot_take(wrap, lv.COLOR_FORMAT.ARGB8888)
    w, h, cf, raw = _snapshot_pixels(dsc)
    try:
        dsc.buf_free()
    except BaseException:
        pass
    wrap.delete()
    return w, h, cf, raw


def bake_all_tiles(splash_parent):
    """Prepare every tile bitmap at boot: read the SD cache when it hits, otherwise
    bake and store it.
    Adding an app only needs one APPS entry: its cache is missing, so it gets baked
    once automatically.
    """
    _ensure_cache_dir()
    t0 = time.ticks_ms()
    hits = baked = 0
    for meta in APPS:
        cached = _cache_load(meta)
        if cached is not None:
            BAKED_TILES[meta["id"]] = cached
            hits += 1
            continue
        try:
            w, h, cf, raw = _bake_tile(splash_parent, meta)
            _cache_save(meta, w, h, cf, raw)
            BAKED_TILES[meta["id"]] = (_make_dsc(w, h, cf, raw), raw)
            baked += 1
        except BaseException as error:
            print("[GUI] bake tile '{}' failed: {}".format(meta["id"], error))
            sys.print_exception(error)
    gc.collect()
    print("[GUI] tiles ready: {} cached + {} baked in {}ms | {}".format(
        hits, baked, time.ticks_diff(time.ticks_ms(), t0), _mem_str()))


STATUS_ICONS = {}  # status-bar PNG icon cache: home / wifi_* / battery_*
CLOCK_IMGS = {}    # clock digit bitmap cache: '0'..'9' / ':'


def _battery_level():
    """Charge cell count 0-3 for the status bar, 0 when the pack cannot be read."""
    try:
        state = batt.status()
    except BaseException as error:
        print("[GUI] battery read failed: {}".format(error))
        return 0
    if state is None:
        return 0
    return state[2]


def _status_icon(name):
    """Load and cache a status-bar icon on demand (assets/icon_status_<name>.png).

    LVGL built-in SYMBOLs are not used here: this firmware's built-in fonts are only
    14/16px, so the glyphs are too small inside a 56px status bar; repainting a PNG is
    just a blit (measured 0ms).
    """
    if name in STATUS_ICONS:
        return STATUS_ICONS[name]
    dsc = _load_png(_gui_dir + "/assets/icon_status_{}.png".format(name))
    STATUS_ICONS[name] = dsc
    return dsc


def _clock_imgs():
    """Load the clock digit bitmaps (Nunito Bold 32px). Bitmaps rather than tiny_ttf:
    under RENDER_MODE.FULL a per-second clock change repaints the whole screen, and
    tiny_ttf re-rasterises at ~150ms each time, a lag the eye clearly sees."""
    if CLOCK_IMGS:
        return CLOCK_IMGS
    for ch in "0123456789":
        CLOCK_IMGS[ch] = _load_png(_gui_dir + "/assets/clock_{}.png".format(ch))
    CLOCK_IMGS[":"] = _load_png(_gui_dir + "/assets/clock_colon.png")
    return CLOCK_IMGS


TOUCH_INPUT = None  # global TouchScreen instance so apps can consume the raw point stream


class TouchScreen:
    """machine.TOUCH -> LVGL input device. Display rotation is handled by the Display
    flag and the FT5316 driver's default rotation already matches it, so coordinates
    line up with the display coordinate system directly.

    Sampling runs along two paths:
    - poll(): called by the main loop every ~5ms and the only place that reads the
      hardware. It updates the shared state LVGL consumes (_pressed/_last_x/_last_y)
      and appends raw points to the _raw queue.
    - callback(): called by LVGL every ~30ms; it reads no hardware and only reports the
      shared state maintained by poll(). That way LVGL always sees fresh state and the
      PRESSED hold for quick taps is more reliable.
    Apps (Touch, for one) consume _raw directly through read_raw(), sampling at ~5ms,
    far above LVGL's 30ms.

    Multi-touch: raw points carry track_id so an app can draw each finger as its own
    stroke. LVGL itself is single-pointer, so only the "primary contact" drives
    _pressed/_last_x/_last_y; other contacts are merely queued.
    """

    # Wait this long after an Up with no new contact before really releasing (bridges
    # the FT5316's phantom Up during a drag).
    # Measured on board (2026-10): while dragging, the gap between a phantom Up and the
    # next contact is p50=20-26ms / max=40ms. This value sets the dead time between
    # release and the start of the snap, but it must exceed the measured max, otherwise
    # mid-drag is mistaken for a release (snap fires early -> wrong start_page -> two
    # pages per swipe). 45ms gives 5ms of headroom and stays under the perceptual
    # threshold. Do not lower it to <=40.
    RELEASE_HOLDOFF_MS = 45

    # ---- Tap jitter dead zone ----
    # LVGL's default scroll_limit is 10px: once the finger moves more than 10px after
    # touch-down it enters scroll mode, and entering scroll mode makes LVGL cancel the
    # child's CLICKED event -- which looks like "tapping the icon does nothing and the
    # page wobbles before snapping back". Measured FT5316 jitter during a normal tap
    # reaches 15-20px, so 10px is far too tight.
    # This binding does **not** expose indev.set_scroll_limit (measured MISSING on
    # board), so the touch layer implements an equivalent software dead zone: while the
    # displacement stays within SCROLL_LIMIT_PX nothing is reported to LVGL (the
    # coordinate stays pinned to the touch-down point).
    # Note this is **not** the page-turn threshold (that is still
    # paging.SWIPE_THRESHOLD_PX = 120); it only decides when a tap becomes a drag.
    SCROLL_LIMIT_PX = 24

    # ---- Coast bridging (removes the "pause after release") ----
    # Doing nothing during the 45ms holdoff leaves the page completely still and the
    # eye catches the hiccup. Fix: keep advancing the reported coordinate at the last
    # velocity for the duration of the wait, and LVGL keeps scrolling, which reads as
    # inertia with no still gap. This only feeds coordinates; it never fires
    # SCROLL_BEGIN/END and never fights the snap animation for control. If a real
    # contact arrives after a phantom Up, its true coordinate simply overwrites the
    # guess (same direction) and the hand-off is seamless.
    # Note this is not re-enabling LVGL's SCROLL_MOMENTUM (that inflates the
    # displacement and breaks the threshold test); the coast distance is bounded by the
    # holdoff window at 45ms of movement at most.
    COAST_MIN_V = 0.2       # px/ms; below this there is no coasting (tap / slow drag)
    COAST_MAX_V = 3.0       # px/ms; clamp so a fast flick cannot fly off
    COAST_WINDOW_MS = 80    # velocity estimation window

    def __init__(self):
        global TOUCH_INPUT
        TOUCH_INPUT = self
        # Shared state for LVGL: the FT5316 only raises events at the instant of a
        # down/move, so with no new event the previous state/coordinate is held, which
        # keeps the press continuous until a real up arrives.
        self._pressed = False
        self._last_x = 0
        self._last_y = 0
        # Release holdoff: a continuous FT5316 drag is interleaved with phantom Ups
        # that split one swipe into several down/up segments, which makes LVGL treat
        # each segment as a tap and never scroll. An Up therefore does not release
        # immediately: a new contact within HOLDOFF counts as a continuous press
        # (bridging the phantom Up), and only a full HOLDOFF without contacts really
        # releases.
        self._up_pending = False
        self._up_ms = 0
        # Diagnostic: timestamp of the last real release (_pressed True->False) for
        # swipe latency analysis.
        self._release_ms = 0
        # Diagnostic: time of the first Up in this gesture / number of Up samples /
        # snapshot of _up_ms at release time. Separates "first Up vs last Up" to see how
        # far each sits from the start of the snap.
        self._first_up_ms = 0
        self._up_n = 0
        self._rel_up_ms = 0
        # Diagnostic: gaps (ms) between repeated Ups and the next contact while
        # dragging, used to size the holdoff.
        self._gap_ms = []
        # Coast bridging: velocity samples (ms, x), current velocity in px/ms, and the
        # timestamp of the last sample
        self._samples = []
        self._vel = 0.0
        self._last_ms = 0
        # Time of the last contact or release: decides whether the high frame rate is
        # needed (frame boost)
        self._active_ms = 0
        # Diagnostic: total distance (px) added by coast bridging in one gesture and the
        # number of advance steps
        self._coast_px = 0
        self._coast_n = 0
        # Raw point queue: (x, y, event, track_id) for high-rate consumption by apps.
        # event 2=Down 3=Move anything else=Up; track_id is a stable id for one finger
        # across a single press, letting an app split contacts into separate strokes.
        self._raw = []
        # LVGL is single-pointer and can only follow one finger: _primary_id is the
        # current primary contact and _down_ids is the set of contacts still on screen
        # (added by Down/Move, removed by Up). When the primary lifts, another contact
        # is promoted immediately, so the pointer never hangs in mid-air and never
        # jumps back and forth between fingers.
        self._primary_id = None
        self._down_ids = set()
        self._track_xy = {}
        # Tap dead-zone state: the anchor is the touch-down point; _dz_gate=True means
        # the finger is still inside the dead zone.
        # See the SCROLL_LIMIT_PX comment: this binding has no
        # indev.set_scroll_limit, so the dead zone lives here.
        self._dz_x0 = 0
        self._dz_y0 = 0
        self._dz_gate = False
        try:
            self.touch = TOUCH(0)
            print("[GUI] Touch initialized.")
        except Exception as error:
            print("[GUI] Touch init failed: {}".format(error))
            self.touch = None
            return
        indev = lv.indev_create()
        indev.set_type(lv.INDEV_TYPE.POINTER)
        indev.set_read_cb(self.callback)
        print("[GUI] tap deadzone = {}px (software)".format(self.SCROLL_LIMIT_PX))
        print("[GUI] touch holdoff = {}ms".format(self.RELEASE_HOLDOFF_MS))

    def poll(self):
        """Called by the main loop every ~5ms: read the hardware, update the shared
        state, append raw points."""
        if self.touch is None:
            return
        t0 = time.ticks_ms()
        try:
            # The panel supports 5 points (FT5316 info.point_num=5); ask for exactly
            # that, never more than the hardware can deliver.
            tp = self.touch.read(5)
            now = time.ticks_ms()
            if tp:
                self._active_ms = now
                down = self._down_ids
                touched = False
                # Whether a gesture was already in progress before this batch decides
                # if this press needs a new dead-zone anchor.
                new_press = not self._pressed
                for p in tp:
                    if len(self._raw) < 64:
                        self._raw.append((p.x, p.y, p.event, p.track_id))
                    if p.event in (2, 3):  # Down / Move
                        self._track_xy[p.track_id] = (p.x, p.y)
                        down.add(p.track_id)
                        # Primary contact: keep the current one; if there is none (or it
                        # just lifted) take the first finger pressed in this batch.
                        if self._primary_id is None:
                            self._primary_id = p.track_id
                        if p.track_id == self._primary_id:
                            if new_press:
                                # touch-down point = dead-zone anchor, set once per batch
                                self._dz_x0 = p.x
                                self._dz_y0 = p.y
                                self._dz_gate = True
                                new_press = False
                            px, py = p.x, p.y
                            if self._dz_gate:
                                lim = self.SCROLL_LIMIT_PX
                                dx = px - self._dz_x0
                                dy = py - self._dz_y0
                                if -lim < dx < lim and -lim < dy < lim:
                                    # inside the dead zone: pin the coordinate to the
                                    # anchor so LVGL sees no displacement at all
                                    px, py = self._dz_x0, self._dz_y0
                                else:
                                    # left the dead zone: subtract it on each axis
                                    # separately so the drag does not jump 24px at once
                                    self._dz_gate = False
                                    if dx > lim:
                                        dx -= lim
                                    elif dx < -lim:
                                        dx += lim
                                    else:
                                        dx = 0
                                    if dy > lim:
                                        dy -= lim
                                    elif dy < -lim:
                                        dy += lim
                                    else:
                                        dy = 0
                                    px = self._dz_x0 + dx
                                    py = self._dz_y0 + dy
                            self._last_x = px
                            self._last_y = py
                            touched = True
                    else:                  # Up
                        down.discard(p.track_id)
                        if p.track_id == self._primary_id:
                            self._primary_id = None
                # The primary contact lifted but other fingers are still on the panel: pick
                # one and let the LVGL pointer keep following it, so it neither hangs in
                # mid-air nor gets mistaken for a release.
                if self._primary_id is None and down:
                    for tid in down:
                        self._primary_id = tid
                        break
                    xy = self._track_xy.get(self._primary_id)
                    if xy is not None:
                        self._last_x = xy[0]
                        self._last_y = xy[1]
                    # A different finger does not inherit the previous anchor; it simply
                    # follows the coordinates that arrive from now on
                    self._dz_gate = False
                    self._up_pending = False
                if touched:
                    # This batch has a press/move: keep the press alive and keep feeding
                    # velocity samples (used by coast bridging).
                    # The test must be "did this batch contain Down/Move", not "is the down
                    # set non-empty at the end of the batch": the FT5316 normally splits a
                    # tap into a Down+Up pair inside the same batch, and looking at the set
                    # would drop the tap entirely.
                    if self._up_pending and SWIPE_DIAG:
                        # A phantom Up was bridged: record the gap to the next contact
                        # (only gaps under 1s count as the same drag)
                        gap = time.ticks_diff(now, self._up_ms)
                        if gap < 1000:
                            self._gap_ms.append(gap)
                    if not self._pressed:
                        self._pressed = True
                        self._first_up_ms = 0
                        self._up_n = 0
                        self._samples = []
                        self._vel = 0.0
                        self._coast_px = 0
                        self._coast_n = 0
                    self._up_pending = False
                    self._add_sample(now, self._last_x)
                    self._last_ms = now
                    self._update_velocity(now)
                elif not down and self._pressed and not self._up_pending:
                    # Not a single finger left on the panel: enter the holdoff test
                    self._up_pending = True
                    self._up_ms = now
                    self._first_up_ms = now
                    self._up_n += 1
                    self._last_ms = now
                elif self._pressed and self._up_pending:
                    self._coast(now)
            elif self._pressed and self._up_pending:
                self._coast(now)
        except Exception:
            pass
        # Holdoff debounce: after an Up, wait until no new contact arrives within
        # HOLDOFF before really releasing (bridges a phantom Up mid-drag).
        # HOLDOFF must exceed both the measured worst-case phantom-Up gap (33ms) and the
        # LVGL indev read period (30ms); otherwise a drag gets split into segments, or a
        # quick tap loses PRESSED.
        # The _down_ids condition is there too: with several fingers any finger still on
        # the panel means this is not a release (the primary lifting does not end the
        # gesture).
        if self._pressed and self._up_pending and not self._down_ids and \
                time.ticks_diff(time.ticks_ms(), self._up_ms) >= self.RELEASE_HOLDOFF_MS:
            self._pressed = False
            self._up_pending = False
            self._release_ms = time.ticks_ms()
            self._rel_up_ms = self._up_ms
            # Refresh the activity time at release too, so the whole snap animation gets
            # the high frame rate
            self._active_ms = self._release_ms
        t = time.ticks_diff(time.ticks_ms(), t0)
        if t > PERF["touch_max"]:
            PERF["touch_max"] = t

    def gap_stats(self):
        """Diagnostic: drain the Up->next-contact gap samples and return
        (n, p50, p90, max); None when there are no samples."""
        gaps = sorted(self._gap_ms)
        self._gap_ms = []
        if not gaps:
            return None
        n = len(gaps)
        return n, gaps[n // 2], gaps[min(n - 1, (n * 9) // 10)], gaps[-1]

    def busy(self):
        """For the main loop: True while a holdoff decision is pending (only this short
        stretch needs 1ms granularity).
        Without it, "holdoff expired -> real release" would be delayed by the main-loop
        period (5-22ms) and turn into a perceptible extra gap after the finger lifts.
        _pressed is deliberately not included: 5ms granularity is plenty while dragging
        (LVGL's indev itself only reads every 30ms), so there is no reason to burn CPU."""
        return self._up_pending

    @property
    def pressed(self):
        """For apps: whether a finger is currently (still considered) down on the screen.

        Note this includes the holdoff window, so it stays True for up to 45ms after the
        finger lifts. That is exactly what "settle after release" style checks want: they
        get enough time to wait for the real lift, without stealing a drag while the
        finger is still on the panel.
        """
        return self._pressed

    def pos(self):
        """For apps: current contact coordinate (x, y) in screen coordinates.

        With no finger down it returns the last position rather than (0, 0): an app that
        implements drag-tracking together with pressed needs the final position before
        the lift at the moment of release.
        """
        return (self._last_x, self._last_y)

    def active(self):
        """For the main loop: whether the high frame rate is needed (touching, or just
        released while the snap animation may still be running).
        The comparison goes through ticks_diff so tick wrap-around cannot fool it."""
        if self._pressed or self._up_pending:
            return True
        return time.ticks_diff(time.ticks_ms(), self._active_ms) < BOOST_TAIL_MS

    # ---- Coast bridging internals ----
    def _add_sample(self, now, x):
        """Record one velocity sample, keeping only the most recent few in the window
        (constant memory)."""
        samples = self._samples
        samples.append((now, x))
        while len(samples) > 8:
            samples.pop(0)

    def _update_velocity(self, now):
        """Estimate velocity (px/ms) from the first and last sample in the window, then
        clamp it. Stale samples reset it to zero so that "drag halfway, pause, then
        release" is not mistaken for a fast flick."""
        samples = self._samples
        while len(samples) > 2 and \
                time.ticks_diff(now, samples[0][0]) > self.COAST_WINDOW_MS:
            samples.pop(0)
        if len(samples) < 2:
            self._vel = 0.0
            return
        t0, x0 = samples[0]
        t1, x1 = samples[-1]
        dt = time.ticks_diff(t1, t0)
        if dt <= 0:
            self._vel = 0.0
            return
        vel = (x1 - x0) / float(dt)
        if vel > self.COAST_MAX_V:
            vel = self.COAST_MAX_V
        elif vel < -self.COAST_MAX_V:
            vel = -self.COAST_MAX_V
        self._vel = vel

    def _coast(self, now):
        """Keep advancing the reported coordinate at the last velocity during the
        holdoff wait so LVGL keeps scrolling.
        Only _last_x changes (callback reads it); the LVGL scroll API is never touched,
        so no SCROLL_BEGIN/END fires and the snap animation is never contested."""
        dt = time.ticks_diff(now, self._last_ms)
        if dt <= 0:
            return
        self._last_ms = now
        self._update_velocity(now)
        vel = self._vel
        if -self.COAST_MIN_V < vel < self.COAST_MIN_V:
            return
        if dt > 60:      # the main loop occasionally stalls; cap the step to avoid a jump
            dt = 60
        nx = self._last_x + vel * dt
        if nx < 0:
            nx = 0.0
        elif nx > DISPLAY_WIDTH - 1:
            nx = float(DISPLAY_WIDTH - 1)
        nx = int(nx)
        if nx != self._last_x:
            self._coast_px += abs(nx - self._last_x)
            self._coast_n += 1
            self._last_x = nx

    def read_raw(self):
        """For apps: take and clear the raw point list accumulated since the last call.
        Each item is (x, y, event, track_id); event 2=Down 3=Move anything else=Up."""
        if not self._raw:
            return None
        pts = self._raw
        self._raw = []
        return pts

    def clear_raw(self):
        """Drop the currently queued raw points. Called when opening or closing an app:
        the down/up of "tap the icon to enter the app" would otherwise stay in the queue
        and be mistaken for a tap by the app's own tap detection."""
        self._raw = []

    def callback(self, driver, data):
        # No hardware access here (poll already read it); only report the shared state
        # that poll maintains.
        # The coordinate keeps advancing during a continuous press (including a drag
        # bridged across phantom Ups), which is what lets LVGL recognise a swipe.
        if self.touch is None:
            data.state = lv.INDEV_STATE.RELEASED
            return
        data.point.x = self._last_x
        data.point.y = self._last_y
        data.state = lv.INDEV_STATE.PRESSED if self._pressed else lv.INDEV_STATE.RELEASED


PERF = {"flush_max": 0, "flush_sum": 0, "flush_n": 0,
        "task_max": 0, "task_sum": 0, "task_n": 0,
        "touch_max": 0, "loop_n": 0}


class HardwareButtons:
    """RockBase physical keys: GPIO0=CANCEL (pull-up, active low), GPIO53=OK (pull-down,
    active high).

    The polarity is a measured on-board fact: GPIO0 idles at 1 and reads 0 when pressed;
    GPIO53 idles at 0 and reads 1 when pressed (the other leg of the switch goes to
    3.3V).
    The main loop calls poll() every ~5ms for edge detection plus debounce (a level only
    counts once it has been stable for >= DEBOUNCE_MS).
    CANCEL walks back one layer per press, along the path the user came in by:
    inside an app -> the folder flyout it was opened from (or the desktop, for a
    top-level app) -> the desktop page the folder lives on -> page 0. An app can take
    over the first step through ctx.back_handler.
    OK: no function bound yet, only printed (reserved for confirm/select semantics).
    """

    CANCEL_PIN = 0
    OK_PIN = 53
    DEBOUNCE_MS = 30
    # Active level while pressed (confirmed by measurement on board)
    ACTIVE_LEVEL = {"cancel": 0, "ok": 1}

    def __init__(self):
        self.ok_pin = None
        self.cancel_pin = None
        try:
            # Some K230 pads do not default to the GPIO function, so switch them
            # explicitly to GPIOxx (the same method used for the PA pin in app_audio)
            fpioa = FPIOA()
            for p in (self.CANCEL_PIN, self.OK_PIN):
                try:
                    fpioa.set_function(p, getattr(FPIOA, "GPIO{}".format(p)))
                except BaseException as e:
                    print("[GUI] FPIOA set_function({}) failed: {}".format(p, e))
            self.cancel_pin = Pin(self.CANCEL_PIN, Pin.IN, Pin.PULL_UP)
            self.ok_pin = Pin(self.OK_PIN, Pin.IN, Pin.PULL_DOWN)
            print("[GUI] hardware buttons ready: GPIO{}=CANCEL(up/active-low) "
                  "GPIO{}=OK(down/active-high)".format(self.CANCEL_PIN, self.OK_PIN))
        except BaseException as error:
            print("[GUI] hardware button init failed: {}".format(error))
            self.cancel_pin = None
            self.ok_pin = None
        # Per key: stable state (True=pressed), last raw level, time of the level change
        self._stable = {"cancel": False, "ok": False}
        self._raw = {"cancel": False, "ok": False}   # starts out treated as not pressed
        self._change_ms = {"cancel": 0, "ok": 0}

    def _scan(self, pin, name):
        if pin is None:
            return False
        try:
            level = pin.value()
        except BaseException:
            return False
        now = time.ticks_ms()
        pressed = (level == self.ACTIVE_LEVEL[name])
        if pressed != self._raw[name]:
            self._raw[name] = pressed
            self._change_ms[name] = now
        elif pressed != self._stable[name] and \
                time.ticks_diff(now, self._change_ms[name]) >= self.DEBOUNCE_MS:
            self._stable[name] = pressed
            if pressed:
                return True  # this poll saw one stable press (edge)
        return False

    def poll(self, launcher):
        """Called from the main loop; returns whether a key event was consumed."""
        cancel_press = self._scan(self.cancel_pin, "cancel")
        ok_press = self._scan(self.ok_pin, "ok")
        # Physical keys give the same click-sound feedback as on-screen buttons. OK is
        # the exception: the running app can mute it with ctx.ok_beep=False, because
        # Camera already plays a synthesised shutter on OK and two sounds at once muffle
        # each other.
        if ok_press and (launcher.ctx is None or launcher.ctx.ok_beep):
            beep.tap()
        if cancel_press:
            beep.tap()
        if ok_press:
            print("[Key] OK pressed (GPIO{})".format(self.OK_PIN))
            # Forward the OK edge to the current app (consumed with ctx.take_ok() inside
            # step()). On the home screen ctx is None, so the press is only printed and
            # has no side effect.
            if launcher.ctx is not None:
                launcher.ctx.ok_pressed = True
        if cancel_press:
            print("[Key] CANCEL pressed (GPIO{})".format(self.CANCEL_PIN))
            return launcher.back_out()
        return False


def _backlight_on():
    """Drive the panel backlight to full brightness.

    PWM first for a short fade, then a plain GPIO high, then give up loudly: a silent
    failure here is indistinguishable from a dead board from the user's side.
    """
    global _BACKLIGHT
    try:
        pwm = PWM(BACKLIGHT_PIN, freq=BACKLIGHT_PWM_HZ, duty=0)
        for step in range(1, BACKLIGHT_FADE_STEPS + 1):
            pwm.duty(step * 100 // BACKLIGHT_FADE_STEPS)
            time.sleep_ms(BACKLIGHT_FADE_MS)
        _BACKLIGHT = pwm
        print("[GUI] backlight on: PWM {}Hz {}%, fade {}ms".format(
            BACKLIGHT_PWM_HZ, 100, BACKLIGHT_FADE_STEPS * BACKLIGHT_FADE_MS))
        return
    except BaseException as error:
        print("[GUI] backlight PWM unavailable: {}".format(error))
    try:
        fpioa = FPIOA()
        fn = getattr(FPIOA, "GPIO25", None)
        if fn is None:
            fn = FPIOA.GPIO0 + BACKLIGHT_PIN
        fpioa.set_function(BACKLIGHT_PIN, fn)
        pin = Pin(BACKLIGHT_PIN, Pin.OUT, pull=Pin.PULL_NONE, drive=7)
        pin.value(1)
        _BACKLIGHT = pin
        print("[GUI] backlight on: GPIO{} high".format(BACKLIGHT_PIN))
    except BaseException as error:
        print("[GUI] backlight GPIO failed: {} (the panel will stay dark)".format(error))


def lvgl_setup():
    global disp_imgs, disp_drv, REFR_NOW
    lv.init()    # LV_COLOR_DEPTH is 32: ARGB8888 <-> image.BGRA8888
    disp_imgs = [image.Image(DISPLAY_WIDTH, DISPLAY_HEIGHT, image.BGRA8888) for _ in range(2)]
    disp_drv = lv.disp_create(DISPLAY_WIDTH, DISPLAY_HEIGHT)
    disp_drv.set_color_format(lv.COLOR_FORMAT.ARGB8888)
    disp_drv.set_draw_buffers(disp_imgs[0].bytearray(), disp_imgs[1].bytearray(),
                              disp_imgs[0].size(), lv.DISP_RENDER_MODE.FULL)
    disp_drv.set_flush_cb(_flush_cb)
    # Probe the forced-refresh API (presence varies between bindings). If the probe
    # fails, fall back to the normal cadence.
    refr = getattr(lv, "refr_now", None)
    if refr is None:
        print("[GUI] frame boost: lv.refr_now NOT available")
        return
    try:
        refr(disp_drv)
        REFR_NOW = refr
        print("[GUI] frame boost: lv.refr_now ok")
    except BaseException as error:
        print("[GUI] frame boost: lv.refr_now failed: {}".format(error))


def _flush_cb(disp_drv, area, color):
    t0 = time.ticks_ms()
    if disp_drv.flush_is_last():
        ptr = uctypes.addressof(color.__dereference__())
        img_to_show = disp_imgs[0] if disp_imgs[0].virtaddr() == ptr else disp_imgs[1]
        Display.show_image(img_to_show, layer=Display.LAYER_OSD0)
    disp_drv.flush_ready()
    t = time.ticks_diff(time.ticks_ms(), t0)
    PERF["flush_sum"] += t
    PERF["flush_n"] += 1
    if t > PERF["flush_max"]:
        PERF["flush_max"] = t


class AppContext:
    """Context handed to an app: content-area size plus exit request."""

    def __init__(self):
        self.width = DISPLAY_WIDTH
        self.height = DISPLAY_HEIGHT - STATUS_H
        self.screen_w = DISPLAY_WIDTH
        self.screen_h = DISPLAY_HEIGHT
        # Absolute offset of the content area on screen (apps use it to map screen
        # touch coordinates into their local space)
        self.content_x = 0
        self.content_y = STATUS_H
        self.app_name = ""
        self.want_exit = False
        self.touch = None  # TouchScreen instance; apps can call touch.read_raw() for high-rate points
        # Physical OK key (GPIO53) press edge: set by HardwareButtons, consumed by the app
        # via take_ok(). A read-and-clear flag rather than a callback, so an app that has
        # not implemented the interface cannot crash.
        self.ok_pressed = False
        # Whether the OK key also plays a click sound (read by HardwareButtons).
        # Default True; apps with their own audio (Camera's shutter, for one) set it to
        # False while constructing so the two sounds never overlap.
        self.ok_beep = True
        # CANCEL key interception: lets an app walk back one layer at a time (gallery
        # full screen -> grid -> home screen, for instance).
        # Returning True means the key was consumed (stay in the app); None or False keeps
        # the default behaviour of exiting.
        self.back_handler = None
        # Hardware failing while the app runs (camera unplugged mid-session, say): the app
        # calls report_unavailable(), and the main loop releases the app, hides its UI
        # and shows the same notice panel as a failed start.
        self.unavailable_msg = None

    def request_exit(self):
        self.want_exit = True

    def report_unavailable(self, message):
        """An app reports at runtime that a hardware dependency is unusable (frames keep
        failing, for example).

        The app neither shows a panel nor exits itself: the launcher handles it
        centrally (release handles + notice + Cancel back to the home screen), keeping
        the experience identical to a failed construction. Repeated calls keep only the
        first message.
        """
        if self.unavailable_msg is None:
            self.unavailable_msg = message

    def set_back_handler(self, handler):
        """An app registers its CANCEL handler: returning True consumes the key and does
        not exit the app."""
        self.back_handler = handler

    def take_ok(self):
        """For apps: whether the OK key was pressed in this cycle; one read clears it."""
        if self.ok_pressed:
            self.ok_pressed = False
            return True
        return False


class Launcher:
    def __init__(self):
        self.active_app = None
        self.active_meta = None
        self.ctx = None
        self.clock_imgs = None  # clock bitmap widgets of the current screen (None = none)
        self.dots = []
        self.pager = None      # home pager (paging.SnapPager; None when there is one page)
        # Home page to restore. A release_home app deletes the home screen, so the pager
        # is rebuilt from scratch and would otherwise always come back on page 0. The
        # page is saved before that deletion so CANCEL can step back one level at a time
        # (app -> folder flyout -> the page the folder lives on -> page 0).
        self.home_page = 0
        # Folder the running app was opened from, so exiting returns to its flyout
        # instead of the bare desktop. None when the app was opened from a top-level
        # tile.
        self.return_folder = None
        self.home_scr = None   # built once and kept resident for reuse
        self.app_scr = None    # current app screen, deleted explicitly on exit
        self.app_content = None  # current app content container (hidden if it fails later)
        self.home_clock_imgs = None  # home clock widgets (overwritten by an app; restored on return)
        self.wifi_img = None   # wifi icon widget of the current screen (None = no status bar)
        self.home_wifi_img = None  # home wifi icon (overwritten by an app; restored on return)
        self.batt_img = None   # battery icon widget of the current screen (None = no status bar)
        self.home_batt_img = None  # home battery icon (same hand-over as wifi)
        self._wifi_key = None  # last wifi variant applied, avoids re-calling set_src every second
        self.splash = None     # boot splash screen, deleted once home is up (never delete an active screen)
        self.folder = None     # open folder flyout {"meta":..,"obj":..} (lives on the home screen only)

    # ---------- Shared shell: status bar (with the large Home) + content area ----------
    def _build_shell(self, center_text, show_clock, immersive=False):
        """56px status bar: large Home button at the top-left, title/clock centred, status
        icons at the right.
        No bottom navigation bar; leaving an app means tapping the status-bar Home or
        pressing the physical CANCEL key.
        Returns (scr, content, home_btn); home_btn is disabled on the home screen (None
        means immersive)."""
        scr = lv.obj()
        scr.set_style_bg_color(lv.color_hex(COL_BG), 0)
        scr.set_style_bg_opa(lv.OPA.COVER, 0)
        scr.set_style_border_width(0, 0)

        if immersive:
            # Immersive: no status bar, the content area is the whole screen. The app
            # owns its own exit interaction.
            content = lv.obj(scr)
            content.set_size(DISPLAY_WIDTH, DISPLAY_HEIGHT)
            content.align(lv.ALIGN.TOP_MID, 0, 0)
            content.set_style_bg_opa(lv.OPA.TRANSP, 0)
            content.set_style_border_width(0, 0)
            content.set_style_pad_all(0, 0)
            # lv.obj is scrollable by default: children that touch the edge or overflow
            # (a detection label drawn 16px above its box, for instance) extend the
            # scroll range and put a scrollbar on the right/bottom edge (hit on board).
            # An immersive content area is the whole screen and never needs scrolling.
            content.clear_flag(lv.obj.FLAG.SCROLLABLE)
            self.clock_imgs = None
            # Immersive has no status bar: drop the wifi icon reference and its variant
            # cache so _update_wifi_icon does not write to a deleted widget (the previous
            # screen's status bar dies with that screen). This has to return right here:
            # falling through would rebuild the status bar and shrink content to the
            # 800x424 block below it, cropping the immersive view.
            self.wifi_img = None
            self._wifi_key = None
            return scr, content, None
        # Status bar: the container itself must not scroll (otherwise overflowing content
        # puts a scrollbar on the right edge)
        status = lv.obj(scr)
        status.set_size(DISPLAY_WIDTH, STATUS_H)
        status.align(lv.ALIGN.TOP_MID, 0, 0)
        status.set_style_bg_color(lv.color_hex(COL_BAR), 0)
        status.set_style_border_width(0, 0)
        status.set_style_radius(0, 0)
        status.set_style_pad_all(0, 0)
        status.clear_flag(lv.obj.FLAG.SCROLLABLE)

        # Large Home button at the top-left (tapping it inside an app returns home; it is
        # disabled on the home screen)
        home_btn = lv.btn(status)
        home_btn.set_size(STATUS_H - 12, STATUS_H - 12)
        home_btn.align(lv.ALIGN.LEFT_MID, 8, 0)
        home_btn.set_style_bg_opa(lv.OPA.TRANSP, 0)
        home_btn.set_style_radius(10, 0)
        home_btn.set_style_bg_color(lv.color_hex(COL_TEXT), lv.STATE.PRESSED)
        home_btn.set_style_bg_opa(lv.OPA._10, lv.STATE.PRESSED)
        _clean(home_btn)
        home_src = _status_icon("home")
        if home_src is not None:
            home_icon = lv.img(home_btn)
            home_icon.set_src(home_src)
            home_icon.center()
            _no_hit(home_icon)
        else:
            # Fallback: use the built-in symbol when the PNG is missing
            home_icon = lv.label(home_btn)
            home_icon.set_text(_sym("HOME"))
            home_icon.set_style_text_color(lv.color_hex(COL_TEXT), 0)
            home_font = _font("font_montserrat_24", "font_montserrat_16")
            if home_font:
                home_icon.set_style_text_font(home_font, 0)
            home_icon.center()

        self.clock_imgs = None
        center = lv.label(status)
        center.set_text(center_text)
        center.set_style_text_color(lv.color_hex(COL_TEXT), 0)
        center.align(lv.ALIGN.CENTER, 0, 0)
        if show_clock:
            # The clock uses bitmap digits (HH:MM:SS = 6 digits + 2 colons). Each digit is
            # positioned absolutely relative to CENTER: a flex container would crop the
            # first and last digit symmetrically when its width falls short (hit on board).
            center.set_text("")
            slots_w = [CLOCK_CELL_W, CLOCK_CELL_W, CLOCK_COLON_W,
                       CLOCK_CELL_W, CLOCK_CELL_W, CLOCK_COLON_W,
                       CLOCK_CELL_W, CLOCK_CELL_W]
            total_w = sum(slots_w)
            imgs = _clock_imgs()
            colon_dsc = imgs.get(":")
            widgets = []
            x = -total_w // 2
            for slot, sw in enumerate(slots_w):
                im = lv.img(status)
                im.set_size(sw, CLOCK_H)
                if slot in (2, 5) and colon_dsc is not None:
                    im.set_src(colon_dsc)
                im.align(lv.ALIGN.CENTER, x + sw // 2, 0)
                _no_hit(im)
                widgets.append(im)
                x += sw
            self.clock_imgs = widgets
            self._update_clock()

        # Status icons on the right: battery hugs the edge, wifi sits to its left.
        # The battery icon carries four charge cells and is replaced on the 1s tick
        # from batt.status(), the same way wifi swaps its signal variant.
        batt_w = STATUS_BATT_W
        batt_icon = None
        batt_src = _status_icon("battery_" + str(_battery_level()))
        if batt_src is not None:
            batt_icon = lv.img(status)
            batt_icon.set_src(batt_src)
            batt_icon.align(lv.ALIGN.RIGHT_MID, -14, 0)
            _no_hit(batt_icon)
            self.batt_img = batt_icon
            self._battery_key = None
            self._update_battery_icon()
        else:
            self.batt_img = None
        wifi_src = _status_icon("wifi")
        if wifi_src is not None:
            wifi_icon = lv.img(status)
            wifi_icon.set_src(wifi_src)
            wifi_icon.align(lv.ALIGN.RIGHT_MID, -(14 + batt_w + 14), 0)
            _no_hit(wifi_icon)
            # Keep this widget so _update_wifi_icon can swap its src every second based
            # on connection state/rssi.
            # Every non-immersive screen builds its own; switching between home and app
            # screens repoints this attribute at the current screen's widget.
            # _wifi_key must be cleared: it caches "the variant last applied to a
            # widget", but this screen's widget is brand new (initial src = white full
            # bars). Left set, _update_wifi_icon would skip set_src when the state has not
            # changed and the app screen would keep the white full-bars icon (hit on
            # board).
            self.wifi_img = wifi_icon
            self._wifi_key = None
            self._update_wifi_icon()
        else:
            self.wifi_img = None
        if batt_src is None and wifi_src is None:
            # Fallback: both PNGs are missing, so use built-in symbols
            right = lv.label(status)
            parts = []
            wifi = _sym("WIFI")
            batt_sym = _sym("BATTERY_FULL", "BATTERY_3")
            if wifi:
                parts.append(wifi)
            if batt_sym:
                parts.append(batt_sym)
            right.set_text(" ".join(parts))
            right.set_style_text_color(lv.color_hex(COL_TEXT), 0)
            right.align(lv.ALIGN.RIGHT_MID, -14, 0)

        # Content area (from below the status bar down to the bottom of the screen)
        content = lv.obj(scr)
        content.set_size(DISPLAY_WIDTH, DISPLAY_HEIGHT - STATUS_H)
        content.align(lv.ALIGN.TOP_MID, 0, STATUS_H)
        content.set_style_bg_opa(lv.OPA.TRANSP, 0)
        content.set_style_border_width(0, 0)
        content.set_style_pad_all(0, 0)
        content.clear_flag(lv.obj.FLAG.SCROLLABLE)

        return scr, content, home_btn

    # ---------- Home screen ----------
    def _release_home_memory(self):
        """Destroy the home screen and release the tile bitmap cache (~1.5MB total) to
        hand a contiguous heap to a memory-hungry app.

        Prerequisite: the active screen has already switched away (deleting the active
        screen is never safe). dots/pager/clock_imgs point at objects on the deleted
        screen and must be cleared with it, otherwise the indicator update before
        returning home would dangle.
        On the way back, build_home takes the full rebuild path (tiles reload from the SD
        cache in about a second).
        """
        if self.home_scr is not None:
            # Save the page before the pager is destroyed. build_home() rebuilds it from
            # scratch after a release_home, and a fresh SnapPager always starts on page 0.
            if self.pager is not None:
                self.home_page = self.pager.page
            try:
                self.home_scr.delete()
                print("[GUI]   home screen deleted for release_home (page {})".format(
                    self.home_page))
            except BaseException as error:
                print("[GUI]   home screen delete error:", error)
            self.home_scr = None
        self.home_clock_imgs = None
        self.clock_imgs = None
        self.home_wifi_img = None
        self.wifi_img = None
        self.home_batt_img = None
        self.batt_img = None
        self.dots = []
        self.pager = None
        BAKED_TILES.clear()
        gc.collect()
        print("[GUI] home memory released | {}".format(_mem_str()))

    def _reload_tile_cache(self):
        """Read the tile bitmaps back in from the SD cache after release_home dropped
        them (no re-bake)."""
        t0 = time.ticks_ms()
        hits = 0
        for meta in APPS:
            cached = _cache_load(meta)
            if cached is not None:
                BAKED_TILES[meta["id"]] = cached
                hits += 1
        gc.collect()
        print("[GUI] tiles reloaded: {}/{} in {}ms | {}".format(
            hits, len(APPS), time.ticks_diff(time.ticks_ms(), t0), _mem_str()))

    def build_home(self):
        # The home screen is built exactly once: lv.scr_load does not destroy the old
        # screen, so rebuilding it on every app exit would pile up object trees forever
        # and make the UI slower the longer it runs.
        if self.home_scr is not None:
            lv.scr_load(self.home_scr)
            self.clock_imgs = self.home_clock_imgs
            self.wifi_img = self.home_wifi_img
            self.batt_img = self.home_batt_img
            # Back on the home widget: its src is frozen at whatever it was before the
            # app started (during the app, the per-second refresh went to the app
            # screen's widget). Clearing the key forces a fresh set_src on the next tick.
            self._wifi_key = None
            self._battery_key = None
            return
        # Coming back from an app that used release_home: the tile bitmaps were dropped,
        # so reload them from the SD cache first
        if not BAKED_TILES:
            self._reload_tile_cache()
        print("[GUI] build_home: shell...")
        scr, content, home_btn = self._build_shell("", show_clock=True)
        # A Home button is meaningless on the home screen (we are already there):
        # disable it so it cannot steal events or mislead
        disabled = getattr(lv.STATE, "DISABLED", None)
        if home_btn is not None and disabled is not None:
            home_btn.add_state(disabled)

        area_w = DISPLAY_WIDTH
        area_h = DISPLAY_HEIGHT - STATUS_H

        # Paging: at most 4 columns x 2 rows = 8 icons per page, horizontal swipe + snap
        cols, rows = 4, 2
        per_page = cols * rows
        pages = [APPS[i:i + per_page] for i in range(0, len(APPS), per_page)] or [[]]

        # Vertical distribution (symmetric, bottom-up):
        #   GRID_V_MARGIN | row1 gap_y row2 | DOTS_GAP | dots DOT_H | GRID_V_MARGIN
        # rows_avail is the height available to the two icon rows; the row gap is derived
        # from it backwards so no blank space piles up at the bottom.
        rows_avail = area_h - 2 * GRID_V_MARGIN - DOTS_GAP - DOT_H
        # Derive spacing from the screen width / available height: fill the page as much as
        # possible while keeping the left-right (top-bottom) margins equal.
        gap_x = TILE_PAD
        if cols > 1:
            gap_x = max(TILE_PAD, (area_w - 2 * GRID_MARGIN - cols * TILE_SIZE) // (cols - 1))
        gap_y = TILE_PAD
        if rows > 1:
            gap_y = max(TILE_PAD, (rows_avail - rows * TILE_SIZE) // (rows - 1))
        grid_h = rows * TILE_SIZE + (rows - 1) * gap_y
        dots_y = GRID_V_MARGIN + grid_h + DOTS_GAP
        print("[GUI] grid: {}x{} tile={} gap=({},{}) span={}x{} | dots_y={} (bottom margin={})"
              .format(cols, rows, TILE_SIZE, gap_x, gap_y,
                      cols * TILE_SIZE + (cols - 1) * gap_x, grid_h,
                      dots_y, area_h - (dots_y + DOT_H)))
        wrap = lv.obj(content)
        wrap.set_size(area_w, grid_h)
        wrap.align(lv.ALIGN.TOP_MID, 0, GRID_V_MARGIN)
        wrap.set_style_bg_opa(lv.OPA.TRANSP, 0)
        wrap.set_style_border_width(0, 0)
        wrap.set_style_pad_all(0, 0)
        wrap.set_flex_flow(lv.FLEX_FLOW.ROW)
        # Scroll container setup (clear pad_column/pad_row, disable momentum/elastic/
        # scrollbar, set the snap duration) comes from the paging module so the gallery
        # and other apps share one set of parameters.
        paging.setup_horiz_scroller(wrap, area_w)
        # With a single page, scrolling is disabled outright: FT5316 touch jitter would be
        # classified as a scroll, and LVGL then cancels the icons' CLICKED (which shows up
        # as icons being very hard to tap).
        if len(pages) <= 1:
            wrap.clear_flag(lv.obj.FLAG.SCROLLABLE)

        for page_apps in pages:
            page = lv.obj(wrap)
            page.set_size(area_w, grid_h)
            page.clear_flag(lv.obj.FLAG.SCROLLABLE)
            page.set_style_bg_opa(lv.OPA.TRANSP, 0)
            page.set_style_border_width(0, 0)
            page.set_flex_flow(lv.FLEX_FLOW.ROW_WRAP)
            # A full page (per_page icons) is spread out and centred; a partial page
            # starts at the top-left instead and is not centred.
            if len(page_apps) >= per_page:
                page.set_flex_align(lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER,
                                    lv.FLEX_ALIGN.CENTER)
                # 16px sideways plus the 8px centring adds up to 24, exactly GRID_MARGIN,
                # so the left edge lines up with a partial page.
                # No vertical pad: the content height already equals grid_h, and adding
                # more would overflow and push the rows away from where they belong.
                page.set_style_pad_left(GRID_PAD_L, 0)
                page.set_style_pad_right(GRID_PAD_L, 0)
                page.set_style_pad_top(0, 0)
                page.set_style_pad_bottom(0, 0)
            else:
                page.set_flex_align(lv.FLEX_ALIGN.START, lv.FLEX_ALIGN.START,
                                    lv.FLEX_ALIGN.START)
                # The left pad matches a full page's real outer margin, so the icons' left
                # edge does not jump when the page changes
                page.set_style_pad_left(GRID_MARGIN, 0)
                page.set_style_pad_right(GRID_PAD_L, 0)
                page.set_style_pad_top(0, 0)
                page.set_style_pad_bottom(0, 0)
            page.set_style_pad_row(gap_y, 0)
            page.set_style_pad_column(gap_x, 0)
            for meta in page_apps:
                self._make_app_tile(page, meta)

        # Page indicator dots (12px dot / 30x12 pill when active)
        if len(pages) > 1:
            self.dots = []
            dot_row = lv.obj(content)
            dot_row.set_size(area_w, DOT_H)
            dot_row.align(lv.ALIGN.TOP_MID, 0, dots_y)
            dot_row.set_style_bg_opa(lv.OPA.TRANSP, 0)
            dot_row.set_style_border_width(0, 0)
            dot_row.clear_flag(lv.obj.FLAG.SCROLLABLE)
            dot_row.set_flex_flow(lv.FLEX_FLOW.ROW)
            dot_row.set_flex_align(lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER, lv.FLEX_ALIGN.CENTER)
            dot_row.set_style_pad_all(0, 0)
            dot_row.set_style_pad_column(8, 0)
            for di in range(len(pages)):
                dot = lv.obj(dot_row)
                self._set_dot_style(dot, di == 0)
                self.dots.append(dot)

            # Manual snap: start page (recorded on touch-down) plus a flag for a
            # programmatic snap animation in flight.
            # paging.SnapPager owns the threshold test and the snapping (the same logic
            # the gallery and other apps use).
            snap_state = {"start_page": 0, "animating": False}

            def _set_dots(idx):
                for di, dot in enumerate(self.dots):
                    self._set_dot_style(dot, di == idx)

            def _on_page_changed(page):
                _set_dots(page)

            self.pager = paging.SnapPager(
                wrap, area_w, len(pages),
                on_page_changed=_on_page_changed,
                is_pressed=lambda: (TOUCH_INPUT is not None and
                                    TOUCH_INPUT.pressed))

        lv.scr_load(scr)
        print("[GUI] build_home: {} pages, {} apps | {}".format(
            len(pages), len(APPS), _mem_str()))
        # Restore the page the user left from. A rebuild drops the pager, and without
        # this the desktop always came back on page 0, which is what made CANCEL look
        # like it skipped the folder level entirely.
        if self.pager is not None and self.home_page > 0:
            if self.home_page < self.pager.page_count:
                self.pager.go(self.home_page, animate=False)
                for di, dot in enumerate(self.dots):
                    self._set_dot_style(dot, di == self.home_page)
                print("[GUI] home page restored: {}".format(self.home_page))
            else:
                self.home_page = 0
        self.home_scr = scr
        self.home_clock_imgs = self.clock_imgs
        # The home wifi icon needs its own reference: entering an app makes _build_shell
        # repoint self.wifi_img at the app screen's icon (or None when immersive), and the
        # early-return path back home restores from this copy.
        # Failing to keep it leaves wifi_img=None after returning home, so
        # _update_wifi_icon idles silently and the icon freezes at its boot value (hit on
        # board: the icon stayed stale after switching SSID and coming back).
        self.home_wifi_img = self.wifi_img
        self.home_batt_img = self.batt_img

    def _set_dot_style(self, dot, active):
        if active:
            dot.set_size(30, 12)
            dot.set_style_bg_color(lv.color_hex(ACCENT), 0)
        else:
            dot.set_size(12, 12)
            dot.set_style_bg_color(lv.color_hex(COL_DOT_OFF), 0)
        dot.set_style_bg_opa(lv.OPA.COVER, 0)
        dot.set_style_radius(6, 0)  # half of the 12px height; this binding has no 0xFFFF circle
        dot.set_style_border_width(0, 0)

    def _make_app_tile(self, parent, meta):
        # A tile is one baked bitmap (button + icon + label already rendered), so a
        # full-screen repaint during a scroll costs a bitmap blit, about 0ms.
        # The bitmap itself is clickable, so hit testing is unchanged: one CLICKED event
        # per tile.
        baked = BAKED_TILES.get(meta["id"])
        if baked is not None:
            dsc, _raw = baked
            tile = lv.img(parent)
            tile.set_src(dsc)
            tile.set_size(TILE_SIZE, TILE_SIZE)
            tile.add_flag(lv.obj.FLAG.CLICKABLE)
            tile.clear_flag(lv.obj.FLAG.SCROLLABLE)
            # lv.img is not in the scroll chain by default: a drag started on a tile must
            # bubble up to the scrollable wrap for paging to work
            for _fn in ("SCROLL_CHAIN", "SCROLL_CHAIN_HOR", "SCROLL_CHAIN_VER"):
                _f = getattr(lv.obj.FLAG, _fn, None)
                if _f is not None:
                    tile.add_flag(_f)
        else:
            # Baked bitmap missing (should never happen): fall back to an empty rounded
            # button placeholder
            tile = lv.obj(parent)
            tile.set_size(TILE_SIZE, TILE_SIZE)
            tile.set_style_bg_color(lv.color_hex(TILE_BG), 0)
            tile.set_style_radius(TILE_RADIUS, 0)
            tile.add_flag(lv.obj.FLAG.CLICKABLE)
            tile.clear_flag(lv.obj.FLAG.SCROLLABLE)
            lb = lv.label(tile)
            lb.set_text(meta["name"])
            lb.center()

        def on_clicked(event, m=meta):
            print("[GUI] tile clicked: '{}'".format(m["name"]))
            if m.get("folder"):
                self._open_folder(m)
            else:
                # A top-level tile has no folder level to return to. Clearing the
                # breadcrumb here (not only in close_app) keeps a failed launch from
                # leaving a stale one behind for the next app.
                self.return_folder = None
                self.open_app(m)

        # Click sound plus app launch (beep.on_click plays first, then invokes the callback)
        beep.on_click(tile, on_clicked)

    # ---------- Folder flyout ----------
    # The flyout lives on the home screen (no new screen), so the home tiles and paging
    # state stay exactly as they were and closing it only deletes one object tree.
    # Member tiles skip the tile bake (saving the cache and memory of 16 tiles) and are
    # drawn directly as an icon PNG, or an accent badge plus symbol.

    def _open_folder(self, meta):
        if self.home_scr is None:
            return
        self._close_folder()
        members = FOLDER_MEMBERS.get(meta["id"], [])
        if not members:
            print("[GUI] folder '{}' empty".format(meta["id"]))
            return

        # Backdrop: full-screen translucent black that swallows home-screen taps; tapping
        # it closes the flyout.
        # It is attached to the home screen (scr) rather than content because content is
        # only 424px tall and the backdrop has to cover the status bar.
        scr = self.home_scr
        backdrop = lv.obj(scr)
        backdrop.set_size(DISPLAY_WIDTH, DISPLAY_HEIGHT)
        backdrop.set_pos(0, 0)
        backdrop.set_style_bg_color(lv.color_hex(0x000000), 0)
        backdrop.set_style_bg_opa(lv.OPA._60, 0)
        backdrop.set_style_border_width(0, 0)
        backdrop.set_style_radius(0, 0)
        backdrop.clear_flag(lv.obj.FLAG.SCROLLABLE)
        backdrop.add_flag(lv.obj.FLAG.CLICKABLE)
        beep.on_click(backdrop, lambda e: self._close_folder())

        # Panel: same visual language as the Vision flyout (dark fill + border + large
        # radius).
        n = len(members)
        cols = 3
        rows = (n + cols - 1) // cols
        cell = 112                      # member tile (tap target >=100px; 3 rows still fit the 450px panel)
        gap = 12
        panel_w = cols * cell + (cols - 1) * gap + 32
        panel_h = rows * cell + (rows - 1) * gap + 76   # 76 = title row + top/bottom pad
        panel = lv.obj(backdrop)
        panel.set_size(panel_w, panel_h)
        panel.align(lv.ALIGN.CENTER, 0, 0)
        panel.set_style_bg_color(lv.color_hex(COL_BAR), 0)
        panel.set_style_bg_opa(lv.OPA.COVER, 0)
        panel.set_style_border_width(1, 0)
        panel.set_style_border_color(lv.color_hex(TILE_BORDER), 0)
        panel.set_style_radius(20, 0)
        panel.set_style_pad_all(0, 0)
        panel.clear_flag(lv.obj.FLAG.SCROLLABLE)
        try:
            panel.set_style_clip_corner(True, 0)
        except Exception:
            pass

        title = lv.label(panel)
        title.set_text(meta["name"])
        title.set_style_text_color(lv.color_hex(COL_TEXT), 0)
        tf = TILE_FONT or _font("font_montserrat_20", "font_montserrat_16")
        if tf:
            title.set_style_text_font(tf, 0)
        title.align(lv.ALIGN.TOP_MID, 0, 14)

        grid = lv.obj(panel)
        # The grid must be **one ring larger** than the tile array: when an edge tile grows
        # on press it would otherwise cross the grid boundary and be clipped (LVGL clips
        # children by default), which looks like the dialog cut it off. Centre tiles have
        # neighbours around them, so only edge tiles suffer.
        # Hence the EXPAND padding on all four sides with the grid size bumped by
        # 2*EXPAND, keeping the content area exactly equal to the tile array total (flex
        # lays out from inside the padding).
        grid_expand = 12
        content_w = cols * cell + (cols - 1) * gap
        content_h = rows * cell + (rows - 1) * gap
        grid.set_size(content_w + 2 * grid_expand,
                      content_h + 2 * grid_expand)
        grid.align(lv.ALIGN.TOP_MID, 0, 52 - grid_expand)
        grid.set_style_bg_opa(lv.OPA.TRANSP, 0)
        grid.set_style_border_width(0, 0)
        grid.set_style_pad_left(grid_expand, 0)
        grid.set_style_pad_right(grid_expand, 0)
        grid.set_style_pad_top(grid_expand, 0)
        grid.set_style_pad_bottom(grid_expand, 0)
        grid.set_style_pad_row(gap, 0)
        grid.set_style_pad_column(gap, 0)
        grid.set_flex_flow(lv.FLEX_FLOW.ROW_WRAP)
        grid.set_flex_align(lv.FLEX_ALIGN.START, lv.FLEX_ALIGN.START,
                            lv.FLEX_ALIGN.START)
        grid.clear_flag(lv.obj.FLAG.SCROLLABLE)

        for m in members:
            self._make_member_tile(grid, m)

        # Disable home scrolling while the flyout is open: if FT5316 jitter were taken for
        # a scroll, LVGL would cancel the CLICKED of the backdrop and members (the same trap
        # as the single-page no-scroll case, hit on board). Re-enabled on close.
        if self.pager is not None:
            self.pager.set_enabled(False)
        self.folder = {"meta": meta, "obj": backdrop}
        print("[GUI] folder '{}' opened: {} members | {}".format(
            meta["id"], n, _mem_str()))

    def _make_member_tile(self, parent, meta):
        tile = lv.obj(parent)
        tile.set_size(112, 112)
        tile.set_style_bg_color(lv.color_hex(TILE_BG), 0)
        tile.set_style_bg_opa(lv.OPA.COVER, 0)
        tile.set_style_radius(16, 0)
        tile.set_style_border_width(1, 0)
        tile.set_style_border_color(lv.color_hex(TILE_BORDER), 0)
        tile.set_style_pad_all(0, 0)
        tile.add_flag(lv.obj.FLAG.CLICKABLE)
        tile.clear_flag(lv.obj.FLAG.SCROLLABLE)
        self._press_feedback(tile)

        icon_src = None
        if meta.get("icon"):
            # Flyout member icons use the 64px version scaled at build time
            # (tools/gen_member_icons.py).
            # Do not use the 112px original with set_size(64, 64): on lv.img a size means
            # **clip**, not scale, so only a top-left block of the icon shows and it looks
            # truncated (measured on board).
            # This binding also has no set_scale/set_style_transform_scale, and SIZE_MODE
            # only affects layout, not rendering (modes 0..5 render identically), so
            # scaling can only be done offline.
            base = meta["icon"].rsplit("/", 1)[-1]
            icon_src = _load_png(_gui_dir + "/assets/cache/member_" + base)
            if icon_src is None:
                # Fallback: original image when the scaled copy is missing (it will be
                # clipped, but at least it is not blank)
                icon_src = _load_png(_gui_dir + "/" + meta["icon"])
        if icon_src is not None:
            ic = lv.img(tile)
            ic.set_src(icon_src)
            # The 64px version is placed at its native size; no set_size call (that would
            # clip it).
            ic.align(lv.ALIGN.TOP_MID, 0, 6)
            _no_hit(ic)
        else:
            # Placeholder member: accent round badge plus an LVGL symbol (built-in symbol
            # font, no TTF needed).
            idx = 0
            for fid, ms in FOLDER_MEMBERS.items():
                if meta in ms:
                    idx = ms.index(meta)
                    break
            accent = ACCENTS[idx % len(ACCENTS)]
            badge = lv.obj(tile)
            badge.set_size(56, 56)
            badge.align(lv.ALIGN.TOP_MID, 0, 10)
            badge.set_style_bg_color(lv.color_hex(accent), 0)
            badge.set_style_bg_opa(lv.OPA.COVER, 0)
            badge.set_style_radius(32, 0)
            badge.set_style_border_width(0, 0)
            # Padding must be cleared and SCROLLABLE turned off: the theme's default pad
            # eats space inside the circle, and once the symbol label overflows the badge
            # becomes scrollable, scrollbars appear at its right/bottom edge and push the
            # symbol out of view (hit on board: the coloured circle looked "hollow" with an
            # extra bar on its right).
            badge.set_style_pad_all(0, 0)
            badge.clear_flag(lv.obj.FLAG.SCROLLABLE)
            _no_hit(badge)
            sym = lv.label(badge)
            sym.set_text(meta.get("symbol", ""))
            sym.set_style_text_color(lv.color_hex(COL_TEXT), 0)
            f = _font("font_montserrat_20", "font_montserrat_16")
            if f:
                sym.set_style_text_font(f, 0)
            sym.center()
            _no_hit(sym)

        lab = lv.label(tile)
        lab.set_text(meta["name"])
        lab.set_style_text_color(lv.color_hex(NAME_COLOR), 0)
        f = _font("font_montserrat_14", "font_montserrat_16")
        if f:
            lab.set_style_text_font(f, 0)
        lab.align(lv.ALIGN.BOTTOM_MID, 0, -5)
        _no_hit(lab)

        def on_clicked(event, m=meta):
            print("[GUI] folder member clicked: '{}'".format(m["name"]))
            # Remember the folder this member came from. Exiting the app then reopens
            # this flyout, so CANCEL walks back the way the user came in instead of
            # jumping straight to the desktop.
            if self.folder is not None:
                self.return_folder = self.folder["meta"]
            # Close the flyout before opening the app: open_app switches screens and may
            # call release_home, which would destroy the flyout objects still attached to
            # the home screen and leave the state dangling.
            self._close_folder()
            self.open_app(m)

        beep.on_click(tile, on_clicked)

    def _press_feedback(self, obj):
        # Same treatment as app_network: grow slightly on press, restore on release.
        try:
            obj.set_style_transform_width(6, lv.STATE.PRESSED)
            obj.set_style_transform_height(6, lv.STATE.PRESSED)
        except Exception:
            pass

    def _close_folder(self):
        if self.folder is None:
            return
        try:
            self.folder["obj"].delete()
        except BaseException as error:
            print("[GUI] folder close error: {}".format(error))
        self.folder = None
        if self.pager is not None:
            self.pager.set_enabled(True)
        print("[GUI] folder closed | {}".format(_mem_str()))

    def back_out(self):
        """Walk back exactly one navigation level. Returns whether a layer was consumed.

        The levels, in order: the app (or its own handler) -> the folder flyout the app
        was entered from, or the desktop for a top-level app -> the desktop page the
        folder sits on -> page 0. Each press moves one level, so the user retraces the
        path they came in by instead of being dropped on the first page.

        Kept as one method rather than inlined in the key poll so the whole back-out
        ladder can be exercised without physical button events.
        """
        # An app may intercept first (gallery full screen -> grid, and so on).
        if self.ctx is not None:
            handler = self.ctx.back_handler
            if handler is not None:
                try:
                    if handler():
                        return True
                except BaseException as error:
                    print("[GUI] back handler error: {}".format(error))
            self.ctx.request_exit()
            return True

        # Desktop with a flyout open: close it and stay on this page.
        if self.folder is not None:
            self._close_folder()
            return True

        # Desktop with nothing open: step back one page. On page 0 there is nothing
        # left, which is what lets the last press end the back-out quietly.
        pager = self.pager
        if pager is not None and pager.page > 0:
            print("[GUI] CANCEL: page {} -> 0".format(pager.page))
            self.home_page = 0
            # go() overwrites an in-flight snap safely, so this also works right after
            # a swipe.
            pager.go(0)
            return True
        return False

    def _wifi_state_key(self):
        """Pick the wifi icon variant ("off"/"1"/"2"/"3") from net_mgr's connection
        state plus rssi. The bucket thresholds live in net_mgr and share their source with
        the signal colouring in the Network list."""
        try:
            return net_mgr.wifi_icon_key()
        except Exception:
            return "off"

    def _update_wifi_icon(self):
        """Refresh the status-bar wifi icon. Immersive apps have no status bar, so this
        is skipped when wifi_img is None."""
        img = getattr(self, "wifi_img", None)
        if img is None:
            return
        key = self._wifi_state_key()
        if key == getattr(self, "_wifi_key", None):
            return
        src = _status_icon("wifi_" + key)
        if src is not None:
            try:
                img.set_src(src)
                self._wifi_key = key
            except BaseException as error:
                print("[GUI] wifi icon error: {}".format(error))

    def _update_battery_icon(self):
        """Refresh the status-bar battery icon from the shared ADC cache.

        Immersive apps have no status bar, so this is skipped when batt_img is None.
        batt.status() is cached for 20s inside the module, so the 1s tick only costs a
        compare and a set_src when the charge cell actually changes.
        """
        img = getattr(self, "batt_img", None)
        if img is None:
            return
        key = _battery_level()
        if key == getattr(self, "_battery_key", None):
            return
        src = _status_icon("battery_" + str(key))
        if src is not None:
            try:
                img.set_src(src)
                self._battery_key = key
            except BaseException as error:
                print("[GUI] battery icon error: {}".format(error))

    def _update_clock(self):
        """Refresh the status-bar clock bitmaps. Only the home screen builds a clock;
        inside an app clock_imgs is None, so this returns immediately."""
        widgets = self.clock_imgs
        if not widgets:
            return
        try:
            tm = time.localtime()
            text = "{:02d}{:02d}{:02d}".format(tm[3], tm[4], tm[5])
        except Exception:
            text = "000000"
        imgs = _clock_imgs()
        di = 0
        for slot in range(8):
            if slot in (2, 5):
                continue
            dsc = imgs.get(text[di])
            if dsc is not None:
                widgets[slot].set_src(dsc)
            di += 1

    # ---------- App lifecycle ----------
    def open_app(self, meta):
        t0 = time.ticks_ms()
        print("[GUI] open '{}' begin | {}".format(meta["id"], _mem_str()))
        immersive = bool(meta.get("immersive"))
        ctx = AppContext()
        ctx.touch = TOUCH_INPUT
        if immersive:
            # Immersive: content area is the whole screen, no status bar, and the app
            # implements its own exit interaction
            ctx.width = DISPLAY_WIDTH
            ctx.height = DISPLAY_HEIGHT
            ctx.content_x = 0
            ctx.content_y = 0
            scr, content, home_btn = self._build_shell(meta["name"], show_clock=False,
                                                       immersive=True)
        else:
            scr, content, home_btn = self._build_shell(meta["name"], show_clock=False)
            ctx.content_x = 0
            ctx.content_y = STATUS_H
            if home_btn is not None:
                beep.on_click(
                    home_btn,
                    lambda e: (print("[GUI] home clicked"), ctx.request_exit()))
        ctx.app_name = meta["name"]

        # release_home: reclaim home-screen memory before a heavy AI or memory-sensitive
        # app starts.
        # Switch to this app's screen first (home_scr must no longer be active before it
        # can be deleted safely), then destroy the home screen and release the tile bitmap
        # cache, freeing a contiguous heap for the kmodel load and inference.
        if meta.get("release_home"):
            lv.scr_load(scr)
            self._release_home_memory()

        app = None
        startup_error = None
        try:
            module = __import__(meta["module"], None, None, ["create_app"])
            app = module.create_app(content, ctx)
        except AppUnavailable as error:
            # Hardware missing (camera not plugged in, for example): show a friendly notice
            # panel instead of exposing the underlying exception.
            print("[GUI] open '{}' unavailable: {}".format(meta["id"], error.message))
            startup_error = error
            gc.collect()
        except BaseException as error:
            print("[GUI] open {} failed: {}".format(meta["id"], error))
            sys.print_exception(error)
            # Truncate: the panel height is fixed at 320px and a long exception string
            # would cover the Cancel button.
            startup_error = AppUnavailable(
                "This app could not start.\n\n{}".format(error)[:160])

        self.active_app = app
        self.active_meta = meta
        self.ctx = ctx
        self.app_scr = scr
        self.app_content = content
        # Discard the down/up left over from "tap the icon to enter the app", otherwise
        # the app's tap detection may fire a spurious exit
        if TOUCH_INPUT is not None:
            TOUCH_INPUT.clear_raw()
        lv.scr_load(scr)
        if startup_error is not None:
            # The panel must be created after scr became the active screen (it is attached
            # to scr and deleted together with close_app).
            self._show_app_unavailable(scr, ctx, meta, startup_error, content)
        print("[GUI] open '{}' done in {}ms | {}".format(
            meta["id"], time.ticks_diff(time.ticks_ms(), t0), _mem_str()))

    def _show_app_unavailable(self, scr, ctx, meta, error, content=None):
        """Notice panel for an app that cannot start (missing hardware or a failed
        constructor).

        Key point: the panel does not depend on active_app existing. When construction
        fails active_app is None, so the main loop's ctx.want_exit check must sit outside
        the app branch; otherwise CANCEL only sets a flag nobody ever handles and the user
        is stuck on the error screen (hit on board).
        """
        # An app that failed to construct may already have registered a CANCEL
        # interceptor (Scan sets back_handler before opening its sensor, for one). It must
        # be cleared here, or CANCEL would be consumed by a half-initialised object and
        # never get the user out.
        ctx.back_handler = None

        # A failed app may already have built part of its widgets (status text, an empty
        # viewfinder). Those leftovers show through the 60% backdrop (a "camera init..."
        # line at the top, for instance), so hide the whole content layer.
        if content is not None:
            try:
                content.add_flag(lv.obj.FLAG.HIDDEN)
            except BaseException:
                pass

        # Backdrop: full-screen translucent black; tapping empty space also exits.
        backdrop = lv.obj(scr)
        backdrop.set_size(DISPLAY_WIDTH, DISPLAY_HEIGHT)
        backdrop.set_pos(0, 0)
        backdrop.set_style_bg_color(lv.color_hex(0x000000), 0)
        backdrop.set_style_bg_opa(lv.OPA._60, 0)
        backdrop.set_style_border_width(0, 0)
        backdrop.set_style_radius(0, 0)
        backdrop.set_style_pad_all(0, 0)
        backdrop.clear_flag(lv.obj.FLAG.SCROLLABLE)
        backdrop.add_flag(lv.obj.FLAG.CLICKABLE)
        beep.on_click(backdrop, lambda e: ctx.request_exit())

        # Panel: same visual language as the folder flyout (dark fill + border + large
        # radius).
        panel = lv.obj(backdrop)
        panel.set_size(600, 320)
        panel.align(lv.ALIGN.CENTER, 0, 0)
        panel.set_style_bg_color(lv.color_hex(COL_BAR), 0)
        panel.set_style_bg_opa(lv.OPA.COVER, 0)
        panel.set_style_border_width(1, 0)
        panel.set_style_border_color(lv.color_hex(TILE_BORDER), 0)
        panel.set_style_radius(20, 0)
        panel.set_style_pad_all(0, 0)
        panel.clear_flag(lv.obj.FLAG.SCROLLABLE)
        panel.add_flag(lv.obj.FLAG.CLICKABLE)

        title = lv.label(panel)
        title.set_text("{} unavailable".format(meta["name"]))
        title.set_style_text_color(lv.color_hex(COL_TEXT), 0)
        tf = _font("font_montserrat_24", "font_montserrat_20", "font_montserrat_16")
        if tf:
            title.set_style_text_font(tf, 0)
        title.align(lv.ALIGN.TOP_MID, 0, 30)

        msg = lv.label(panel)
        msg.set_text(error.message)
        msg.set_style_text_color(lv.color_hex(COL_MUTED), 0)
        mf = _font("font_montserrat_20", "font_montserrat_16")
        if mf:
            msg.set_style_text_font(mf, 0)
        msg.set_style_text_align(lv.TEXT_ALIGN.CENTER, 0)
        # set_style_max_width requires a selector (otherwise TypeError); excessively wide
        # text wraps automatically.
        try:
            msg.set_style_max_width(520, 0)
        except BaseException:
            msg.set_width(520)
        msg.align(lv.ALIGN.TOP_MID, 0, 100)

        # Cancel: the app-wide neutral button style, no brand cyan, because bright cyan
        # (0x4CC9F0) is too loud on a dark panel and clashes with the de-saturated palette
        # used by the home screen and flyouts.
        # The size uses the keyboard's 28px bold bitmap font (the same one as the password
        # keyboard's connect button: font_load pulls it into memory once and repaints are
        # blits). TILE_FONT cannot be used here: it is tiny_ttf, which measures
        # 485-1298ms per frame when re-rasterised under RENDER_MODE.FULL.
        btn = lv.btn(panel)
        btn.set_size(200, 60)
        btn.align(lv.ALIGN.BOTTOM_MID, 0, -36)
        _clean(btn)                     # clear the theme border/shadow first, then style it
        btn.set_style_radius(30, 0)
        btn.set_style_bg_color(lv.color_hex(TILE_BG), 0)
        btn.set_style_bg_opa(lv.OPA.COVER, 0)
        btn.set_style_border_width(1, 0)
        btn.set_style_border_color(lv.color_hex(TILE_BORDER), 0)
        # Pressed state: one level brighter fill plus a slight grow (same as app_network's
        # _press_feedback)
        btn.set_style_bg_color(lv.color_hex(0x2A3340), lv.STATE.PRESSED)
        try:
            btn.set_style_transform_width(6, lv.STATE.PRESSED)
            btn.set_style_transform_height(6, lv.STATE.PRESSED)
        except BaseException:
            pass
        lab = lv.label(btn)
        lab.set_text("Cancel")
        lab.set_style_text_color(lv.color_hex(COL_TEXT), 0)
        bold = None
        try:
            from ui import kb_layer
            bold = kb_layer.load_kb_font()
        except BaseException as error:
            print("[GUI] kb font unavailable: {}".format(error))
        if bold is None:
            bold = _font("font_montserrat_20", "font_montserrat_16")
        if bold:
            lab.set_style_text_font(bold, 0)
        lab.center()
        _no_hit(lab)
        beep.on_click(btn, lambda e: ctx.request_exit())
        print("[GUI] unavailable panel shown for '{}'".format(meta["id"]))

    def _fail_running_app(self):
        """Hardware dropped out at runtime: release the app, hide its UI and reuse the
        "app unavailable" panel.

        The only difference from a failed construction is timing: the app has already
        built its UI and run for some frames, so destroy() must run first to release the
        sensor/kpu, then active_app is cleared (close_app skips destroy when it is None,
        so nothing is released twice), and finally the same panel lets the user Cancel
        back to the home screen.
        """
        app = self.active_app
        ctx = self.ctx
        meta = self.active_meta or {"id": "?", "name": "App"}
        message = ctx.unavailable_msg
        ctx.unavailable_msg = None
        ctx.back_handler = None
        ctx.ok_pressed = False
        self.active_app = None
        if app is not None:
            try:
                app.destroy()
            except BaseException as error:
                print("[GUI]   runtime destroy error: {}".format(error))
        gc.collect()
        print("[GUI] app '{}' went unavailable while running: {}".format(
            meta["id"], message))
        self._show_app_unavailable(self.app_scr, ctx, meta,
                                   AppUnavailable(message), self.app_content)

    def close_app(self):
        t0 = time.ticks_ms()
        app = self.active_app
        app_id = self.active_meta["id"] if self.active_meta else "?"
        self.active_app = None
        self.active_meta = None
        self.ctx = None
        self.app_content = None
        # Note: do not clear self.dots -- home_scr is kept resident, dots belong to the
        # home indicators, and clearing them breaks the indicator update after returning
        # home and entering an app again (home is built only once).
        # The down/up of the exit gesture must not leak into the next app (or into the home
        # screen's tap detection)
        if TOUCH_INPUT is not None:
            TOUCH_INPUT.clear_raw()
        # A top-level tile clears the breadcrumb; only a folder member keeps one.
        returning_to = self.return_folder
        self.return_folder = None
        print("[GUI] close '{}' begin | {}".format(app_id, _mem_str()))
        if app is not None:
            try:
                app.destroy()
                print("[GUI]   app.destroy() ok")
            except BaseException as error:
                print("[GUI]   app.destroy() error: {}".format(error))
        # Switch back to the home screen before destroying the app screen: the active
        # screen must never be deleted, or LVGL's active screen dangles and touch
        # hit-testing stops working entirely (nothing responds).
        self.build_home()
        print("[GUI]   home restored | {}".format(_mem_str()))
        if self.app_scr is not None:
            try:
                self.app_scr.delete()
                print("[GUI]   app screen deleted | {}".format(_mem_str()))
            except BaseException as error:
                print("[GUI]   app screen delete error: {}".format(error))
            self.app_scr = None
        gc.collect()
        # Layered back-out, second step: the app was entered from a folder flyout, so
        # reopen that flyout on the home screen we just restored. The page is already
        # correct because build_home() restores self.home_page.
        if returning_to is not None:
            print("[GUI]   returning to folder '{}'".format(returning_to["id"]))
            self._open_folder(returning_to)
        print("[GUI] close '{}' done in {}ms | after gc: {}".format(
            app_id, time.ticks_diff(time.ticks_ms(), t0), _mem_str()))

    # ---------- Main loop ----------
    def run(self):
        self.build_home()
        # home is now the active screen, so only at this point is deleting the splash
        # safe (never delete the screen that is being displayed)
        if self.splash is not None:
            try:
                self.splash.delete()
            except BaseException:
                pass
            self.splash = None
        buttons = HardwareButtons()
        last_clock_update = time.ticks_ms()
        # Network service auto-start: net_mgr.start() is deliberately not called before
        # build_home, because start() immediately spins up the connect thread whose wlan
        # driver section holds the GIL and slows first-screen rendering and touch response
        # (measured: the splash sat on "preparing apps..." until WiFi produced a result).
        # Instead it starts NET_BOOT_DELAY_MS after the home screen appears, so the desktop
        # is interactive first and WiFi connects in the background.
        # Once the connection limit is reached the service stops, and only a manual
        # connect in the Network app brings it back.
        net_boot_due = time.ticks_add(last_clock_update, NET_BOOT_DELAY_MS)
        while True:
            os.exitpoint()
            if net_boot_due is not None and \
                    time.ticks_diff(time.ticks_ms(), net_boot_due) >= 0:
                net_boot_due = None
                try:
                    net_mgr.start()
                except BaseException as e:
                    print("[GUI] net start error:", e)
            # Click-sound shutdown: duty is zeroed when its time is up (an integer
            # comparison when idle)
            beep.poll()
            buttons.poll(self)
            if TOUCH_INPUT is not None:
                TOUCH_INPUT.poll()
            t0 = time.ticks_ms()
            next_wake = lv.task_handler()
            dt = time.ticks_diff(time.ticks_ms(), t0)
            PERF["task_sum"] += dt
            PERF["task_n"] += 1
            PERF["loop_n"] += 1
            if dt > PERF["task_max"]:
                PERF["task_max"] = dt
            app = self.active_app
            if app is not None:
                try:
                    app.step()
                except BaseException as error:
                    print("[GUI] app step error: {}".format(error))
                    sys.print_exception(error)
                    self.ctx.request_exit()
            # Hardware dropping out at runtime (camera unplugged mid-session, say): the app
            # only sets a flag and this is the single place that cleans up.
            # Ordered after app.step() and before want_exit: with the panel showing,
            # want_exit is still False, and only Cancel or the CANCEL key returns home.
            if self.ctx is not None and self.ctx.unavailable_msg is not None:
                self._fail_running_app()
            # The want_exit check must sit outside the app branch: when an app failed to
            # construct (AppUnavailable raised because the camera is missing, for example)
            # active_app is None and the screen holds only the notice panel, so CANCEL only
            # sets ctx.want_exit. Nested inside the if above, nobody would ever handle it
            # and the user would be stuck on the error panel.
            if self.ctx is not None and self.ctx.want_exit:
                self.close_app()
            elif app is None and self.pager is not None:
                # Home paging snap (poll driven; while an app runs it manages its own pager)
                self.pager.poll()
            # Network service is resident: the main loop drives its connection state
            # machine and NTP scheduling (inside and outside apps).
            # poll only compares ticks and advances state, which is negligible; blocking
            # work lives in net_mgr's own _thread and never passes through here.
            try:
                net_mgr.poll()
            except BaseException as error:
                print("[GUI] net poll error: {}".format(error))
            now = time.ticks_ms()
            if time.ticks_diff(now, last_clock_update) >= 1000:
                self._update_clock()
                self._update_wifi_icon()
                self._update_battery_icon()
                last_clock_update = now
            # Frame boost: while active (touching / just released) force an immediate
            # repaint every frame to break LVGL's LV_DEF_REFR_PERIOD(30ms) ceiling. Frames
            # with no dirty area return quickly from refr_now.
            active = TOUCH_INPUT is not None and TOUCH_INPUT.active()
            if active and FRAME_BOOST and REFR_NOW is not None:
                try:
                    REFR_NOW(disp_drv)
                except BaseException:
                    pass
            # Sleep until the next animation frame LVGL suggests (UINT32_MAX means idle),
            # but touch/key polling needs a short period, so idle is capped at 5ms and
            # animation follows the returned value (>=1ms, including scroll snap
            # animations).
            # Only drop to 1ms while an Up is awaiting the holdoff decision: that
            # granularity directly sets the post-release snap gap.
            if TOUCH_INPUT is not None and TOUCH_INPUT.busy():
                time.sleep_ms(1)
            elif active:
                # Active (dragging / snap animation): no sleep, refr_now pushes the frame
                # rate to the hardware ceiling.
                # This must be 0ms rather than 1ms, because 1ms actually costs more on
                # MicroPython.
                pass
            elif next_wake >= 0xFFFFFFFF or next_wake < 0:
                time.sleep_ms(5)
            else:
                time.sleep_ms(max(1, min(next_wake, 30)))


def _show_splash():
    """Boot splash: the first tile bake costs ~1.3s each, so this avoids a blank black
    wait. When the cache hits, it only flashes by."""
    scr = lv.obj()
    scr.set_style_bg_color(lv.color_hex(COL_BG), 0)
    scr.set_style_bg_opa(lv.OPA.COVER, 0)
    scr.set_style_border_width(0, 0)
    lb = lv.label(scr)
    lb.set_text(_sym("HOME") + " RockBase")
    lb.set_style_text_color(lv.color_hex(COL_TEXT), 0)
    font = _font("font_montserrat_24", "font_montserrat_16")
    if font:
        lb.set_style_text_font(font, 0)
    lb.align(lv.ALIGN.CENTER, 0, -10)
    tip = lv.label(scr)
    tip.set_text("preparing apps...")
    tip.set_style_text_color(lv.color_hex(COL_MUTED), 0)
    tip.align(lv.ALIGN.CENTER, 0, 24)
    lv.scr_load(scr)
    lv.task_handler()
    return scr


def main():
    # Display rotation is applied at the Display layer.
    Display.init(DISPLAY_TYPE, width=DISPLAY_WIDTH, height=DISPLAY_HEIGHT,
                 flag=DISPLAY_FLAG, to_ide=True)
    # Light the panel as early as possible: everything below can take a second (tile
    # bake) and the user must see that the board booted.
    _backlight_on()
    try:
        lvgl_setup()
        # Buzzer before the UI so the very first tap after boot has sound
        beep.init()
        # When a worker (connect/scan/NTP) holds the GIL inside a wlan C call, the click
        # sound cannot be muted by its shutdown thread and inevitably becomes a long tone;
        # let beep stay silent for that window instead.
        beep.set_busy_check(net_mgr.busy)
        splash = _show_splash()
        bake_all_tiles(splash)
        launcher = Launcher()
        launcher.splash = splash   # deleted safely in run() once home is loaded
        TouchScreen()
        launcher.run()
    except KeyboardInterrupt as e:
        print("[GUI] user stop:", e)
    except BaseException as e:
        sys.print_exception(e)
        raise
    finally:
        beep.shutdown()
        lv.deinit()
        Display.deinit()
        gc.collect()


if __name__ == "__main__":
    main()
