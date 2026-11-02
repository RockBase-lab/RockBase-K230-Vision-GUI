# Performance budget

Measured costs on the board. Numbers are per frame unless stated otherwise, at
800x480 with LVGL FULL render. Use them to decide whether a design fits the
20 ms loop before writing it.

## The 20 ms budget

The GUI main loop targets 50 Hz, i.e. 20 ms per iteration. Everything an app
does in `step()` competes for that budget: sensor snapshot, pre-processing,
inference, post-processing, LVGL object updates and flush.

Observed jitter is normal: single frames of 30-40 ms occur from system-level
scheduling (LVGL, touch, network manager) and are not controllable by an app.

## Repaint costs

Because the firmware uses `DISP_RENDER_MODE.FULL`, any invalidation repaints the
entire screen, so the cost of a change is roughly the cost of a full repaint
plus the widgets that actually changed.

| Operation | Cost |
|---|---|
| Full-screen solid object fill | 3.7 ms |
| 1000 px thick line | 3.6 ms |
| Thick line rotated 30° | 12.5 ms |
| 9 labels with tiny_ttf, per repaint | **1411 ms** |
| 9 labels as baked snapshot bitmaps | ~0 ms |
| 9 plain PNG image widgets | ~0 ms |
| 800x372 ARGB8888 canvas `fill_bg` | **73 ms** |
| 20 full-width 10 px lines on that canvas | 200 ms |
| 40 px `hint_multitouch.png` bitmap | 0.8 ms |
| `lv.line` x1 (face outline, 34 points) | 12.0 ms |
| `lv.line` x5 | 17.3 ms |
| `lv.line` x8 | 20.1 ms |
| `lv.line` x10 | 25.2 ms |
| `set_points` for 100 points, no repaint | 1.83 ms |
| mesh: 23 edges | 11.9 ms |
| mesh: 46 edges | 19.3 ms |
| mesh: 92 edges | 30.6 ms |
| mesh: 123 edges (52 nodes) | 44.2 ms |
| mesh: 104 edges (39 nodes) | 38.1 ms |
| mesh: 94 edges (36 nodes) | 34.8 ms |
| mesh: 63 edges (26 nodes, app default) | 25.3 ms |
| 512-float normalise, Python loop | **11.1 ms** |
| 512-float normalise, ulab `np.dot` + divide | **0.02 ms** |
| 512-float dot product, ulab | 0.016 ms |
| 512-float dot product, Python loop | 0.46 ms |
| mesh: 92 edges + 42 vertex dots | 32.2 ms |
| 42 vertex dots alone | 5.0 ms |
| pose: 6 skeleton polylines, 3 px, rounded | 5.5 ms |
| pose: 17 joint dots (8 px) | 4.8 ms |
| pose: 6 polylines + 17 dots together | 5.9 ms |

Screen `lv.line` objects scale at roughly 1.2-1.6 ms each after the first, and
the first one is expensive because it covers the most pixels. The point count
barely matters: pushing 100 points into ten lines costs 1.83 ms while drawing
them costs about 20 ms, so a mesh must be batched into polylines instead of one
object per point. Under FULL render every visible line is redrawn whether or not
its geometry changed, so hiding a group is the only way to save its cost.

Edge width is not the lever: a 92-edge mesh costs 30.6 ms at 1 px and 31.2 ms at
2 px. Shaded length decides, so reduce the number of edges, not their thickness.
Vertex dots are nearly free once the edges are drawn (1.2 ms for 42 of them),
which makes a node-and-link mesh look worth its cost.

Two consequences that have already shaped this codebase:

1. **Every tile, icon, digit and hint is a pre-baked bitmap.** Text rendered by
   tiny_ttf at repaint time is three orders of magnitude too slow.
2. **Instrument panels are drawn with `lv.line` + `lv.obj`, never a canvas.** A
   large RGB565 canvas will OOM the board outright.

Full-frame PNG decoding from a `lv.img_dsc_t` with no header goes through the
decode cache, so repeated repaints are negligible.

## Text variant costs

Measured under FULL render with `invalidate()` + `lv.refr_now()`:

| Case | Cost per frame |
|---|---|
| Move y of 7 multi-size labels | 3184 ms |
| Change font size of the same labels | 5779 ms |
| 36 px freetype text, full keyboard repaint | 485 ms |
| `montserrat_16`, same screen | 6.3 ms |
| 28 px loaded bin font, 5-frame average | 3.6 ms |

