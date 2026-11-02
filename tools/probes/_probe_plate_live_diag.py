# Reports what the detector actually sees on the current camera frame, before any
# of the app's filters run.
#
# The reported symptom: a plate that is clearly readable by eye is not recognised,
# and moving closer fixes it. That can be three different faults and they need to
# be told apart before changing a number:
#   A  nothing is detected            -> the confidence floor is too high
#   B  detected but rejected by the   -> MIN_PLATE_H / aspect band too tight
#      geometry gate
#   C  detected, accepted, but the    -> the crop is too small for the recogniser
#      string is wrong
#
# So this prints, for one frame: every raw box with its size/aspect, whether the
# app's gate would keep it, and the decoded string with and without the gate.
# It also saves the frame so the same image can be re-run after a change.
#
# No lv objects. The sensor is released in a finally block.

import gc
import sys
import time

import ulab.numpy as np
import nncase_runtime as nn
import aidemo
from libs.AI2D import Ai2d
from media.sensor import Sensor, CAM_CHN_ID_1, CAM_CHN_ID_2

sys.path.insert(0, "/sdcard/factory/gui")
from camera_fov import set_preview

PREVIEW_W = 800
PREVIEW_H = 480
DET_MODEL = "/sdcard/examples/kmodel/yolo_license_plate_det.kmodel"
REC_MODEL = "/sdcard/examples/kmodel/licence_reco.kmodel"
DET_IN = [640, 640]
REC_IN = [220, 32]
DET_MAX = 10
Y_OFF = 160
MIN_ASPECT = 2.0
MAX_ASPECT = 4.6
MIN_PLATE_H = 34

DICT = ["挂", "使", "领", "澳", "港", "皖", "沪", "津", "渝", "冀",
        "晋", "蒙", "辽", "吉", "黑", "苏", "浙", "京", "闽", "赣",
        "鲁", "豫", "鄂", "湘", "粤", "桂", "琼", "川", "贵", "云",
        "藏", "陕", "甘", "青", "宁", "新", "警", "学",
        "0", "1", "2", "3", "4", "5", "6", "7", "8", "9",
        "A", "B", "C", "D", "E", "F", "G", "H", "J", "K",
        "L", "M", "N", "P", "Q", "R", "S", "T", "U", "V",
        "W", "X", "Y", "Z", "_", "-"]
DICT_SIZE = len(DICT)


def correct_quad(q):
    points = []
    for k in range(4):
        x = float(q[k * 2])
        y = float(q[k * 2 + 1]) - Y_OFF
        if x < 0.0:
            x = 0.0
        elif x > PREVIEW_W - 1:
            x = PREVIEW_W - 1.0
        if y < 0.0:
            y = 0.0
        elif y > PREVIEW_H - 1:
            y = PREVIEW_H - 1.0
        points.append(x)
        points.append(y)
    return np.array(points)


def geom(quad):
    xs = [quad[0], quad[2], quad[4], quad[6]]
    ys = [quad[1], quad[3], quad[5], quad[7]]
    w = max(xs) - min(xs)
    h = max(ys) - min(ys)
    return w, h


def passes_gate(quad):
    w, h = geom(quad)
    if h < MIN_PLATE_H or w < 1.0:
        return False
    aspect = w / h
    return MIN_ASPECT <= aspect <= MAX_ASPECT


class Rec:
    def __init__(self):
        self.kpu = nn.kpu()
        self.kpu.load_kmodel(REC_MODEL)
        self.a2d = None
        self.shape = None

    def decode(self, crop):
        h = len(crop[0][0])
        w = len(crop[0][0][0])
        if self.shape != (h, w):
            if self.a2d is not None:
                try:
                    self.a2d.deinit()
                except BaseException:
                    pass
            a2d = Ai2d()
            a2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                               np.uint8, np.uint8)
            a2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
            a2d.build([1, 1, h, w], [1, 3, REC_IN[1], REC_IN[0]])
            self.a2d = a2d
            self.shape = (h, w)
        tin = self.a2d.run(crop)
        self.kpu.set_input_tensor(0, tin)
        self.kpu.run()
        ot = self.kpu.get_output_tensor(0)
        try:
            out = ot.to_numpy()
        finally:
            ot.release()
        flat = out.reshape((-1, DICT_SIZE))
        n = len(flat)
        text = ""
        prev = -1
        conf_sum = 0.0
        for i in range(n):
            row = flat[i]
            b1 = -1e9
            i1 = -1
            for c in range(DICT_SIZE):
                v = float(row[c])
                if v > b1:
                    b1 = v
                    i1 = c
            if i1 > 0 and i1 != prev:
                text += DICT[i1 - 1] if i1 - 1 < DICT_SIZE else "?"
                conf_sum += b1
            prev = i1
        del flat
        return text, (conf_sum / len(text) if text else 0.0)

    def release(self):
        if self.a2d is not None:
            try:
                self.a2d.deinit()
            except BaseException:
                pass
            self.a2d = None
        if self.kpu is not None:
            try:
                self.kpu.release()
            except BaseException:
                pass
            self.kpu = None
        self.shape = None


