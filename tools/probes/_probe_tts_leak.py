# Where did the 977 KB go? Repeat load/release cycles and watch heap_free to
# separate a real leak from one-time native arena growth.
import gc
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
sys.path.insert(0, "/sdcard")

out = []


def log(m):
    out.append(m)
    print(m)


ENC = "/sdcard/examples/kmodel/zh_fastspeech_1_f32.kmodel"
DEC = "/sdcard/examples/kmodel/zh_fastspeech_2.kmodel"
HIFI = "/sdcard/examples/kmodel/hifigan.kmodel"

import nncase_runtime as nn
import ulab.numpy as np
import aidemo

# Minimal load/release without AIBase, to isolate the arena from Python objects.
log("heap_free start={}".format(gc.mem_free()))

for cycle in range(3):
    kpus = []
    for path, name in ((ENC, "enc"), (DEC, "dec"), (HIFI, "hifi")):
        t0 = time.ticks_ms()
        k = nn.kpu()
        k.load_kmodel(path)
        kpus.append((k, name))
        log("cycle{} {} load={}ms free={}".format(
            cycle, name, time.ticks_diff(time.ticks_ms(), t0), gc.mem_free()))
    for k, name in kpus:
        try:
            k.release()
        except BaseException as e:
            log("cycle{} {} release failed: {}".format(cycle, name, e))
    del kpus
    gc.collect()
    log("cycle{} AFTER RELEASE free={}".format(cycle, gc.mem_free()))

# The ttszh C frontend holds the dictionaries; check its own cost.
log("--- ttszh frontend")
h = None
try:
    t0 = time.ticks_ms()
    h = aidemo.tts_zh_create("/sdcard/examples/utils/pinyin.txt",
                             "/sdcard/examples/utils/small_pinyin.txt",
                             "/sdcard/examples/utils/phone_map.txt")
    log("ttszh create={}ms free={}".format(
        time.ticks_diff(time.ticks_ms(), t0), gc.mem_free()))
    pre = aidemo.tts_zh_preprocess(h, "你好")
    log("preprocess ok data_len={} free={}".format(pre[1], gc.mem_free()))
finally:
    if h is not None:
        try:
            aidemo.tts_zh_destroy(h)
            h = None
        except BaseException as e:
            log("ttszh destroy failed: {}".format(e))
    gc.collect()
    log("after ttszh destroy free={}".format(gc.mem_free()))

out.append("PROBE_DONE")
f = open("/sdcard/_ttsleak.txt", "w")
f.write("\n".join(out))
f.close()
print("WROTE")
