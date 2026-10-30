# Runs the IMU app inside the real launcher and context.
# Checks that touch is wired, counts errors, and measures the frame rate.

import gc
import sys
import time
import ubinascii

import lvgl as lv

for _k in ("apps.app_imu", "app_imu", "apps"):
    if _k in sys.modules:
        del sys.modules[_k]

src = open("/sdcard/factory/gui/gui.py").read()

glb = {"__name__": "gui_mod"}
try:
    exec(compile(src, "/sdcard/factory/gui/gui.py", "exec"), glb)
except SystemExit:
    pass
print("[I] gui module loaded")

Launcher = glb["Launcher"]
launcher = Launcher()

for fn in ("_ensure_cache_dir", "_init_cache_dir"):
    f = getattr(launcher, fn, None)
    if f is not None:
        try:
            f()
            print("[I]", fn, "ok")
        except BaseException as e:
            print("[I]", fn, "failed:", e)

scr, content, home_btn = launcher._build_shell("IMU", show_clock=True)
print("[I] shell: scr=%s content=%s" % (scr is not None, content is not None))

ctx = glb["AppContext"]()

try:
    glb["TouchScreen"]()
    print("[I] TouchScreen ready")
except BaseException as e:
    print("[I] TouchScreen failed:", e)
ctx.touch = glb.get("TOUCH_INPUT")
ctx.app_name = "IMU"
lv.scr_load(scr)
lv.task_handler()
print("[I] ctx.touch:", ctx.touch is not None,
      "| ctx w/h:", ctx.width, ctx.height)

import apps.app_imu as imu_mod
app = imu_mod.create_app(content, ctx)
print("[I] app class:", type(app).__name__, "page:", app._page)

t0 = time.ticks_ms()
err = 0
while time.ticks_diff(time.ticks_ms(), t0) < 3000:
    try:
        launcher.tick() if hasattr(launcher, "tick") else None
        app.step()
        lv.task_handler()
    except BaseException as e:
        err += 1
        if err <= 3:
            print("[I] step err:", e)
    time.sleep_ms(5)

print("[I] err=%d samples=%d roll=%+.2f pitch=%+.2f yaw=%+.2f pages=%s"
      % (err, app._samples, app._roll, app._pitch, app._yaw,
         [p is not None for p in app._pg]))
print("[I] dash text: %r %r %r"
      % (app._d_text[0].get_text(), app._d_text[1].get_text(),
         app._d_text[2].get_text()))

def snap(tag):
    dsc = lv.snapshot_take(lv.scr_act(), lv.COLOR_FORMAT.ARGB8888)
    w, h = dsc.header.w, dsc.header.h
    ptr = int.from_bytes(bytes(memoryview(dsc.data)), "little")
    raw = uctypes.bytearray_at(ptr, w * h * 4)
    dsc.buf_free()
    out = bytearray()
    for y in range(h):
        x = 0
        while x < w:
            off = (y * w + x) * 4
            b, g, r = raw[off], raw[off + 1], raw[off + 2]
            n = 1
            while x + n < w and n < 255:
                o = off + n * 4
                if raw[o] != b or raw[o + 1] != g or raw[o + 2] != r:
                    break
                n += 1
            out.append(n - 1)
            out.append(b)
            out.append(g)
            out.append(r)
            x += n
    print("SHOT %s %d %d %d %d" % (tag, w, h, len(raw), len(out)))
    b64 = ubinascii.b2a_base64(bytes(out)).decode().strip()
    for i in range(0, len(b64), 800):
        print("B64 " + b64[i:i + 800])
    print("SHOTEND %s" % tag)

snap("real_dash")

def goto(page, tag):
    """A few step() calls are needed after switching pages to fill the readout."""
    app._set_page(page)
    for _ in range(6):
        app.step()
        lv.task_handler()
        time.sleep_ms(21)
    lv.task_handler()
    snap(tag)

goto(1, "real_raw")
goto(2, "real_att")
print("[I] att readout: %r"
      % [app._att_readout[i].get_text() for i in range(3)])
print("[I] raw readout: %r"
      % [app._readout[i].get_text() for i in range(7)])
print("PROBE DONE")
