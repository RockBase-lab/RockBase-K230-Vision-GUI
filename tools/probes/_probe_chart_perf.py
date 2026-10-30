# Chart cost curve against point count, using the same FULL display
# setup as the GUI.

import image
import lvgl as lv
import time

lv.init()
disp = lv.disp_create(800, 480)
disp.set_color_format(lv.COLOR_FORMAT.ARGB8888)
imgs = [image.Image(800, 480, image.BGRA8888) for _ in range(2)]
disp.set_draw_buffers(imgs[0].bytearray(), imgs[1].bytearray(),
                      imgs[0].size(), lv.DISP_RENDER_MODE.FULL)
disp.set_flush_cb(lambda d, a, c: d.flush_ready())

scr = lv.obj()
lv.scr_load(scr)
lv.task_handler()

ch = lv.chart(scr)
ch.set_size(760, 380)
ch.set_pos(20, 40)

for name in ("set_type", "set_axis_range", "add_series", "set_point_count",
             "set_update_mode", "set_next_value", "set_y_axis_range",
             "set_x_axis_range", "set_div_line_count", "set_style_line_width"):
    print("chart.%s:" % name, hasattr(ch, name))

COLS = (0x60A5FA, 0x34D399, 0xFBBF24, 0xF87171, 0xA78BFA, 0x22D3EE, 0xFB923C)
series = []
for c in COLS:
    s = ch.add_series(lv.color_hex(c), lv.chart.AXIS.PRIMARY_Y)
    series.append(s)
print("series added:", len(series))

lv.task_handler()
t0 = time.ticks_us()
for _ in range(10):
    lv.refr_now(disp)
base = time.ticks_diff(time.ticks_us(), t0) // 10
print("baseline full refr_now: %d us (%.2f ms)" % (base, base / 1000.0))

for N in (60, 120, 200, 300):
    ch.set_point_count(N)
    for i in range(N):
        for j, s in enumerate(series):
            ch.set_next_value(s, ((i * (j + 1)) % 100) - 50)
    lv.task_handler()
    t0 = time.ticks_us()
    for k in range(20):
        for s in series:
            ch.set_next_value(s, k % 100 - 50)
        lv.refr_now(disp)
    dt = time.ticks_diff(time.ticks_us(), t0) // 20
    print("N=%d full refr_now w/7series: %d us (%.2f ms)" % (N, dt, dt / 1000.0))

print("DONE")
