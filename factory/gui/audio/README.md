# audio/ — audio subsystem (L3)

Everything that produces or captures sound through the codec path: recording,
WAV finalising, playback, camera shutter, the tone sweep and TTS.

Click feedback is **not** here: `hardware/beep.py` drives the buzzer on GPIO43
through PWM and bypasses the codec deliberately.

## Contents

| Module | Provides |
| --- | --- |
| `device.py` | PyAudio capture/playback, WAV writing, buffer discipline |
| `shutter.py` | synthesised camera shutter effect, cached at `/data/audio/shutter.wav` |
| `tone.py` | single-frequency sine for the sweep page |
| `greet.py` | spoken greeting for the face-recognition app |
| `tts.py` | text-to-speech, voices cached under `/data/face/recognize/voice` |
| `pinyin_map.py` | pinyin syllable → one representative Han character, so an ASCII name can be spoken by the zh TTS front end |

## Rules

1. **May import:** `core/`, `hardware/`, and the runtime. Never `apps/` or
   `shell/`. Every module here imports `audio.device`; none goes upward.
2. **`stop_stream()` before `close()`, always.** The reverse order hard-hangs the
   board with no exception (`docs/performance-budget.md`).
3. **Match the write block size to `frames_per_buffer`.** A mismatch stutters;
   a silent standalone probe cannot detect it because it never sees the
   28-80 ms frame jitter of a real GUI.
4. **Every generated clip is a rebuildable cache** under `/data/audio/` or
   `/data/face/recognize/voice`. This firmware has no `os.sync()`, so a file
   written just before a reset can vanish; regenerate on miss instead of
   retrying forever.
5. **`__init__.py` re-exports nothing**, so importing `audio` never opens the
   codec. Import `audio.device` explicitly.

## Status

Populated, final layout.
