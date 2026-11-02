# Verifies the baked plate font loads on the board and can actually draw the
# Chinese characters the recogniser emits.
#
# font_load needs the LVGL FS drive prefix "A:"; without it the call returns None
# silently (docs/pitfalls.md). A loaded font that lacks a glyph draws a box, which
# looks identical to "font missing" in a screenshot, so the check also renders a
# label and reports the font's own metrics for a known character.
#
# Run with --lvgl: creating an lv object without display initialisation wedges the
# board.

import lvgl as lv

PATH = "A:/sdcard/factory/gui/assets/fonts/plate_cjk_30.bin"
SAMPLE = "渝A999U9"

load = getattr(lv, "font_load", None)
print("font_load available:", load is not None)
font = None
if load is not None:
    try:
        font = load(PATH)
        print("font_load ->", "OK" if font is not None else "None")
    except BaseException as error:
        print("font_load raised:", error)

if font is not None:
    try:
        print("line_height:", font.get_line_height())
    except BaseException as error:
        print("line_height unavailable:", error)

    screen = lv.scr_act()
    label = lv.label(screen)
    label.set_text(SAMPLE)
    label.set_style_text_font(font, 0)
    label.align(lv.ALIGN.CENTER, 0, 0)
    # Two frames: LVGL lays out on the first pass, so measurements read 0 before
    # the second one (measured).
    lv.refr_now(lv.disp_get_default())
    lv.task_handler()
    lv.refr_now(lv.disp_get_default())
    print("label width x height:", label.get_width(), "x", label.get_height())
    print("label text:", label.get_text())
    label.delete()
else:
    print("SKIPPED: no font")

print("RESULT: DONE")