Real-time font animation is therefore impossible, and freetype is unusable for
dynamic widgets because its glyph cache cannot be enabled from Python.

## Chart cost

`lv.chart` under FULL render, 800x480, one `refr_now` per frame:

| Points | Cost |
|---|---|
| 0 (baseline) | 0.21 ms |
| 60 | 6.8 ms |
| 120 | 10.9 ms |
| 200 | 17.2 ms |
| 300 | 24 ms |

About 2.4 ms + 0.074 ms per point. With a 1.7 ms I2C read included, the ceiling
inside 20 ms is N ≈ 230; N ≤ 150 is the safe zone. Window duration is `N / 50`
seconds, so 150 points is a 3 s window at 50 Hz.

`SHIFT` mode moves the plot by `plot_w / (N − 1)` pixels per
`set_next_value`, so pushing once per frame at the refresh rate is what makes
motion smooth.

## Inference chain

See `docs/board-ai-runtime.md` for the full breakdown. Summary at 800x480
preview with a 320 model:

| Stage | Cost |
|---|---|
| snapshot | 0.5 ms |
| ai2d | 1.0-1.5 ms |
| kpu | 4.0 ms (RetinaFace) / 28 ms (yolov8n) / 19 ms (yolo11n-pose) |
| post-process | 1.4-3.0 ms |
| kmodel load | 390-560 ms, once (pose models: 680-770 ms) |

The pose chain therefore runs at about 19 fps end to end on board (measured
inside the real launcher over 5 s: 96 steps, zero errors), with a 21 ms
inference plus a 22 ms LVGL task handler per iteration. Inference is the limit;
the skeleton geometry costs 5.9 ms and the joint dots are almost free once the
polylines are drawn.

A small single-shot detector therefore fits comfortably; a yolov8n detection
loop dominates the budget at 31 ms total and lands near 32 fps standalone,
dropping to about 10 fps inside the GUI because the repaint stacks on top.

### Plate chain (two models)

Measured on a still frame, 800x480 into the 640 plate detector:

| Stage | Cost |
|---|---|
| ai2d (640x640) | 3 ms |
| detection kpu | 58-64 ms |
| detection post-process | 0-4 ms |
| `ocr_rec_preprocess` (1 plate) | 4-11 ms |
| recognition ai2d + kpu (220x32) | 1 ms + 6-15 ms |
| both kmodel loads | 978 ms, once (901 + 77) |

About 65-80 ms per frame with one plate. The detector alone is twice the cost of
yolov8n at 320, so this app is detection-bound. Only one plate is recognised per
detection iteration (round-robin), which bounds the frame at that number instead of
scaling with the plate count.

### where the plate frame time actually goes

Instrumented inside the real loop, one plate in view, 120 iterations:

| Segment | Per iteration |
|---|---|
| detection (ai2d 3 ms + kpu ~58 ms + post 0-4 ms) | **68.1 ms** |
| LVGL `task_handler` (FULL render repaint) | **22.5 ms** |
| recognition (crop + ai2d + kpu + decode) | 6.1 ms |
| `gc.collect()` every 8 iterations | 1.9 ms |
| two snapshots, preview blit, overlay update | 2.4 ms |
| **total** | **101.3 ms** |

Detection is 67% and the full-screen repaint 22%. Nothing else is worth touching:
the snapshots are 0.5 ms each and the overlay update 0.3 ms.

### detection cadence is the only frame-rate lever

A 640x640 inference is 4x the pixels of the 320 models used elsewhere in this GUI,
and its cost does not depend on what the loop does between runs. So the lever is
how often it runs. Measured over 60 iterations each:

| Loop structure | Per iteration | FPS | Preview speedup |
|---|---|---|---|
| detect every iteration | 100.5 ms | 9.9 | 1.00x |
| **detect every 2nd iteration** | **69.1 ms** | **14.5** | **1.46x** |
| no detection at all (ceiling) | 35.6 ms | 28.1 | 2.82x |

The app ships the middle row: the preview is what the eye watches, so it gets every
frame, and the plate reading runs at about 7 per second. That is still fast enough
for the label to settle in under a second at `STREAK_N = 4`, and the overlay holds
its last box between detections.

