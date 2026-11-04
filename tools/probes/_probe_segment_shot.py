# Headless check of Segment mask alignment: open through the launcher, run a
# few inference steps, then dump (a) the app's own ARGB mask buffer and (b) the
# AI-frame numpy. Both cover the same crop window, so they align 1:1 after a
# 0.4 scale and the PC composite (tmp/_comp_seg.py) shows mask vs picture.
# lv.snapshot_take is NOT used: with the 1.5 MB mask buffer resident it raises
# MemoryError, and after the buffer is freed it wedges the board (measured).
# Run with --lvgl.

import gc
import sys

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("core", "apps.app_segment", "apps", "core.errors", "ui.beep", "launcher",
              "hardware.camera_fov", "core.model_pool"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv

src = open("/sdcard/factory/gui/launcher.py").read()
G = {"__name__": "gui_probe"}
exec(compile(src, "launcher.py", "exec"), G)
G["_ensure_cache_dir"]()

meta = {m["id"]: m for m in G["FOLDER_MEMBERS"]["vision"]}["segment"]
launcher = G["Launcher"]()
launcher.open_app(meta)
app = launcher.active_app
if app is None:
    print("RESULT FAIL app is None")
    raise SystemExit(1)

for i in range(15):
    try:
        app.step()
    except BaseException as e:
        print("E step:", e)
        break
    lv.task_handler()
print("STEPS done objs={}".format(app.last_objs))

import ubinascii


def rle(out, raw, w, h, stride, channels):
    """Row RLE: (run-1, B, G, R[, A]) tuples, run 1..255."""
    def emit(pb, pg, pr, pa, runlen):
        out.append(runlen - 1)
        out.append(pb)
        out.append(pg)
        out.append(pr)
        if channels == 4:
            out.append(pa)

    for y in range(h):
        base = y * stride
        pb = pg = pr = pa = -1
        run = 0
        for x in range(w):
            o = base + x * channels
            b = raw[o]
            g = raw[o + 1]
            r = raw[o + 2]
            a = raw[o + 3] if channels == 4 else 0
            if b == pb and g == pg and r == pr and (channels == 3 or a == pa):
                run += 1
                if run == 255:
                    out.append(254)
                    out.append(pb)
                    out.append(pg)
                    out.append(pr)
                    if channels == 4:
                        out.append(pa)
                    run = 0
            else:
                if run:
                    emit(pb, pg, pr, pa, run)
                pb, pg, pr, pa = b, g, r, a
                run = 1
        if run:
            emit(pb, pg, pr, pa, run)


# (a) the mask buffer as it stands: ARGB, alpha is the tint strength.
mout = bytearray()
rle(mout, app._mask_buf, 800, 480, 800 * 4, 4)
print("SHOT4 mask 800 480 {} {}".format(len(app._mask_buf), len(mout)))
b64 = ubinascii.b2a_base64(bytes(mout)).decode().strip()
for i in range(0, len(b64), 800):
    print("B64 " + b64[i:i + 800])
print("SHOTEND mask")
del b64, mout
app.mask_widget.add_flag(lv.obj.FLAG.HIDDEN)
try:
    app.mask_widget.set_src(None)
except Exception:
    pass
app.masks = None
app._mask_buf = None
app._mask_dsc = None
gc.collect()

# (b) a fresh chn2 preview frame as JPEG. chn1 and chn2 share the same crop
# window and dimensions, so the preview aligns 1:1 with the mask. JPEG avoids
# the ~1 MB allocation that RGB RLE needs on natural camera frames (measured).
try:
    preview = app.sensor.snapshot(chn=2)
except BaseException as e:
    print("PREVIEW_SNAP_FAIL", repr(e))
    raise SystemExit(1)
jpg = preview.to_jpeg()
data = bytes(jpg)
print("JPEG preview 800 480 {}".format(len(data)))
b64 = ubinascii.b2a_base64(data).decode().strip()
for i in range(0, len(b64), 800):
    print("J64 " + b64[i:i + 800])
print("JPEGEND preview")

del src, G, meta, launcher, app, preview, jpg, data
gc.collect()
print("DONE mem_free={}".format(gc.mem_free()))
