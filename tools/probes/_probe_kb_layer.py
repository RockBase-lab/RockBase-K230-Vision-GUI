# Headless keyboard layer: construct, poll, and close.
# Verifies that font_load returns a font, the mode enum resolves, the
# special-page guard restores the number page, and repeat guards roll back.

import sys
for m in ("ui.kb_layer", "apps.app_network"):
    sys.modules.pop(m, None)

import image
import lvgl as lv

lv.init()
disp = lv.disp_create(800, 480)
disp.set_color_format(lv.COLOR_FORMAT.ARGB8888)
b1 = image.Image(800, 480, image.BGRA8888)
b2 = image.Image(800, 480, image.BGRA8888)
disp.set_draw_buffers(b1.bytearray(), b2.bytearray(), b1.size(),
                      lv.DISP_RENDER_MODE.FULL)
disp.set_flush_cb(lambda drv, area, color: drv.flush_ready())

from ui import kb_layer

print("kb font:", kb_layer.load_kb_font())
modes = kb_layer.mode_consts()
print("modes:", modes)

scr = lv.obj()
lv.scr_load(scr)
kb = kb_layer.KbLayer(title="probe", on_ok=lambda: None, ok_text="ok")
print("built: panel={} ta={} kb={}".format(kb.panel is not None,
                                           kb.ta is not None,
                                           kb.kb is not None))
kb.ta.set_text("abc")
for _ in range(5):
    kb.poll(None)
print("text:", kb.get_text())

lower, upper, special, number, arabic = modes
kb.kb.set_mode(special)
kb.poll(None)
got = kb.kb.get_mode()
print("guard special->number: got={} want={} {}".format(
    got, number, "PASS" if got == number else "FAIL"))

kb._locked = True
before = kb.get_text()
kb.ta.set_text(before + "XX")
kb._on_changed(None)
after = kb.get_text()
print("repeat-block: before={} after={} {}".format(
    before, after, "PASS" if after == before else "FAIL"))

kb.close()
print("closed: panel={}".format(kb.panel))
print("PROBE_DONE")
