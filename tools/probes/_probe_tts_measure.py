# TTS feasibility on this board: does the three-stage pipeline run, how long
# does it take, how much heap does it hold, and does an ASCII name synthesise?
# Results go to a small file; audio is generated but never played.
import gc
import os
import sys
import time

sys.path.insert(0, "/sdcard/factory/gui")
sys.path.insert(0, "/sdcard")

out = []


def log(msg):
    out.append(msg)
    print(msg)


ENC = "/sdcard/examples/kmodel/zh_fastspeech_1_f32.kmodel"
DEC = "/sdcard/examples/kmodel/zh_fastspeech_2.kmodel"
HIFI = "/sdcard/examples/kmodel/hifigan.kmodel"
DICT = "/sdcard/examples/utils/pinyin.txt"
PHASE = "/sdcard/examples/utils/small_pinyin.txt"
MAP = "/sdcard/examples/utils/phone_map.txt"
OUT_DIR = "/data/face/recognize/voice"

try:
    os.mkdir("/data/face")
except BaseException:
    pass
try:
    os.mkdir(OUT_DIR)
except BaseException:
    pass

import nncase_runtime as nn
import ulab.numpy as np
import aidemo
import media.wave as wave
from libs.AIBase import AIBase

log("T0 heap_free={} gc={}".format(gc.mem_free(), gc.mem_alloc()))


class Enc(AIBase):
    def __init__(self):
        self.ttszh = None
        AIBase.__init__(self, ENC)
        self.ttszh = aidemo.tts_zh_create(DICT, PHASE, MAP)
        self.data = None
        self.data_len = 0
        self.dur = 0

    def preprocess(self, text):
        pre = aidemo.tts_zh_preprocess(self.ttszh, text)
        self.data = pre[0]
        self.data_len = pre[1]
        return [nn.from_numpy(np.array([0.0])),
                nn.from_numpy(np.array(self.data))]

    def postprocess(self, results):
        o0, o1 = results[0], results[1]
        d = o1[0][:int(self.data_len[0])]
        self.dur = int(np.sum(d))
        mx = 13
        while self.dur > 600:
            for i in range(len(d)):
                if d[i] > mx:
                    d[i] = mx
            mx -= 1
            self.dur = int(np.sum(d))
        dec_in = np.zeros((1, 600, 256), dtype=np.float)
        k = 0
        for i in range(len(d)):
            for j in range(int(d[i])):
                dec_in[0][k] = o0[0][i]
                k += 1
        return dec_in, self.dur


class Dec(AIBase):
    def __init__(self):
        AIBase.__init__(self, DEC)

    def preprocess(self, dec_input):
        return [nn.from_numpy(dec_input)]

    def postprocess(self, results):
        return results[0]


class Hifi(AIBase):
    def __init__(self):
        AIBase.__init__(self, HIFI)
        self.sub = 0
        self.buf = None
        self.mel = []

    def generate(self, dec_out, dur):
        self.mel = []
        self.sub = dur // 100
        if dur % 100 > 0:
            self.sub += 1
        self.buf = np.zeros((1, 80, self.sub * 100), dtype=np.float)
        for i in range(dur):
            self.buf[:, :, i] = dec_out[:, :, i]
        for i in range(self.sub):
            src = np.zeros((1, 80, 100), dtype=np.float)
            for j in range(80):
                for k in range(i * 100, (i + 1) * 100):
                    src[0][j][k - i * 100] = self.buf[0][j][k]
            t = nn.from_numpy(src)
            try:
                res = self.inference([t])
                self.mel += res[0][0][0].tolist()
            finally:
                t.release()
        return self.mel


def synth(enc, dec, hifi, text, path):
    t0 = time.ticks_ms()
    dec_in, dur = enc.run(text)
    t1 = time.ticks_ms()
    dec_out = dec.run(dec_in)
    t2 = time.ticks_ms()
    mel = hifi.generate(dec_out, dur)
    t3 = time.ticks_ms()
    clip = np.clip(np.array(mel[:dur * 256], dtype=np.float), -1.0, 1.0)
    pcm = np.array(clip * 32767.0, dtype=np.int16).tobytes()
    wf = wave.open(path, 'wb')
    try:
        wf.set_channels(1)
        wf.set_sampwidth(2)
        wf.set_framerate(24000)
        wf.write_frames(pcm)
    finally:
        wf.close()
    t4 = time.ticks_ms()
    log("  enc={}ms dec={}ms hifi={}ms wav={}ms dur_frames={} bytes={}".format(
        time.ticks_diff(t1, t0), time.ticks_diff(t2, t1),
        time.ticks_diff(t3, t2), time.ticks_diff(t4, t3), dur, len(pcm)))
    return len(pcm)


enc = dec = hifi = None
try:
    t0 = time.ticks_ms()
    enc = Enc()
    log("T1 enc loaded {}ms heap_free={}".format(
        time.ticks_diff(time.ticks_ms(), t0), gc.mem_free()))
    t0 = time.ticks_ms()
    dec = Dec()
    log("T2 dec loaded {}ms heap_free={}".format(
        time.ticks_diff(time.ticks_ms(), t0), gc.mem_free()))
    t0 = time.ticks_ms()
    hifi = Hifi()
    log("T3 hifi loaded {}ms heap_free={}".format(
        time.ticks_diff(time.ticks_ms(), t0), gc.mem_free()))

    log("T4 synth chinese '你好'")
    try:
        synth(enc, dec, hifi, "你好", OUT_DIR + "/zh_hai.wav")
    except BaseException as e:
        log("  chinese failed: {}".format(e))

    log("T5 synth ascii name '你好，tony'")
    try:
        synth(enc, dec, hifi, "你好，tony", OUT_DIR + "/zh_tony.wav")
    except BaseException as e:
        log("  ascii failed: {}".format(e))

    log("T6 synth fixed phrase '你好，欢迎'")
    try:
        synth(enc, dec, hifi, "你好，欢迎", OUT_DIR + "/zh_welcome.wav")
    except BaseException as e:
        log("  fixed failed: {}".format(e))
except BaseException as e:
    log("LOAD FAILED: {}".format(e))
    sys.print_exception(e)
finally:
    for m, name in ((enc, "enc"), (dec, "dec"), (hifi, "hifi")):
        if m is None:
            continue
        try:
            m.deinit()
        except BaseException as e:
            log("  {} deinit failed: {}".format(name, e))
    try:
        if enc is not None and enc.ttszh is not None:
            aidemo.tts_zh_destroy(enc.ttszh)
            enc.ttszh = None
    except BaseException as e:
        log("  ttszh destroy failed: {}".format(e))
    gc.collect()
    log("T7 released heap_free={}".format(gc.mem_free()))

try:
    log("T8 files: {}".format(sorted(os.listdir(OUT_DIR))))
except BaseException as e:
    log("T8 list failed: {}".format(e))

out.append("PROBE_DONE")
f = open("/sdcard/_ttsmeas.txt", "w")
f.write("\n".join(out))
f.close()
print("WROTE")
