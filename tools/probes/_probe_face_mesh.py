# Measures whether a triangulated face-mesh look fits the frame budget.
#
# Draws the mesh on REAL face geometry: one frame is captured, the 106 landmark
# points are computed as in the app, then the mesh is drawn several ways so the
# cost of edges and vertex dots can be separated.
#
# The synthetic-cost probe measured long crossing lines and is therefore a worst
# case. Mesh edges are short and confined to the face, so the real number matters.
#
# Run with --lvgl and a face in front of the camera.

import gc
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("apps.app_face_points", "apps.app_face_det", "apps",
              "apps.error", "ui.beep"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv
import ulab.numpy as np
import nncase_runtime as nn
import aidemo
from libs.AI2D import Ai2d
from media.sensor import Sensor, CAM_CHN_ID_1

DET_MODEL = "/sdcard/examples/kmodel/face_detection_320.kmodel"
LM_MODEL = "/sdcard/examples/kmodel/face_landmark.kmodel"
ANCHOR = "/sdcard/examples/utils/prior_data_320.bin"
LM_IN = 192
Y_OFF = 160


def chain(ids, closed=False):
    out = []
    for i in range(len(ids) - 1):
        out.append((ids[i], ids[i + 1]))
    if closed:
        out.append((ids[-1], ids[0]))
    return out


# Polylines of the anatomy, then cross links that turn them into triangles.
CHAINS = [
    ([1, 9, 11, 13, 15, 2, 4, 6, 8, 0, 24, 22, 20, 18, 32, 30, 28, 26, 17], True),
    ([43, 45, 47, 50, 51, 48], False),
    ([97, 99, 101, 105, 104, 102], False),
    ([35, 33, 37, 39, 42, 41], True),
    ([89, 87, 91, 93, 96, 95], True),
    ([72, 74], False),
    ([77, 79, 80, 85, 84], False),
    ([52, 55, 56, 59, 58, 61, 68, 67, 63], True),
    ([65, 60, 57, 69, 62], True),
]
LINKS = [
    (43, 9), (48, 2), (97, 16), (102, 8),
    (45, 35), (47, 39), (99, 89), (101, 93),
    (72, 46), (74, 100), (72, 51), (74, 104),
    (35, 77), (41, 52), (89, 85), (95, 68),
    (77, 52), (85, 55), (79, 55), (80, 56),
    (68, 1), (67, 24), (86, 74), (84, 80),
    (39, 77), (93, 85), (57, 86), (62, 86),
    (0, 17), (0, 32), (17, 52), (32, 68),
]

EDGES_FULL = []
for ids, closed in CHAINS:
    EDGES_FULL += chain(ids, closed)
EDGES_FULL += LINKS


def unique_vertices(edges):
    seen = {}
    for a, b in edges:
        seen[a] = True
        seen[b] = True
    return len(seen)


print("E1 polylines={} edges={} vertices={}".format(
    len(CHAINS), len(EDGES_FULL), unique_vertices(EDGES_FULL)))

# ---------------------------------------------------------------- capture face
anchors = np.frombuffer(open(ANCHOR, "rb").read(), dtype=np.float).reshape(
    (4200, 4))

scr = lv.scr_act()
bg = lv.obj(scr)
bg.set_size(800, 480)
bg.align(lv.ALIGN.TOP_LEFT, 0, 0)
bg.set_style_bg_color(lv.color_hex(0x101418), 0)
bg.set_style_bg_opa(lv.OPA.COVER, 0)
bg.set_style_border_width(0, 0)
bg.set_style_pad_all(0, 0)
bg.clear_flag(lv.obj.FLAG.SCROLLABLE)

MAX_EDGES = len(EDGES_FULL) + 4
MAX_DOTS = 48
lines = []
for _ in range(MAX_EDGES):
    ln = lv.line(bg)
    ln.set_points([{"x": 0, "y": 0}] * 2, 2)
    ln.set_style_line_width(1, 0)
    ln.set_style_line_color(lv.color_hex(0xFFFFFF), 0)
    ln.set_style_line_opa(lv.OPA.COVER, 0)
    try:
        ln.set_style_line_rounded(False, 0)
    except BaseException:
        pass
    ln.add_flag(lv.obj.FLAG.HIDDEN)
    lines.append(ln)
dots = []
for _ in range(MAX_DOTS):
    d = lv.obj(bg)
    d.set_size(4, 4)
    d.set_style_radius(3, 0)
    d.set_style_bg_color(lv.color_hex(0xFFFFFF), 0)
    d.set_style_bg_opa(lv.OPA.COVER, 0)
    d.set_style_border_width(0, 0)
    d.set_style_pad_all(0, 0)
    d.clear_flag(lv.obj.FLAG.SCROLLABLE)
    d.add_flag(lv.obj.FLAG.HIDDEN)
    dots.append(d)

sensor = None
kpu_det = None
kpu_lm = None
a2d_det = None
a2d_lm = None
pts_x = None
pts_y = None
try:
    sensor = Sensor(fps=30)
    sensor.reset()
    sensor.set_framesize(w=800, h=480, chn=CAM_CHN_ID_1, crop=True)
    sensor.set_pixformat(Sensor.RGBP888, chn=CAM_CHN_ID_1)
    sensor.run()

    kpu_det = nn.kpu()
    kpu_det.load_kmodel(DET_MODEL)
    kpu_lm = nn.kpu()
    kpu_lm.load_kmodel(LM_MODEL)

    a2d_det = Ai2d()
    a2d_det.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                           np.uint8, np.uint8)
    a2d_det.pad([0, 0, 0, 0, 64, 64, 0, 0], 0, [104, 117, 123])
    a2d_det.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
    a2d_det.build([1, 3, 480, 800], [1, 3, 320, 320])

    a2d_lm = Ai2d()
    a2d_lm.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                          np.uint8, np.uint8)

    print("E2 hold still, a face in front of the camera, 5 s ...")
    for s in range(5, 0, -1):
        print("   ", s)
        time.sleep_ms(1000)

    img = sensor.snapshot(chn=CAM_CHN_ID_1)

    tin = a2d_det.run(img.to_numpy_ref())
    kpu_det.set_input_tensor(0, tin)
    kpu_det.run()
    outs = []
    for i in range(kpu_det.outputs_size()):
        ot = kpu_det.get_output_tensor(i)
        try:
            outs.append(ot.to_numpy())
        finally:
            ot.release()
    det = aidemo.face_det_post_process(0.5, 0.2, 320, anchors, [800, 480 + Y_OFF],
                                       outs)
    del outs, tin

    if len(det) == 0 or not det[0]:
        print("E3 no face detected: falling back to synthetic face geometry")
        # A 220x270 face centred on screen: the edge lengths and the shaded area
        # match a real detection at normal viewing distance, which is what the
        # draw cost depends on.
        import math
        pts_syn_x = [0.0] * 106
        pts_syn_y = [0.0] * 106
        for k in range(106):
            ring = k % 3
            ang = (k * 2.399963) % (2 * math.pi)
            rx = 110.0 - ring * 22.0
            ry = 135.0 - ring * 26.0
            pts_syn_x[k] = 400.0 + rx * math.cos(ang)
            pts_syn_y[k] = 240.0 + ry * math.sin(ang)
        pts_x = pts_syn_x
        pts_y = pts_syn_y
        print("E4 synthetic face spans x=[{:.0f},{:.0f}] y=[{:.0f},{:.0f}]".format(
            min(pts_x), max(pts_x), min(pts_y), max(pts_y)))
    else:
        boxes = det[0]
        best = 0
        for i in range(1, len(boxes)):
            if boxes[i][2] * boxes[i][3] > boxes[best][2] * boxes[best][3]:
                best = i
        b = boxes[best]
        bx, by = int(b[0]), int(b[1]) - Y_OFF
        bw, bh = int(b[2]), int(b[3])
        print("E3 largest box ({},{},{}x{})".format(bx, by, bw, bh))

        scale = float(LM_IN) / (max(bw, bh) * 1.5)
        half = LM_IN / 2.0
        m = np.zeros((2, 3), dtype=np.float)
        m[0, 0] = scale
        m[0, 2] = half - (bx + bw / 2.0) * scale
        m[1, 1] = scale
        m[1, 2] = half - (by + bh / 2.0) * scale

        a2d_lm.affine(nn.interp_method.cv2_bilinear, 0, 0, 127, 1,
                      [m[0, 0], m[0, 1], m[0, 2], m[1, 0], m[1, 1], m[1, 2]])
        a2d_lm.build([1, 3, 480, 800], [1, 3, LM_IN, LM_IN])
        tin2 = a2d_lm.run(img.to_numpy_ref())
        kpu_lm.set_input_tensor(0, tin2)
        kpu_lm.run()
        pred = None
        for i in range(kpu_lm.outputs_size()):
            ot = kpu_lm.get_output_tensor(i)
            try:
                if pred is None:
                    pred = ot.to_numpy().flatten()
            finally:
                ot.release()
        del tin2

        inv = aidemo.invert_affine_transform(m).flatten()
        pts_x = [0.0] * 106
        pts_y = [0.0] * 106
        for k in range(106):
            ox = (pred[k * 2] + 1.0) * half
            oy = (pred[k * 2 + 1] + 1.0) * half
            pts_x[k] = ox * inv[0] + oy * inv[1] + inv[2]
            pts_y[k] = ox * inv[3] + oy * inv[4] + inv[5]
        del pred
        print("E4 face spans x=[{:.0f},{:.0f}] y=[{:.0f},{:.0f}]".format(
            min(pts_x), max(pts_x), min(pts_y), max(pts_y)))
