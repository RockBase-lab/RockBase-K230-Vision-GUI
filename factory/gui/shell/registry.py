# Which apps exist on the desktop, and how their tiles are tinted.
#
# tools/gen_tile_cache.py, tools/gen_app_icons.py and tools/_doctor.py parse this
# file as text. Keep each entry on the documented shape: one "id": "value" pair per
# line inside APPS / FOLDER_MEMBERS, and one "id": 0xRRGGBB row per line inside
# APP_ACCENTS, or the regexes silently produce a stale artefact.
#
# An entry becomes a folder tile with "folder": True; its members live in
# FOLDER_MEMBERS under the same id and use the same meta shape.

import lvgl as lv


def _sym(*names):
    """Look up an LVGL built-in symbol by candidate names, tolerating the symbol
    differences between firmware bindings."""
    for name in names:
        symbol = getattr(lv.SYMBOL, name, None)
        if symbol:
            return symbol
    return ""



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
    "battery":  0xC9AE7A,
    "imu":      0x7FA6CC,
    "network":  0x74B5AC,
    "settings": 0x9CA3AF,
    "video":    0xCC8A92,
    "scan":     0xA9BE7C,
    "face":     0xD9A76A,
    "gesture":  0x8FBF9F,
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


# App registry: id / display name / icon PNG (relative to the gui dir, optional) /
# symbol fallback / module.
# An entry with "folder": True is a group tile (iOS-folder style): clicking it opens a
# member picker layer (_open_folder) instead of an app. Members live in FOLDER_MEMBERS
# and use the same meta shape as top-level entries, so flags such as
# immersive/release_home still apply.
# The top level holds 11 entries = two pages of a 4x2 grid (8 + 3). Page 1 is the
# daily set (capture, detect, tune); page 2 keeps the things configured once and
# then left alone (Network, Settings) plus Battery. Placeholder entries point at
# apps.app_placeholder; swap in apps.app_xxx once the real app exists. Board facts
# (firmware, heap, model count) live in the Settings app, not a System tile.
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
    # Face group: chained face pipeline (detect -> align -> embed). Its pipeline
    # differs from the single-model family in Vision and it has to maintain a local
    # face database, so it stands alone as a group.
    {"id": "face", "name": "Face", "icon": "assets/icon_face.png",
     "symbol": _sym("USER"), "folder": True},
    # Vision group: single entry point for every single-model detection app
    # (members in FOLDER_MEMBERS).
    {"id": "vision", "name": "Vision", "icon": "assets/icon_vision.png",
     "symbol": _sym("CPU", "CHARGE"), "folder": True},
    {"id": "audio", "name": "Audio", "icon": "assets/icon_audio.png",
     "symbol": _sym("MUSIC", "LOUD", "VOLUME_MID"), "module": "apps.app_audio"},
    {"id": "touch", "name": "Touch", "icon": "assets/icon_touch.png",
     "symbol": _sym("TOUCHSCREEN", "TOUCH", "EYE_OPEN"), "module": "apps.app_touch"},
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
    # ---- Page 2 ----
    # Network and Settings are configured once and then left alone, so they sit
    # behind a swipe instead of taking a slot on the daily page.
    {"id": "network", "name": "Network", "icon": "assets/icon_network.png",
     "symbol": _sym("WIFI"), "module": "apps.app_network"},
    {"id": "settings", "name": "Settings", "icon": "assets/icon_settings.png",
     "symbol": _sym("SETTINGS"), "module": "apps.app_settings"},
    # Battery: single-cell pack voltage through the ADC5 divider. Sampling runs
    # inside step() one reading at a time so the UI never blocks.
    # (A separate System tile used to sit after IMU; its board facts are now the
    # SYSTEM section of the Settings app.)
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
        # Segment: instance masks tinted over the preview from an ARGB buffer.
        # Runs at 640x384 preview: the display-sized masks buffer (W*H*4) is
        # 1.5MB at 800x480 and does not fit the heap after release_home.
        {"id": "segment", "name": "Segment", "icon": "assets/icon_segment.png",
         "symbol": _sym("CROP"), "module": "apps.app_segment",
         "immersive": True, "release_home": True},
        # Orient: rotated boxes (yolo11n-obb), drawn as closed lv.line quads.
        {"id": "orient", "name": "Orient", "icon": "assets/icon_orient.png",
         "symbol": _sym("ROTATE_RIGHT"), "module": "apps.app_orient",
         "immersive": True, "release_home": True},
        # Person / Fall / Gesture: the three yolov5 anchor-based detectors share
        # apps/anchor_det.py (3-output decode via aicube.anchorbasedet_post_process).
        {"id": "person", "name": "Person", "icon": "assets/icon_person.png",
         "symbol": _sym("USER"), "module": "apps.app_person",
         "immersive": True, "release_home": True},
        {"id": "fall", "name": "Fall", "icon": "assets/icon_fall.png",
         "symbol": _sym("WARNING"), "module": "apps.app_fall",
         "immersive": True, "release_home": True},
        # Gesture: hand_det + handkp_det with an angle-rule classifier; the
        # second model loads on demand through the pool, not at boot.
        {"id": "gesture", "name": "Gesture", "icon": "assets/icon_gesture.png",
         "symbol": _sym("HAND"), "module": "apps.app_gesture",
         "immersive": True, "release_home": True},
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
         "symbol": _sym("SHIELD"), "module": "apps.app_mask",
         "immersive": True, "release_home": True},
    ],
}