Measured after the change, 20 s live: **287 iterations at 14.3 fps, zero errors,
144 recognition readings all identical** (`川WL8888`), label painted once and never
changed. Before the change the same scene gave 10.2 fps at 204 iterations.

A moving plate now lags one detection interval (about 140 ms) on screen. At this
frame rate the lag is not noticeable, and a static plate is unaffected.

The same rate held at 10.1-10.2 fps over 202-204 frames with a single plate at
reading distance (61x20 in frame) before the cadence change, so a small box does not
make detection cheaper: the cost is the 640x640 inference regardless of the plate.

### OCR chain (two models, one-shot scan)

The OCR app is not a preview loop: it previews at full rate, then a Scan button
runs one blocking detect+recognize pass. Measured inside the real launcher
(`_probe_ocr_app.py`, 8 boxes in frame):

| Stage | Cost |
|---|---|
| det kmodel + rec kmodel load | 1958 ms, once at open |
| CJK font load (3743 glyphs) | 4595 ms, once at open |
| app open total (incl. font + models) | 6989 ms |
| detection (ai2d 640x640 + kpu + `ocr_post_process`) | ~230 ms |
| recognition per box (rec ai2d 512x32 + kpu + CTC) | ~70 ms |
| one scan pass, 8 boxes | 993 ms |

The font dominates open, so it loads there (folded into the existing open cost)
and is module-cached, making a second open instant. The glyph count is the only
font-load lever: measured ~1.2 ms per glyph, independent of file size or range
count. The shipped subset is 3743 GB2312 level-1 glyphs; the full 6547-glyph
dict costs 8276 ms and was rejected. Scan stays under a second because the
640x640 detector runs once and recognition scales with the box count.

## QR detection

Measured on one synthetic code (33 modules, 12 px per module), which is the only
repeatable frame:

| Detection size | `find_qrcodes` |
|---|---|
| 640x480 | 21.6 ms |
| 800x480 | 23.0 ms |

Both sizes decode it, so matching the detection channel to the preview's 5:3
aspect costs 1.4 ms and removes the 10% strip on each side that only the
viewfinder could see. The app's dual-scale fallback (`mean_pool(2, 2)`) adds
about 22 ms when the first pass finds nothing, which is why the detection
throttle is 60 ms.

## Blocking calls that cannot be hidden

These run in C and hold the GIL, so no Python code — including worker threads
and timer callbacks — runs while they execute.

| Call | Blocking time |
|---|---|
| `wlan.scan()` | 1482 ms |
| `wlan.connect()` against an unreachable AP | ~9 s (C layer) |
| `stream.stop_stream()` | fixed **1095 ms** |
| kpu model load | ~1.5 s |
| `snapshot()` | 0.5 ms |
| `read()` at 320 frames | 20.0 ms (hardware rhythm) |

Two rules follow:

1. **Never open and close an audio stream per sound effect.** `stop_stream`
   costs a fixed 1095 ms regardless of how much was written or how long the
   stream lived. Open once on app entry and reuse; close once on exit.
2. **A long C call silently starves the beeper.** The buzzer worker cannot
   silence the tone during a scan, so the tone continues until the scan
   returns. The only wait that releases the GIL is `time.sleep_ms()`
   (measured: worker could silence at +35 ms during a sleep, versus +1483 ms
   after a scan). The click-sound helper therefore sleeps
   `BEEP_MS + 5` and polls synchronously before invoking the callback, and
   suppresses taps entirely while the network worker is busy.

## Audio stream discipline

- Write block size **must equal `frames_per_buffer`**. The driver assembles
  whole DMA blocks per `frames_per_buffer`, so writing 220-sample blocks into a
  2205-sample configuration blocks for 50 ms and produces audible stutter.
  `write` blocks for exactly the block duration when the sizes match (measured
  19.7-19.8 ms for 320 frames at 16 kHz).
- Short-effect measurements at 16 kHz / 2 ch / 320 frames: `_pa_enable` 10 ms,
  first `get_pa` 27 ms, `pa.open` 49 ms, writing eight blocks 70 ms plus 20 ms,
  `stop_stream` 1095 ms, `close` 0 ms.
- An open stream that receives no data is silent. During a 500 ms blocking
  snapshot there is no click or pop, so there is no need to push silence blocks
  (each would block 9 ms and halve the loop rate).
