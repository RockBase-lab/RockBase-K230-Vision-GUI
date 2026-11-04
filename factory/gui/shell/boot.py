# Boot preload: load every blocking resource before the desktop appears, driving the
# splash bar.
#
# Every app takes its kpu and its bitmap fonts from model_pool / font_pool, both of
# which load on first use. Loading them here instead makes an app's open path a dict
# lookup: measured on board, OCR went from 2151 ms to 253-320 ms once its two models
# were already resident.
#
# Order matters, and Wi-Fi goes last. Overlapping the connect worker with the asset
# loading was measured at 24.7 s total: a failing wlan.connect() holds the GIL for
# ~9 s, which stretched the tile step from 1.2 s to 7.4 s and the assets from 11 s
# to 24.7 s. Sequentially the assets keep their own speed and only the connect
# itself is slow.

import time

from core import font_pool
from core import i18n
from core import model_pool
from core import ocr_dict
from net import service as net_mgr
from shell import tile_cache

BOOT_PRELOAD_MODELS = True   # False = load models inside each app (comparison / debug)
BOOT_PRELOAD_FONTS = True    # False saves 5.5 s of boot, at the cost of a slow first open

# Step weights are measured milliseconds, not item counts, so the bar advances at a
# believable rate instead of jumping through six 100 ms steps and then sitting still
# for 5 s. Re-measure with tools/probes/_probe_boot_budget.py and _probe_font_boot.py
# after changing the asset set.
BOOT_DICT_MS = 239           # OCR dictionary, measured
BOOT_TILES_MS = 900          # tile cache read, measured
BOOT_HOME_MS = 1000          # build_home, measured

# Boot-time Wi-Fi budget: one attempt inside a hard 15 s window. wlan.connect() to an
# unreachable AP blocks ~9 s inside the C layer and cannot be interrupted from
# Python, so the retry ladder (3 tries, 10 s apart) would hold the progress screen
# for tens of seconds. When the window closes the desktop appears without a network
# and the status bar stays disconnected until the user connects from the Wi-Fi app.
BOOT_WIFI_TIMEOUT_MS = 15000

KMODEL_DIR = "/sdcard/examples/kmodel"

# Every model any app can load, minus the three Detect alternates, with its measured
# load cost. Seventeen resident kpus are verified safe when the Sensor is built at
# sixteen first and the app loads its extra model after (Gesture + handkp_det); the
# alternates stay on demand in app_vision because nineteen is unmeasured.
# The per-item costs are what keep the bar honest: the OCR pair alone is 1409 ms.
BOOT_MODELS = [
    ("face_detection_320.kmodel", 186),
    ("face_liveness_rgb.kmodel", 74),
    ("face_landmark.kmodel", 98),
    ("face_recognition_mobile.kmodel", 162),
    ("yolov8n_320.kmodel", 566),
    ("yolo11n-pose.kmodel", 831),
    ("yolo_license_plate_det.kmodel", 907),
    ("licence_reco.kmodel", 75),
    ("ocr_det_int16.kmodel", 599),
    ("ocr_rec_int16.kmodel", 810),
    ("person_detect_yolov5n.kmodel", 376),
    ("yolov5n-falldown.kmodel", 366),
    ("hand_det.kmodel", 496),
    ("yolo11n-obb.kmodel", 834),
    ("yolov8n_seg_320.kmodel", 676),
    ("face_parse.kmodel", 515),
]

# Every baked bitmap font, cheap to expensive with its measured load cost. 7469 of
# the 7969 ms total is the 6549-glyph OCR font, so it has to carry most of the bar.
BOOT_FONTS = [
    ("mono_dejavu_16.bin", 106),
    ("ui_nunito_bold_22.bin", 110),
    ("kb_nunito_bold_28.bin", 119),
    ("ui_cjk_16.bin", 569),
    ("ui_cjk_28.bin", 193),
    ("plate_cjk_30.bin", 164),
    ("ocr_cjk_16.bin", 7469),
]


