# Headless: call aidemo.segment_postprocess with a synthetic frame and print
# the exact types/shapes at each step, then try both [raw] and raw as the first
# argument. "list is too short" came from one of them; this pins which argument
# layout the C layer accepts. No sensor, no display -- pure nn + aidemo.

import sys
import ulab.numpy as np
import nncase_runtime as nn
import aidemo

kpu = nn.kpu()
kpu.load_kmodel("/sdcard/examples/kmodel/yolov8n_seg_320.kmodel")

inp = nn.from_numpy(np.zeros((1, 3, 320, 320), dtype=np.uint8))
kpu.set_input_tensor(0, inp)
kpu.run()
print("outputs_size:", kpu.outputs_size())
outs = []
for i in range(kpu.outputs_size()):
    ot = kpu.get_output_tensor(i)
    try:
        arr = ot.to_numpy()
        print("to_numpy type:", type(arr))
        outs.append(arr)
    finally:
        ot.release()

raw = outs[0]
print("raw type:", type(raw))
try:
    print("raw shape:", raw.shape)
except BaseException as e:
    print("raw .shape fail:", e)
    try:
        print("raw[0] type:", type(raw[0]), "shape:", raw[0].shape)
    except BaseException as e2:
        print("raw[0] fail:", e2)

masks = np.zeros((1, 384, 640, 4), dtype=np.uint8)

for label, arg in (("[raw]", [raw]), ("raw", raw)):
    try:
        res = aidemo.segment_postprocess(
            arg, [320, 320], [320, 320], [384, 640], 0.3, 0.45, 0.5, masks)
        print("TRY {}: OK res_type={} len={}".format(
            label, type(res), len(res) if res is not None else None))
        if res:
            print("   res[0] len={} res[1] len={} res[2] len={}".format(
                len(res[0]), len(res[1]), len(res[2])))
    except BaseException as e:
        print("TRY {}: FAIL {}".format(label, e))
print("DONE")
