# Desktop string table and language state.
#
# One entry per app id: (en, zh). The zh half must stay inside the character set
# baked into ui_cjk_28.bin (tools/ui_cjk_charset.txt); a character outside it
# renders as a box on board. APPS/FOLDER_MEMBERS keep the English name as the
# display default; every localized consumer asks here.
#
# Language changes are hot: set_lang() persists to config and raises a flag the
# launcher main loop consumes to rebuild the home screen (tile bitmaps are baked
# per language, see cache_path in shell/tile_cache.py). Apps keep running; their
# own strings are not localized yet.

from core import config

LANGS = ("en", "zh")
CJK_FONT = "ui_cjk_28.bin"

NAMES = {
    # Top-level tiles
    "camera": ("Camera", "相机"),
    "gallery": ("Gallery", "相册"),
    "scan": ("Scan", "扫码"),
    "face": ("Face", "人脸"),
    "vision": ("Vision", "视觉"),
    "audio": ("Audio", "音频"),
    "touch": ("Touch", "触控"),
    "imu": ("IMU", "惯导"),
    "network": ("Network", "网络"),
    "settings": ("Settings", "设置"),
    "battery": ("Battery", "电量"),
    # Vision folder members
    "detect": ("Detect", "检测"),
    "pose": ("Pose", "骨骼"),
    "segment": ("Segment", "分割"),
    "orient": ("Orient", "朝向"),
    "person": ("Person", "行人"),
    "fall": ("Fall", "摔倒"),
    "gesture": ("Gesture", "手势"),
    "plate": ("Plate", "车牌"),
    "ocr": ("OCR", "文字"),
    # Face folder members
    "face_det": ("Faces", "人脸"),
    "landmark": ("Points", "关键点"),
    "recognize": ("Recognize", "识别"),
    "liveness": ("Liveness", "活体"),
    "mask": ("Mask", "口罩"),
}

_lang = "en"
_home_dirty = False


def init():
    """Read the stored language after config.load(). Unknown values fall back to en."""
    global _lang
    value = config.get("ui.language", "en")
    _lang = value if value in LANGS else "en"
    return _lang


def lang():
    return _lang


def set_lang(code):
    """Switch language and persist. Returns True when the value actually changed."""
    global _lang, _home_dirty
    if code not in LANGS or code == _lang:
        return False
    _lang = code
    config.set("ui.language", code)
    _home_dirty = True
    return True


def mark_home_dirty():
    """Force a home rebuild without changing the language (restore defaults)."""
    global _home_dirty
    _home_dirty = True


def consume_home_dirty():
    global _home_dirty
    if _home_dirty:
        _home_dirty = False
        return True
    return False


def name(entry_id, fallback=""):
    pair = NAMES.get(entry_id)
    if pair is None:
        return fallback
    return pair[1] if _lang == "zh" else pair[0]


def needs_cjk(text):
    """True when text has a non-ASCII code point: the Latin faces have no CJK glyphs."""
    for ch in text:
        if ord(ch) > 127:
            return True
    return False