finally:
    if sensor is not None:
        for method in ("stop", "deinit"):
            try:
                getattr(sensor, method)()
            except BaseException:
                pass
    for obj, method in ((a2d_lm, "deinit"), (a2d_det, "deinit"),
                        (kpu_lm, "release"), (kpu_det, "release")):
        if obj is not None:
            try:
                getattr(obj, method)()
            except BaseException:
                pass
    gc.collect()
    print("E5 resources released mem_free={}".format(gc.mem_free()))


def paint(edges, width, ndots):
    for i in range(len(lines)):
        lines[i].add_flag(lv.obj.FLAG.HIDDEN)
    for i in range(ndots, MAX_DOTS):
        dots[i].add_flag(lv.obj.FLAG.HIDDEN)
    if pts_x is None:
        return
    for i in range(ndots):
        dots[i].set_pos(int(pts_x[i]) - 2, int(pts_y[i]) - 2)
        dots[i].clear_flag(lv.obj.FLAG.HIDDEN)
    for i, (a, b) in enumerate(edges):
        if i >= len(lines):
            break
        ln = lines[i]
        ln.set_style_line_width(width, 0)
        ln.set_points([{"x": int(pts_x[a]), "y": int(pts_y[a])},
                       {"x": int(pts_x[b]), "y": int(pts_y[b])}], 2)
        ln.clear_flag(lv.obj.FLAG.HIDDEN)


