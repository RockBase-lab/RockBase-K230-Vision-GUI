# Does the expanded font actually contain glyphs for the characters the old subset
# boxed? Compares the rendered advance of a previously-missing string against a
# known-good control string, on one screen.
#
# Why a single screen: a label's width is resolved by the layout pass that runs when
# a screen is loaded, and neither set_text on a laid-out label nor a second scr_load
# refreshes it (measured: every later label read width 0). So everything to be
# measured is created before the one scr_load.
#
# Run with --lvgl.

import gc
import time

import lvgl as lv

FONT = "A:/sdcard/factory/gui/assets/fonts/ocr_cjk_16.bin"

# Characters the model reads but the 4036-glyph subset could not draw: from place
# names, the poem that was measured, and the GB2312 level-2 block.
PREVIOUSLY_MISSING = "圳岑樽裘恣谑馔丄丐丕丞丨丶乂乜亍亓亙亞亟亳亵亶仃仄仉仝仞仡佗佘佚佝佞佟"
# Control: level-1 characters that always rendered.
CONTROL = "中国人民北京上海广州天津重庆西安武汉成都杭州南京"

gc.collect()
print("E0 free={}KB".format(gc.mem_free() // 1024))

t0 = time.ticks_ms()
font = lv.font_load(FONT)
print("E1 font_load {}ms ok={}".format(
    time.ticks_diff(time.ticks_ms(), t0), font is not None))

if font is None:
    print("RESULT: FAIL")
else:
    scr = lv.obj()

    lab_a = lv.label(scr)
    lab_a.set_text(PREVIOUSLY_MISSING)
    lab_a.set_style_text_font(font, 0)
    lab_a.set_pos(4, 100)

    lab_b = lv.label(scr)
    lab_b.set_text(CONTROL)
    lab_b.set_style_text_font(font, 0)
    lab_b.set_pos(4, 160)

    lv.scr_load(scr)
    lv.task_handler()
    lv.task_handler()

    n_a = len(PREVIOUSLY_MISSING)
    n_b = len(CONTROL)
    w_a = lab_a.get_width()
    w_b = lab_b.get_width()
    adv_a = w_a / float(n_a)
    adv_b = w_b / float(n_b)
    print("E2 previous_missing n={} w={} advance={:.2f}px".format(n_a, w_a, adv_a))
    print("E3 control         n={} w={} advance={:.2f}px".format(n_b, w_b, adv_b))

    # A glyph present in the font advances by the font's full width; a missing one
    # falls back to a narrower box. Same advance within a few percent means the
    # previously-missing characters now have real glyphs.
    if w_b <= 0:
        print("RESULT: FAIL - control measured zero")
    else:
        ratio = adv_a / adv_b
        ok = 0.95 <= ratio <= 1.05
        print("E4 ratio={:.3f} -> {}".format(
            ratio, "MATCH" if ok else "MISMATCH"))
        print("RESULT: {}".format("PASS" if ok else "CHECK"))
