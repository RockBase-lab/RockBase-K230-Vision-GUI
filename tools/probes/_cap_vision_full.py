# Checks that an immersive app screen has no status bar and that the
# content area is the full screen.

import sys
import time
import gc
import ubinascii
import uctypes

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("apps.app_vision", "apps", "app_error", "gui"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv

_f = open("/sdcard/factory/gui/launcher.py")
gui_src = _f.read()
_f.close()
del _f

G = {"__name__": "gui_probe"}
exec(compile(gui_src, "launcher.py", "exec"), G)
print("L1 gui loaded")

launcher = G["Launcher"]()
meta = None
for item in G["FOLDER_MEMBERS"]["vision"]:
    if item["id"] == "detect":
        meta = item
        break
print("L2 meta:", meta["name"], meta["module"], "immersive=", meta.get("immersive"))

t0 = time.ticks_ms()
launcher.open_app(meta)
print("L3 open_app {}ms".format(time.ticks_diff(time.ticks_ms(), t0)))

app = launcher.active_app
print("L4 app={} ctx={}x{}".format(app is not None, launcher.ctx.width,
                                   launcher.ctx.height))

for _ in range(2):
    lv.task_handler()
    time.sleep_ms(60)

scr = launcher.app_scr
n_child = scr.get_child_cnt()
print("L5 app_scr children={}".format(n_child))
status_found = False
for i in range(n_child):
    ch = scr.get_child(i)
    try:
        cw, chh = ch.get_width(), ch.get_height()
    except BaseException:
        continue
    if cw == 800 and chh == G["STATUS_H"]:
        status_found = True
    print("     child[{}] {}{}x{} at ({},{})".format(
        i, type(ch).__name__, cw, chh, ch.get_x(), ch.get_y()))
print("L6 status_bar_present={}".format(status_found))

content = launcher.app_content
print("L7 content={}x{} at ({},{})".format(content.get_width(), content.get_height(),
                                           content.get_x(), content.get_y()))

bar = app.stat_btn
print("L8 topbar x={} y={} w={} h={}".format(bar.get_x(), bar.get_y(),
                                             bar.get_width(), bar.get_height()))

for _ in range(6):
    launcher.active_app.step()
    lv.task_handler()
    time.sleep_ms(120)
print("L9 objs={} fps={} stat='{}'".format(app.last_objs, app.fps_text,
                                           app.stat_model.get_text()))

try:

    app.img_widget.add_flag(lv.obj.FLAG.HIDDEN)
    for _ in range(2):
        lv.task_handler()
        time.sleep_ms(50)
    print("L10 preview hidden, mem_free={}".format(gc.mem_free()))

    dsc = lv.snapshot_take(lv.scr_act(), lv.COLOR_FORMAT.ARGB8888)
    w = dsc.header.w
    h = dsc.header.h
    ptr = int.from_bytes(bytes(memoryview(dsc.data)), "little")
    raw = uctypes.bytearray_at(ptr, w * h * 4)
    out = bytearray()
    y = 0
    while y < h:
        base = y * w * 4
        run = 1
        b0 = raw[base]
        g0 = raw[base + 1]
        r0 = raw[base + 2]
        for x in range(1, w):
            o = base + x * 4
            b = raw[o]
            g = raw[o + 1]
            r = raw[o + 2]
            if b == b0 and g == g0 and r == r0 and run < 256:
                run += 1
            else:
                out.append(run - 1)
                out.append(b0)
                out.append(g0)
                out.append(r0)
                b0, g0, r0 = b, g, r
                run = 1
        out.append(run - 1)
        out.append(b0)
        out.append(g0)
        out.append(r0)
        y += 1
    dsc.buf_free()

    print("SHOT visionfull %d %d %d %d" % (w, h, w * h * 4, len(out)))
    b64 = ubinascii.b2a_base64(bytes(out)).decode().strip()
    del out
    gc.collect()
    for i in range(0, len(b64), 800):
        print("B64 " + b64[i:i + 800])
    print("SHOTEND visionfull")
except BaseException as e:
    sys.print_exception(e)

G["_ensure_cache_dir"]()
launcher.close_app()
print("L11 closed, sensor released")