- Never skip `stop_stream()` and go straight to `close()`. Doing so hard-hangs
  the board; eight Ctrl-C and six Ctrl-D attempts at both 115200 and 921600
  produced no echo. Order is always `stop_stream()` then `close()`.
- The stream's blocking `write` is the natural pacing mechanism for playback;
  no additional time gating is needed.
- A shared `PyAudio` instance is mandatory. Creating and terminating one per
  operation repeatedly enables/disables mpp channels and exhausts the audio
  output. All release paths call `stop_stream`/`close` and never `terminate`.

## Recording pipeline

- Do not normalise while recording. Write s16 directly (right-shift by 16) with
  the peak stored in a sidecar file, then apply gain at playback.
- Gain can only be computed after the peak is known, so the finaliser writes a
  temporary raw stream, reads it back in blocks, normalises and writes the WAV,
  then deletes the temporary file.
- ulab vectorisation is 0.03 µs/sample against 2.7 µs/sample in pure Python —
  about 80×. Use module-level `np.max()`; ndarrays have no `.max()` method.
- SD block size matters: 32 KB writes reach 10.65 MB/s against 2.83 MB/s at
  4 KB; reads are 19.38 MB/s against 8.72 MB/s.
- A noise gate must work per block (32 KB = 128 ms) rather than per sample, or
  vectorisation stops paying off. The audible difference is nil.
- A 120 s recording normalises in 657 ms of pure compute and about 1.5 s wall
  clock (the naive per-sample version took 15 s).
- **Throttle progress-bar updates to 200 ms.** Each widget change dirties the
  whole screen (about 28 ms), so updating every step makes finalisation 12×
  slower.
- Voice has a peak/RMS ratio around 3.4, so it sounds quiet even with the peak
  near full scale. Playback gain cannot fix this because static gain clips;
  the fix is dynamic compression during finalisation (scan block peaks, build a
  gain track of 256 = 1.0, apply immediately on attack and release-limited).
  Measured end to end: RMS +2.0 dB, zero clipping, 510 ms for a 4 s clip.
- Compression release must be capped at the gain the current block requires
  (`min(g_prev + RELEASE, required)`); otherwise it overshoots and clips again,
  which is audible as a sandy texture.

## Frame pacing

- `LV_DEF_REFR_PERIOD` of 30 ms caps the loop at 33 fps unless an app calls
  `lv.refr_now(lv.disp_get_default())` each frame. The call returns in 0 ms when
  nothing is dirty.
- Throttle with an accumulating fixed cadence (`next_ms += 20`), not with
  "at least 20 ms since last time". The latter alternates between 10 and 30 ms
  at tick boundaries. If a deadline is already past, realign to `now + 20`
  instead of catching up immediately.
- A 50 Hz app that only touches seven labels per frame measured 6.3-9.3 ms,
  sustaining 49.7 Hz with a 20 ms maximum interval. Repainting all 70 labels
  measured 13.7-17.5 ms, sustaining 42.2 Hz with occasional 30 ms frames. This
  is why history rows refresh at 5 Hz while the live row refreshes at 50 Hz.
- Sparse `step()` calls in a probe report a fake 9-24 Hz because of the 20 ms
  cadence gate; measure real frame rates through a launcher loop.

## Drag interaction

Drag-follow while a finger is down measured 12.2 ms per frame on an instrument
dashboard and 15.3 ms on a chart page; a snap animation takes about 240 ms;
steady state is 50 Hz.

## Gesture thresholds

- Page turn requires 28 % of the page width (224 px on an 800 px screen) or a
  fast flick (≥ 0.55 px/ms with ≥ 40 px displacement).
- The touch release holdoff must exceed 40 ms (`RELEASE_HOLDOFF_MS = 45`).
- Gesture end is decided by 60 ms of silence, not by an Up event.

## History-row rendering

Scrolling shifts every row, so partial repaint optimisation does not apply;
what must be controlled is how many rows are refreshed per frame.

A naive implementation that queued all rows and drew one per frame left the
tail permanently blank below 20 Hz: nine rows needed 387 ms while the queue
was rebuilt every 200 ms. The fix is per-row dirty flags plus a **5 ms time
budget per frame** (at least one row). After the fix, 9 of 9 rows are filled at
17.4 fps with no blanks, while still holding 50.00 Hz at 50 Hz.
