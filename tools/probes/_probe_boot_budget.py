# Boot budget: the exact cost of every resource a boot preload would load, in the
# same order the boot sequence will use. The progress bar needs real numbers: with
# guesswork weights it either jumps or freezes.
#
# Run with --lvgl (font_load needs a live LVGL). Prints one line per item with its
# own cost and the running total, then releases everything.
#
# Expected shape on this card: five fonts (~5 s, dominated by the OCR CJK font),
# thirteen kmodels (~5 s), the OCR dictionary (~0.1 s).

import gc
import time

import lvgl as lv
import nncase_runtime as nn

ASSETS = "/sdcard/factory/gui/assets/fonts"
KMODEL_DIR = "/sdcard/examples/kmodel"
DICT_PATH = "/sdcard/examples/utils/dict.txt"

# Bitmap fonts the apps load through lv.font_load. Order = the order they will be
# preloaded: small ones first so the bar moves immediately.
FONTS = [
    "mono_dejavu_16.bin",
    "ui_nunito_bold_22.bin",
    "kb_nunito_bold_28.bin",
    "plate_cjk_30.bin",
    "ocr_cjk_16.bin",
]

# Every model any GUI app can load, including the three the Detect app can switch
# to at runtime.
MODELS = [
    "face_detection_320.kmodel",
    "face_liveness_rgb.kmodel",
    "face_landmark.kmodel",
    "face_recognition_mobile.kmodel",
    "yolov8n_320.kmodel",
    "yolov8n_224.kmodel",
    "fruit_det_yolov8n_320.kmodel",
    "yolo26n.kmodel",
    "yolo11n-pose.kmodel",
    "yolo_license_plate_det.kmodel",
    "licence_reco.kmodel",
    "ocr_det_int16.kmodel",
    "ocr_rec_int16.kmodel",
]

hold_fonts = []
hold_models = []
total = 0

gc.collect()
print("B0 free at start={} ({} KB)".format(gc.mem_free(), gc.mem_free() // 1024))

# ---- fonts ----
fonts_total = 0
for name in FONTS:
    path = "A:" + ASSETS + "/" + name
    t0 = time.ticks_ms()
    try:
        font = lv.font_load(path)
    except BaseException as error:
        print("B1 {:26s} FAILED {}".format(name, error))
        continue
    dt = time.ticks_diff(time.ticks_ms(), t0)
    if font is None:
        print("B1 {:26s} returned None".format(name))
        continue
    hold_fonts.append(font)
    fonts_total += dt
    total += dt
    gc.collect()
    print("B1 {:26s} {:>5d} ms  total={:>5d} ms  free={} KB".format(
        name, dt, total, gc.mem_free() // 1024))
print("B2 fonts done: {} loaded, {} ms".format(len(hold_fonts), fonts_total))

# ---- dictionary ----
t0 = time.ticks_ms()
try:
    words = open(DICT_PATH, "r").read(100000).split("\r\n")
    dict_ms = time.ticks_diff(time.ticks_ms(), t0)
    total += dict_ms
    print("B3 dict {:>5d} ms entries={} free={} KB".format(
        dict_ms, len(words), gc.mem_free() // 1024))
    del words
except BaseException as error:
    print("B3 dict FAILED {}".format(error))

# ---- models ----
models_total = 0
for name in MODELS:
    path = KMODEL_DIR + "/" + name
    t0 = time.ticks_ms()
    try:
        kpu = nn.kpu()
        kpu.load_kmodel(path)
    except BaseException as error:
        dt = time.ticks_diff(time.ticks_ms(), t0)
        print("B4 {:32s} {:>5d} ms FAILED {}".format(name, dt, error))
        continue
    dt = time.ticks_diff(time.ticks_ms(), t0)
    hold_models.append(kpu)
    models_total += dt
    total += dt
    print("B4 {:32s} {:>5d} ms  total={:>5d} ms  resident={} free={} KB".format(
        name, dt, total, len(hold_models), gc.mem_free() // 1024))
print("B5 models done: {} resident, {} ms".format(len(hold_models), models_total))

gc.collect()
print("B6 SUMMARY fonts={} ms models={} ms grand_total={} ms ({:.1f} s)".format(
    fonts_total, models_total, total, total / 1000.0))
print("B7 free after everything={} ({} KB)".format(
    gc.mem_free(), gc.mem_free() // 1024))

for kpu in hold_models:
    try:
        kpu.release()
    except BaseException:
        pass
del hold_models
del hold_fonts
gc.collect()
print("B8 after release free={} ({} KB)".format(
    gc.mem_free(), gc.mem_free() // 1024))
print("RESULT: DONE")
