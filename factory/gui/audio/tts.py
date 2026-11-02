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
# The codec saturates near 390 mV and 48%FS is the 350 mV red line measured for
# this board (see the amplifier calibration in device.py).
PEAK_FS = 0.48
# Decoder input is a fixed 600-frame window; longer utterances clamp per-phoneme
# durations down until they fit (that is the model's own scheme).
MAX_DEC_FRAMES = 600

# Loudness. Normalising the peak alone is not enough: raw TTS measures 5.7-10.0
# %FS RMS at a 48%FS peak (crest factor 4.8-8.4) and sounds 8-12 dB quieter than
# the recorded audio path, which sits at 24.3%FS RMS with crest 1.98.
#
# The recording path compresses blocks down to COMP_TARGET_FRAC (0.5) of the
# global peak and then scales the result back to the red line, so mid-level
# blocks gain 1/frac while the loudest stay at 48%FS. 0.5 lifts them only 2x,
# which is enough for a source whose own crest is about 3 but not for TTS: its
# plosives and pauses put the crest at 5.8-8.4 and most blocks never reach the
# threshold. A lower threshold is therefore used for TTS only, leaving the
# recording calibration untouched.
TTS_COMP_FRAC = 0.30
def _peak_of(arr):
    """Largest magnitude. ulab has no abs(), so this is max and min."""
    hi = float(np.max(arr))
    lo = float(np.min(arr))
    return hi if hi > -lo else -lo


def _compress(x, block, frac, release, floor):
    """Compress peak blocks down to `frac` of the global peak, in place.

    x is expected to peak at 1.0. Gain falls instantly and rises rate-limited,
    interpolated across each block, so there is no boundary click. This is the
    recording finaliser's scheme; see the compression note in device.py.
    """
    n = len(x)
    target = frac
    rel = release / 256.0
    low = floor / 256.0
    ramp = np.array([k / float(block) for k in range(block)], dtype=np.float)
    g_prev = 1.0
    i = 0
    while i < n:
        end = i + block
        if end > n:
            end = n
        seg = x[i:end]
        bp = _peak_of(seg)
        if bp > target and bp > 0.0:
            need = target / bp
            if need < low:
                need = low
        else:
            need = 1.0
        g = need
        if g > g_prev:
            g = g_prev + rel
            if g > 1.0:
                g = 1.0
        if end - i == block:
            x[i:end] = seg * (g_prev + (g - g_prev) * ramp)
        else:
            x[i:end] = seg * g
        g_prev = g
        i = end
    return x


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
        peak = _peak_of(clip)
        if peak <= 1e-6:
            return 0
        # Work with unity peak so the compression threshold is unambiguous,
        # compress like the recording path, then land the result on the red line.
        x = clip / peak
        from audio import device
        try:
            use_comp = device.COMP_ENABLED
        except BaseException:
            use_comp = False
        if use_comp:
            x = _compress(x, device.COMP_BLOCK, TTS_COMP_FRAC,
                          device.COMP_RELEASE, device.COMP_MIN_GAIN)
        peak2 = _peak_of(x)
        gain = (PEAK_FS / peak2) if peak2 > 1e-6 else 0.0
        pcm = np.array(x * (32767.0 * gain), dtype=np.int16).tobytes()
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
    """Greeting for an enrolled identity.

    The zh frontend reads Han characters only and emits silence for Latin text,
    so an ASCII name has to be converted to characters that carry the same
    syllables. Measured on board: `你好，nina` left the name segment at 0%FS
    while `你好，妮娜` and a converted pinyin name both voiced every segment.
    """
    if not name:
        return "你好"
    spoken = name_to_hanzi(name)
    return "你好，" + spoken if spoken else "你好"


def _is_han(ch):
    return 0x4E00 <= ord(ch) <= 0x9FFF


def _segment(name):
    """Split an ASCII name into pinyin syllables, or None.

    Chooses the split with the fewest uncommon syllables, then the fewest
    syllables: `nina` is nin+a (囜+锕) if the rare syllable wins, but ni+na
    (你+那) costs less and is the pronunciation the user meant. The table is
    small and names are short, so a plain best-path scan is enough.
    """
    from audio import pinyin_map
    table = pinyin_map.PINYIN_CHAR
    common = pinyin_map.COMMON
    text = "".join(ch for ch in name.lower() if "a" <= ch <= "z")
    n = len(text)
    if not n:
        return None
    far = (1 << 30, 1 << 30)
    best = [far] * (n + 1)
    back = [None] * (n + 1)
    best[0] = (0, 0)
    for i in range(n):
        if best[i] is far:
            continue
        for ln in range(1, min(6, n - i) + 1):
            cand = text[i:i + ln]
            if cand not in table:
                continue
            cost = (best[i][0] + (0 if cand in common else 1),
                    best[i][1] + 1)
            if cost < best[i + ln]:
                best[i + ln] = cost
                back[i + ln] = (i, cand)
    if back[n] is None:
        return None
    out = []
    k = n
    while k > 0:
        i, cand = back[k]
        out.append(table[cand])
        k = i
    out.reverse()
    return out or None


def name_to_hanzi(name):
    """Characters whose pronunciation matches `name`, or None.

    Han characters pass through unchanged. A pinyin name is segmented into
    syllables; when that fails (an English name such as `tony`), each letter is
    spoken with the Chinese name of that letter so the greeting still says
    something instead of going silent.
    """
    if any(_is_han(ch) for ch in name):
        return name
    syls = _segment(name)
    if syls is not None:
        return "".join(syls)
    out = []
    for ch in name.lower():
        sound = LETTER_SOUND.get(ch)
        if sound:
            out.append(sound)
    return "".join(out) or None


# Chinese names of the Latin letters, used when a name is not pinyin at all.
LETTER_SOUND = {
    "a": "诶", "b": "比", "c": "西", "d": "迪", "e": "伊",
    "f": "艾弗", "g": "吉", "h": "艾尺", "i": "艾", "j": "杰",
    "k": "开", "l": "艾勒", "m": "艾姆", "n": "恩", "o": "欧",
    "p": "屁", "q": "扣", "r": "阿尔", "s": "艾斯", "t": "提",
    "u": "优", "v": "维", "w": "达布留", "x": "艾克斯", "y": "歪",
    "z": "贼德",
}
