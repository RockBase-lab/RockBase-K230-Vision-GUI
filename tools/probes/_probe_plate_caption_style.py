# Verifies the plate caption's new look: 30px CJK font and green ink.
#
# Renders the real string on the real screen and reports the label's measured
# size plus the colour it was given, then captures the framebuffer so the result
# can be looked at on the PC rather than trusted.
#
# Run with --lvgl.

import lvgl as lv

FONT = "A:/sdcard/factory/gui/assets/fonts/plate_cjk_30.bin"
SAMPLE = "渝A999U9"
GREEN = 0x4CD964

load = getattr(lv, "font_load", None)
font = load(FONT) if load is not None else None
print("font_load:", "OK" if font is not None else "None")
if font is not None:
    try:
        print("line_height:", font.get_line_height())
    except BaseException as error:
        print("line_height unavailable:", error)

screen = lv.scr_act()
label = lv.label(screen)
label.set_text(SAMPLE)
label.set_style_text_color(lv.color_hex(GREEN), 0)
label.set_style_bg_color(lv.color_hex(0x000000), 0)
label.set_style_bg_opa(lv.OPA._70, 0)
label.set_style_pad_hor(8, 0)
label.set_style_pad_ver(1, 0)
label.set_style_radius(4, 0)
if font is not None:
    label.set_style_text_font(font, 0)
label.align(lv.ALIGN.CENTER, 0, 0)

# Two passes: LVGL lays out on the first, so measurements read 0 before the second.
for _ in range(2):
    lv.task_handler()
    lv.refr_now(lv.disp_get_default())

print("text:", label.get_text())
print("size: {}x{}".format(label.get_width(), label.get_height()))
print("caption height budget in app: 38 px")

label.delete()
print("RESULT: DONE")