def boot_load(splash):
    """Load every blocking resource before the desktop appears, driving the bar."""
    t_all = time.ticks_ms()

    # Total is the sum of the measured step costs, so the bar always lands on 100%
    # exactly when the last step finishes.
    span = BOOT_TILES_MS + BOOT_HOME_MS + BOOT_DICT_MS
    if BOOT_PRELOAD_FONTS:
        for _name, ms in BOOT_FONTS:
            span += ms
    if BOOT_PRELOAD_MODELS:
        for _name, ms in BOOT_MODELS:
            span += ms
    done = 0
    splash.set(0, span, i18n.t("boot.preparing"), i18n.t("boot.tiles"))

    # ---- 1. tiles ----
    t0 = time.ticks_ms()
    tile_cache.bake_all_tiles(splash.scr)
    print("[BOOT] tiles {}ms".format(time.ticks_diff(time.ticks_ms(), t0)))
    done += BOOT_TILES_MS
    splash.set(done, span, i18n.t("boot.preparing"), i18n.t("boot.tiles_done"))

    # ---- 2. fonts ----
    t_step = time.ticks_ms()
    fonts_ok = 0
    if BOOT_PRELOAD_FONTS:
        for i in range(len(BOOT_FONTS)):
            name, ms = BOOT_FONTS[i]
            splash.set(done, span, i18n.t("boot.fonts").format(
                i + 1, len(BOOT_FONTS)), name)
            if font_pool.get(name) is not None:
                fonts_ok += 1
            done += ms
            splash.set(done, span, i18n.t("boot.fonts").format(
                i + 1, len(BOOT_FONTS)), name)
    else:
        splash.note(i18n.t("boot.fonts_ondemand"))
    print("[BOOT] fonts {}ms loaded={}/{}".format(
        time.ticks_diff(time.ticks_ms(), t_step), fonts_ok, len(BOOT_FONTS)))

    # ---- 3. OCR dictionary ----
    t_step = time.ticks_ms()
    splash.set(done, span, i18n.t("boot.dict"), "dict.txt")
    dict_ok = ocr_dict.words() is not None
    done += BOOT_DICT_MS
    print("[BOOT] dict {}ms ok={} entries={}".format(
        time.ticks_diff(time.ticks_ms(), t_step), dict_ok,
        len(ocr_dict.words()) if dict_ok else 0))

    # ---- 4. models ----
    if BOOT_PRELOAD_MODELS:
        for i in range(len(BOOT_MODELS)):
            name, ms = BOOT_MODELS[i]
            splash.set(done, span, i18n.t("boot.models").format(
                i + 1, len(BOOT_MODELS)), name)
            try:
                model_pool.get(KMODEL_DIR + "/" + name)
            except BaseException as error:
                # One bad model must not stop the boot: the app that needs it shows
                # the existing "unavailable" panel instead.
                print("[BOOT] model skipped {}: {}".format(name, error))
            done += ms
            splash.set(done, span, i18n.t("boot.models").format(
                i + 1, len(BOOT_MODELS)), name)
    print("[BOOT] assets done {}ms resident_models={}".format(
        time.ticks_diff(time.ticks_ms(), t_all), model_pool.loaded_count()))

    # ---- 5. network decision (last: it can hold the GIL for seconds) ----
    wifi_waiting = False
    t_wifi = time.ticks_ms()
    try:
        wifi_waiting = net_mgr.boot_start()
    except BaseException as error:
        print("[BOOT] wifi start failed: {}".format(error))
    if wifi_waiting:
        splash.set(done, span, i18n.t("boot.wifi"),
                   i18n.t("boot.wifi_hint").format(BOOT_WIFI_TIMEOUT_MS // 1000))
        outcome = net_mgr.boot_wait(BOOT_WIFI_TIMEOUT_MS)
        if outcome == "connected":
            splash.note(i18n.t("boot.wifi_connected").format(net_mgr.ip()))
        elif outcome == "idle":
            splash.note(i18n.t("boot.wifi_notconfig"))
        else:
            # Warning colour: the boot continues, the desktop just has no network
            # until the user connects from the Wi-Fi app.
            splash.warn(i18n.t("boot.wifi_failed"))
    else:
        splash.note(i18n.t("boot.wifi_ok") if net_mgr.state == net_mgr.CONNECTED
                    else i18n.t("boot.wifi_notconfig"))
    # Measured: the failing path is ~9 s inside wlan.connect() plus radio bring-up,
    # and none of it can be interrupted from Python. Logged separately so a slow boot
    # can be attributed to the network instead of the assets.
    print("[BOOT] wifi phase {}ms".format(
        time.ticks_diff(time.ticks_ms(), t_wifi)))

    done += BOOT_HOME_MS
    splash.set(done, span, i18n.t("boot.preparing"), i18n.t("boot.building"))
    print("[BOOT] total {}ms".format(time.ticks_diff(time.ticks_ms(), t_all)))