def run_at_conf(det_kpu, ai2d_det, rec, chw, conf, nms):
    """Detect at one confidence floor and report raw boxes plus decode results."""
    grid = None
    try:
        tin = ai2d_det.run(chw)
        det_kpu.set_input_tensor(0, tin)
        det_kpu.run()
        ot = det_kpu.get_output_tensor(0)
        try:
            raw = ot.to_numpy()
        finally:
            ot.release()
        grid = raw[0].transpose().copy()
        del raw

        res = aidemo.yolo_license_plate_det_postprocess(
            grid, [PREVIEW_H, PREVIEW_W], [DET_IN[1], DET_IN[0]],
            [PREVIEW_H, PREVIEW_W], conf, nms, DET_MAX)
        src = res[0]
        # res[2] holds one detection score per box (measured: 0.74-0.89 for a real
        # plate), so a box can be ranked by confidence instead of by size alone.
        scores = res[2]
        n = len(src)
        print("  conf={:.2f} raw boxes={}".format(conf, n))
        kept = []
        for i in range(n):
            quad = correct_quad(src[i])
            w, h = geom(quad)
            aspect = w / h if h else 0.0
            ok = passes_gate(quad)
            try:
                score = float(scores[i])
            except BaseException:
                score = -1.0
            print("    box[{}] pos=({},{}) {}x{} aspect={:.2f} score={:.3f} gate={}".format(
                i, int(quad[0]), int(quad[1]), int(w), int(h), aspect, score,
                "KEEP" if ok else "DROP"))
            if ok:
                kept.append(quad)

        for i in range(n):
            quad = correct_quad(src[i])
            try:
                crops = aidemo.ocr_rec_preprocess(
                    chw, [PREVIEW_H, PREVIEW_W], [quad])
                imgs = crops[0]
                if len(imgs) == 0:
                    print("    rec[{}] no crop".format(i))
                    continue
                crop = imgs[0]
                text, avg = rec.decode(crop)
                w, h = geom(quad)
                print("    rec[{}] crop={}x{} -> {!r} conf={:.2f}{}".format(
                    i, len(crop[0][0][0]), len(crop[0][0]), text, avg,
                    "" if passes_gate(quad) else "  (gate would drop this)"))
            except BaseException as error:
                print("    rec[{}] FAILED: {}".format(i, error))
        del res
        return n, len(kept)
    finally:
        del grid


sensor = None
det_kpu = None
ai2d_det = None
rec = None
try:
    sensor = Sensor(fps=30)
    sensor.reset()
    set_preview(sensor, CAM_CHN_ID_1, PREVIEW_W, PREVIEW_H)
    sensor.set_pixformat(Sensor.RGBP888, chn=CAM_CHN_ID_1)
    set_preview(sensor, CAM_CHN_ID_2, PREVIEW_W, PREVIEW_H)
    sensor.set_pixformat(Sensor.RGB888, chn=CAM_CHN_ID_2)
    sensor.run()
    time.sleep_ms(600)

    print("hold the plate in the position where it fails, 3 s ...")
    time.sleep_ms(3000)

    # A few frames: the detector is not perfectly stable, so one frame can miss
    # while the next finds it.
    #
    # RGBP888's to_numpy_ref() is already (3, H, W); converting from HWC fails with
    # "operands could not be broadcast together" because the shape already has the
    # planes first (measured).
    frames = []
    for _ in range(3):
        img = sensor.snapshot(chn=CAM_CHN_ID_1)
        ref = img.to_numpy_ref()
        chw = np.zeros((3, PREVIEW_H, PREVIEW_W), dtype=np.uint8)
        chw[0] = ref[0]
        chw[1] = ref[1]
        chw[2] = ref[2]
        frames.append(chw)
        del ref, img

    # Save the frame so the same image can be re-analysed after a change without
    # asking the user to hold still again. save() rejects the RGB888 format, so the
    # JPEG encoder is used instead (measured).
    try:
        save = sensor.snapshot(chn=CAM_CHN_ID_2)
        data = save.to_jpeg()
        handle = open("/data/plate/live_fail.jpg", "wb")
        handle.write(data)
        handle.close()
        del handle, data, save
        print("saved /data/plate/live_fail.jpg")
    except BaseException as error:
        print("save failed:", error)

    det_kpu = nn.kpu()
    det_kpu.load_kmodel(DET_MODEL)
    rec = Rec()

    mw, mh = DET_IN
    scale = min(float(mw) / PREVIEW_W, float(mh) / PREVIEW_H)
    pad_top = int((mh - int(PREVIEW_H * scale)) / 2)
    a2d = Ai2d()
    a2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                       np.uint8, np.uint8)
    a2d.pad([0, 0, 0, 0, pad_top, pad_top, 0, 0], 0, [128, 128, 128])
    a2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
    a2d.build([1, 3, PREVIEW_H, PREVIEW_W], [1, 3, mh, mw])
    ai2d_det = a2d

    for f in range(len(frames)):
        print("")
        print("===== frame {}".format(f))
        for conf in (0.20, 0.10, 0.05):
            run_at_conf(det_kpu, ai2d_det, rec, frames[f], conf, 0.2)

    del frames
    gc.collect()
except BaseException as error:
    print("FAILED:", error)
    sys.print_exception(error)
finally:
    if rec is not None:
        rec.release()
    if ai2d_det is not None:
        try:
            ai2d_det.deinit()
        except BaseException:
            pass
    if det_kpu is not None:
        try:
            det_kpu.release()
        except BaseException:
            pass
    if sensor is not None:
        for name in ("stop", "deinit"):
            try:
                getattr(sensor, name)()
                print("sensor.{} ok".format(name))
            except BaseException as error:
                print("sensor.{} error: {}".format(name, error))
    gc.collect()
    print("mem_free:", gc.mem_free())

print("RESULT: DONE")
