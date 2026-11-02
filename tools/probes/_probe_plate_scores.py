# Inspects the plate post-process result structure for a usable confidence.
#
# The geometry gate cannot separate a distant real plate (measured 61x20) from
# clutter boxes (measured 72x17, 83x21), because both sit inside any aspect band a
# real plate fits in. If the post-process returns a per-box score, ranking on it is
# the correct discriminator; if it does not, the app has to rely on the format and
# voting checks instead.
#
# Runs on SD-card frames, so no sensor is claimed.

import gc

import image
import ulab.numpy as np
import nncase_runtime as nn
import aidemo
from libs.AI2D import Ai2d

PREVIEW_W = 800
PREVIEW_H = 480
DET_MODEL = "/sdcard/examples/kmodel/yolo_license_plate_det.kmodel"
DET_IN = [640, 640]
Y_OFF = 160
FRAMES = ["/data/plate/live_fail.jpg", "/data/plate/real.jpg",
          "/data/plate/small.jpg", "/data/plate/var_base.jpg"]


def load_chw(path):
    img = image.Image(path).to_rgb888()
    w = img.width()
    h = img.height()
    ref = img.to_numpy_ref()
    chw = np.zeros((3, h, w), dtype=np.uint8)
    chw[0] = ref[:, :, 0]
    chw[1] = ref[:, :, 1]
    chw[2] = ref[:, :, 2]
    del ref, img
    return chw, w, h


def shape_of(a):
    try:
        return str(a.shape)
    except BaseException:
        pass
    parts = []
    cur = a
    for _ in range(4):
        try:
            parts.append(len(cur))
        except BaseException:
            break
        if not parts[-1]:
            break
        try:
            cur = cur[0]
        except BaseException:
            break
    return "lenwalk={}".format(parts)


kpu = None
a2d = None
try:
    kpu = nn.kpu()
    kpu.load_kmodel(DET_MODEL)
    mw, mh = DET_IN
    scale = min(float(mw) / PREVIEW_W, float(mh) / PREVIEW_H)
    pad_top = int((mh - int(PREVIEW_H * scale)) / 2)
    a2d = Ai2d()
    a2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                       np.uint8, np.uint8)
    a2d.pad([0, 0, 0, 0, pad_top, pad_top, 0, 0], 0, [128, 128, 128])
    a2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
    a2d.build([1, 3, PREVIEW_H, PREVIEW_W], [1, 3, mh, mw])

    for path in FRAMES:
        print("")
        print("===== {}".format(path))
        try:
            chw, w, h = load_chw(path)
        except BaseException as error:
            print("  load failed:", error)
            continue

        tin = a2d.run(chw)
        kpu.set_input_tensor(0, tin)
        kpu.run()
        ot = kpu.get_output_tensor(0)
        try:
            raw = ot.to_numpy()
        finally:
            ot.release()
        grid = raw[0].transpose().copy()
        del raw

        for conf in (0.20, 0.10, 0.05):
            res = aidemo.yolo_license_plate_det_postprocess(
                grid, [PREVIEW_H, PREVIEW_W], [DET_IN[1], DET_IN[0]],
                [PREVIEW_H, PREVIEW_W], conf, 0.2, 10)
            quads = res[0]
            print("  conf={:.2f} boxes={}".format(conf, len(quads)))
            for i in range(len(quads)):
                q = quads[i]
                xs = [float(q[0]), float(q[2]), float(q[4]), float(q[6])]
                ys = [float(q[1]), float(q[3]), float(q[5]), float(q[7])]
                bw = max(xs) - min(xs)
                bh = max(ys) - min(ys)
                # res[1] and res[2] are per-box side data; print the values so their
                # meaning is measured rather than assumed.
                extra1 = ""
                extra2 = ""
                try:
                    row = res[1][i]
                    extra1 = str([round(float(row[k]), 2) for k in range(len(row))])
                except BaseException as error:
                    extra1 = "n/a ({})".format(error)
                try:
                    extra2 = "{:.4f}".format(float(res[2][i]))
                except BaseException as error:
                    extra2 = "n/a ({})".format(error)
                print("    box[{}] {}x{} aspect={:.2f} pos=({},{})".format(
                    i, int(bw), int(bh), bw / bh if bh else 0,
                    int(min(xs)), int(min(ys))))
                print("      res[1][{}]={}  res[2][{}]={}".format(
                    i, extra1, i, extra2))
            del res
        del grid, chw
        gc.collect()
except BaseException as error:
    print("FAILED:", error)
finally:
    if a2d is not None:
        try:
            a2d.deinit()
        except BaseException:
            pass
    if kpu is not None:
        try:
            kpu.release()
        except BaseException:
            pass
    gc.collect()
    print("mem_free:", gc.mem_free())

print("RESULT: DONE")
