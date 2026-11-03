# Font load cost and glyph coverage for the expanded OCR font.
#
# Two questions at once: how long the 6549-glyph build takes to load (that number
# becomes the boot progress weight), and whether a sample of characters the model
# can emit but the old 4036-glyph build could not draw now has a real glyph.
#
# Run with --lvgl.

import gc
import time

import lvgl as lv

PATH = "A:/sdcard/factory/gui/assets/fonts/ocr_cjk_16.bin"

# Characters that used to render as boxes: from place names, poetry and the
# GB2312 level-2 block the model can read.
SAMPLE = "圳岑樽裘恣谑馔丄丐丕丞丨丶乂乜亍亓亙亞亟亳亵亶仃仄仉仝仞仡佗佘佚佝佞佟"
MISSING_BEFORE = "呸墒撅毖沏沤烃瓤硷耪蓑谰"

gc.collect()
print("G0 free before={}KB".format(gc.mem_free() // 1024))

t0 = time.ticks_ms()
font = lv.font_load(PATH)
load_ms = time.ticks_diff(time.ticks_ms(), t0)
print("G1 font_load {}ms -> {} free={}KB".format(
    load_ms, "ok" if font is not None else "None", gc.mem_free() // 1024))

if font is None:
    print("RESULT: FAIL - font did not load")
else:
    # A label with the sample text: if a glyph is absent it renders as a box, which
    # still "succeeds", so the useful signal is that the font loaded and the label
    # can be laid out with the expected width (boxes are a different width).
    scr = lv.obj()
    lab = lv.label(scr)
    lab.set_text(SAMPLE)
    lab.set_style_text_font(font, 0)
    lv.scr_load(scr)
    lv.task_handler()
    lv.task_handler()
    print("G2 sample layout w={} h={}".format(lab.get_width(), lab.get_height()))

    # Per-glyph advance: a 16 px font should be about 16 px per hanzi. Boxes are
    # usually narrower, so a width near len*16 is the evidence glyphs exist.
    print("G3 expect~{} got {}".format(len(SAMPLE) * 16, lab.get_width()))

    lab.set_text(MISSING_BEFORE)
    lv.task_handler()
    lv.task_handler()
    print("G4 level-1 gaps w={} (expect~{})".format(
        lab.get_width(), len(MISSING_BEFORE) * 16))

    try:
        scr.delete()
    except BaseException:
        pass

print("RESULT: DONE")
