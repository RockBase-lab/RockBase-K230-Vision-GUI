# Chinese TTS: text -> 24 kHz mono WAV, three-stage KPU pipeline.
#
# Stage chain: fastspeech-1 (text -> phoneme encodings plus durations),
# fastspeech-2 (durations -> mel), hifigan (mel -> PCM). The split into three
# kmodels is the model's own contract; it is not a design choice here.
#
# Measured on board (2026-11-02):
#   model load   enc 1313-3033 ms, dec 1930 ms, hifi 1941 ms
#   synthesis    enc 41 ms, dec 208 ms, hifi 1094-2231 ms (scales with length)
#   "你好"       1.34 s total, 65 duration frames, 33280 B WAV
#   "你好，tony" 1.32 s total, 86 frames - an ASCII name is accepted, so a
#                greeting does not have to fall back to a fixed phrase
#   heap         load/release keeps gc.mem_free() flat over three cycles, so the
#                KPU memory is outside the Python heap and nothing leaks
#
# **Load cost (about 5 s for the three models) is why synthesis happens once at
# enrollment, never in the recognition loop.** The greeting is written to disk
# and playback only ever touches the WAV.
#
# Output is scaled to PEAK_FS before writing: the codec saturates near 390 mV
# and 48%FS is the 350 mV red line measured for this board, so a full-scale
# 16-bit write would clip. See the calibration block in audio/device.py.

import gc
import os

import ulab.numpy as np
import nncase_runtime as nn
import aidemo
import media.wave as wave
from libs.AIBase import AIBase

VOICE_DIR = "/data/face/recognize/voice"

ENC_MODEL = "/sdcard/examples/kmodel/zh_fastspeech_1_f32.kmodel"
DEC_MODEL = "/sdcard/examples/kmodel/zh_fastspeech_2.kmodel"
HIFI_MODEL = "/sdcard/examples/kmodel/hifigan.kmodel"
DICT_PATH = "/sdcard/examples/utils/pinyin.txt"
PHASE_PATH = "/sdcard/examples/utils/small_pinyin.txt"
MAP_PATH = "/sdcard/examples/utils/phone_map.txt"

OUT_RATE = 24000
# 48%FS = the 350 mV red line; see the amplifier calibration in device.py.
PEAK_FS = 0.48
# Decoder input is a fixed 600-frame window; longer utterances clamp per-phoneme
# durations down until they fit (that is the model's own scheme).
MAX_DEC_FRAMES = 600


def ensure_dir(path):
    """Create a nested path level by level; existing levels are ignored."""
    cur = ""
    for part in path.strip("/").split("/"):
        cur += "/" + part
        try:
            os.mkdir(cur)
        except OSError:
            pass


class _Encoder(AIBase):
    def __init__(self):
        self.ttszh = None
        AIBase.__init__(self, ENC_MODEL)
        self.ttszh = aidemo.tts_zh_create(DICT_PATH, PHASE_PATH, MAP_PATH)
        self.data = None
        self.data_len = 0
        self.dur_sum = 0

    def preprocess(self, text):
        pre = aidemo.tts_zh_preprocess(self.ttszh, text)
        self.data = pre[0]
        self.data_len = pre[1]
        return [nn.from_numpy(np.array([0.0])),
                nn.from_numpy(np.array(self.data))]

    def postprocess(self, results):
        enc0 = results[0]
        enc1 = results[1]
        durations = enc1[0][:int(self.data_len[0])]
        self.dur_sum = int(np.sum(durations))
        # Shrink the per-phoneme durations until the utterance fits the decoder
        # window. The ceiling steps down so every phone gives way, not just the
        # long ones.
        cap = 13
        while self.dur_sum > MAX_DEC_FRAMES:
            for i in range(len(durations)):
                if durations[i] > cap:
                    durations[i] = cap
            cap -= 1
            self.dur_sum = int(np.sum(durations))
        dec_in = np.zeros((1, MAX_DEC_FRAMES, 256), dtype=np.float)
        k = 0
        for i in range(len(durations)):
            for j in range(int(durations[i])):
                dec_in[0][k] = enc0[0][i]
                k += 1
        return dec_in, self.dur_sum

    def deinit(self):
        handle = getattr(self, "ttszh", None)
        if handle is not None:
            try:
                aidemo.tts_zh_destroy(handle)
            except BaseException as e:
                print("[TTS] ttszh destroy failed:", e)
            self.ttszh = None
        self.data = None
        AIBase.deinit(self)


class _Decoder(AIBase):
    def __init__(self):
        AIBase.__init__(self, DEC_MODEL)

    def preprocess(self, dec_input):
        return [nn.from_numpy(dec_input)]

    def postprocess(self, results):
        return results[0]


class _HifiGan(AIBase):
    def __init__(self):
        AIBase.__init__(self, HIFI_MODEL)
        self.sub = 0
        self.buf = None
        self.mel = []

    def generate(self, dec_out, dur_sum):
        """Run the vocoder in 100-frame slices and return all mel samples."""
        self.mel = []
        self.sub = dur_sum // 100
        if dur_sum % 100 > 0:
            self.sub += 1
        self.buf = np.zeros((1, 80, self.sub * 100), dtype=np.float)
        for i in range(dur_sum):
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
        self.buf = None
        return self.mel


class Synth:
    """The three models, loaded once and released together.

    Loading costs about 5 s, so an owner either keeps this alive for a batch of
    greetings or creates it around a single one. Every release path goes
    through close(), because a missed KPU release leaks native memory that only
    a power cycle reclaims.
    """

    def __init__(self):
        self.enc = None
        self.dec = None
        self.hifi = None
        self.enc = _Encoder()
        try:
            self.dec = _Decoder()
            self.hifi = _HifiGan()
        except BaseException:
            self.close()
            raise

    def text_to_wav(self, text, path):
        """Synthesise `text` into `path`. Returns the PCM byte count, or 0."""
        enc_in, dur_sum = self.enc.run(text)
        dec_out = self.dec.run(enc_in)
        mel = self.hifi.generate(dec_out, dur_sum)
        if dur_sum <= 0 or not mel:
            return 0
        clip = np.clip(np.array(mel[:dur_sum * 256], dtype=np.float), -1.0, 1.0)
        # PEAK_FS, not full scale: see the module header.
        pcm = np.array(clip * (32767.0 * PEAK_FS), dtype=np.int16).tobytes()
        ensure_dir(path.rsplit("/", 1)[0])
        tmp = path + ".tmp"
        wf = wave.open(tmp, "wb")
        try:
            wf.set_channels(1)
            wf.set_sampwidth(2)
            wf.set_framerate(OUT_RATE)
            wf.write_frames(pcm)
        finally:
            wf.close()
        try:
            os.remove(path)
        except BaseException:
            pass
        os.rename(tmp, path)
        return len(pcm)

    def close(self):
        for model in (self.enc, self.dec, self.hifi):
            if model is None:
                continue
            try:
                model.deinit()
            except BaseException as e:
                print("[TTS] deinit failed:", e)
        self.enc = None
        self.dec = None
        self.hifi = None
        gc.collect()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, tb):
        self.close()
        return False


def speak_text(name):
    """Greeting for an enrolled identity. Chinese prefix keeps the phrase
    pronounceable even though the name itself may be ASCII."""
    return "你好，" + name if name else "你好"