def bench(tag, fn, n=10):
    fn()
    lv.refr_now(lv.disp_get_default())
    t0 = time.ticks_us()
    for _ in range(n):
        fn()
        lv.refr_now(lv.disp_get_default())
    ms = time.ticks_diff(time.ticks_us(), t0) / 1000.0 / n
    print("E6 {:<38s} {:6.2f} ms  {:5.1f} fps".format(
        tag, ms, 1000.0 / ms if ms else 0))
    return ms


if pts_x is not None:
    half_edges = EDGES_FULL[:len(EDGES_FULL) // 2]
    q_edges = EDGES_FULL[:len(EDGES_FULL) // 4]
    bench("baseline (nothing visible)", lambda: paint([], 1, 0))
    bench("edges {} w1".format(len(q_edges)), lambda: paint(q_edges, 1, 0))
    bench("edges {} w1".format(len(half_edges)), lambda: paint(half_edges, 1, 0))
    bench("edges {} w1 (full mesh)".format(len(EDGES_FULL)),
          lambda: paint(EDGES_FULL, 1, 0))
    bench("edges {} w2 (full mesh)".format(len(EDGES_FULL)),
          lambda: paint(EDGES_FULL, 2, 0))
    bench("edges {} w1 + 40 dots".format(len(EDGES_FULL)),
          lambda: paint(EDGES_FULL, 1, 40))
    bench("edges {} w2 + 40 dots".format(len(EDGES_FULL)),
          lambda: paint(EDGES_FULL, 2, 40))
    bench("dots only (40)", lambda: paint([], 1, 40))
    bench("edges {} w2 (no dots)".format(len(half_edges)),
          lambda: paint(half_edges, 2, 0))

for obj in lines + dots:
    try:
        obj.delete()
    except BaseException:
        pass
bg.delete()
gc.collect()
print("RESULT: DONE")
