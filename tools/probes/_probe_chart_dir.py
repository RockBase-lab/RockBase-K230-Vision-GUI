# Enumerates chart methods and constants to confirm the available API.

import image
import lvgl as lv

lv.init()
disp = lv.disp_create(800, 480)
disp.set_color_format(lv.COLOR_FORMAT.ARGB8888)
imgs = [image.Image(800, 480, image.BGRA8888) for _ in range(2)]
disp.set_draw_buffers(imgs[0].bytearray(), imgs[1].bytearray(),
                      imgs[0].size(), lv.DISP_RENDER_MODE.FULL)
disp.set_flush_cb(lambda d, a, c: d.flush_ready())
scr = lv.obj()
lv.scr_load(scr)
ch = lv.chart(scr)

meths = [a for a in dir(ch) if not a.startswith("_")]
print("axis/range/update methods:")
for a in meths:
    if "range" in a or "axis" in a or "update" in a or "shift" in a:
        print("  ", a)
print("UPDATE_MODE consts:", [a for a in dir(lv.chart.UPDATE_MODE)
                              if not a.startswith("_")]
      if hasattr(lv.chart, "UPDATE_MODE") else "NONE")
print("DONE")
