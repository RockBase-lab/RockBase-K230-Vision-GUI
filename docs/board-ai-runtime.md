# On-board AI runtime

How a model runs on the K230 from the board-side MicroPython environment: the
`libs` wrappers, the KPU call sequence, and the post-processing contracts that
the GUI apps rely on.

Everything in this file was measured on the board. The `libs` package resolves
from `/sdcard/libs`; the import search path is `''`, `.frozen`, `/sdcard`,
`/data`, `/data/lib`. There is no `/sdcard/examples/libs`.

## Ai2d (geometry pre-processing)

Use the wrapper class, not the native module. `nn.ai2d()` exposes a different,
lower-level method set (`set_dtype`, `set_pad_param`, `set_resize_param`, ...).

```python
from libs.AI2D import Ai2d
import nncase_runtime as nn
import ulab.numpy as np

ai2d = Ai2d()                                   # no arguments
ai2d.set_ai2d_dtype(nn.ai2d_format.NCHW_FMT, nn.ai2d_format.NCHW_FMT,
                    np.uint8, np.uint8)
ai2d.pad([0, 0, 0, 0, top, bottom, left, right], 0, [128, 128, 128])
ai2d.resize(nn.interp_method.tf_bilinear, nn.interp_mode.half_pixel)
ai2d.build([1, 3, h_in, w_in], [1, 3, 320, 320])
out = ai2d.run(img_np)                          # single argument
```

`run()` returns the wrapper's own reusable runtime tensor. Do not release it.

### padding happens in output space

`pad` is applied after the resize, in the **output** coordinate space. Feeding
800x480 with `pad(64, 64)` and resizing to 320x320 puts the content in output
rows `64..255` (64 top pad + 192 content + 64 bottom pad).

So the letterbox pad amount is computed from the output size minus the scaled
content size: `(320 - 480 * 0.4) / 2 = 64`, not 160.

To verify this yourself, feed a synthetic image that is 50 in the top half and
200 in the bottom half, and check which output row the step lands on.

## KPU

```python
kpu = nn.kpu()
kpu.load_kmodel("/sdcard/examples/kmodel/xxx.kmodel")
kpu.set_input_tensor(0, ai2d_out_tensor)
kpu.run()                       # no arguments
ot = kpu.get_output_tensor(0)
arr = ot.to_numpy()
ot.release()                    # mandatory
```

- `load_kmodel` takes a path string. There is no `nn.from_file`.
- A runtime tensor exposes only `release` and `to_numpy`. There is no
  `to_numpy_copy`.
- **Release every output tensor immediately after converting.** Failing to do
  so leaks native memory, which crashes the board and drops the USB serial
  link (`ClearCommError` / `PermissionError 22` / `OSError 995`). Recovery
  needs a physical reset.
- **`get_output_tensor()` is only valid after `run()`.** Calling it on a loaded
  but not yet executed model blocks inside the native layer with no exception
  and no serial output; the link then dies with `ClearCommError`. To inspect a
  model's output layout without a camera, run it once on a synthetic frame.
- Tear down with `kpu.release()`, not `deinit`.

## Sensor capture

```python
s = Sensor(fps=30)
s.reset()
s.set_framesize(w=640, h=360, chn=CAM_CHN_ID_2, crop=True)
s.set_pixformat(Sensor.RGB888, chn=CAM_CHN_ID_2)
MediaManager.init()
s.run()
img = s.snapshot(chn=CAM_CHN_ID_2)
ref = img.to_numpy_ref()
```

- `MediaManager.init()` must precede `s.run()`.
- `Sensor` snapshots directly; there is no `get_stream`.
- `to_numpy_ref()` shape follows the pixel format: `RGB888` gives `(H, W, 3)`,
  `RGBP888` gives `(3, H, W)`.
- Teardown: `s.stop()` then `MediaManager.deinit()`.

### the AI channel format must match the ai2d declaration

The AI channel must be `Sensor.RGBP888` (planar, CHW) whenever ai2d declares
`NCHW_FMT`. Feeding `RGB888` (packed HWC) makes ai2d read HWC memory as NCHW,
so the KPU receives scrambled data.

The symptom is deceptive: scores sit near 0.006 (blank-paper level) while the
same image scores 0.95 on a PC, and **the per-channel statistics still look
normal** (mean/min/max match the source) because only the byte order changed.
Dumping the ai2d output as a thumbnail shows rainbow noise, with per-channel
MSE of 4000-8800 against the source.

To isolate the fault, save `np.ascontiguousarray(img.transpose(2, 0, 1)[None])`
on the PC, push it to the board as `.npy`, and feed it to the KPU through
`nn.from_numpy()`. A correct score there proves the model and the
post-processing are fine and the ai2d call is wrong.

The display channel still needs `RGB888` (packed) because `as_lvgl_img_src`
only accepts packed data. Detection apps therefore run **two camera
channels**: `chn` with `RGBP888` for AI, another with `RGB888` for the LVGL
preview.

Never declare the ai2d input as `RGB_packed` while passing `[1, 3, H, W]`: the
native layer waits for a matching size and **deadlocks** with no Python
exception and no REPL response. Only a physical reset recovers it.

## YOLO post-processing contracts

| Family | Output shape | Signature | Notes |
|---|---|---|---|
| yolov8 | `(1, 4+ncls, anchors)` | `yolov8_det_postprocess(data, rgb, model, disp, ncls, conf, nms, max)` | 8 args; pass `raw[0].transpose().copy()` |
| yolo26 | `(1, 300, 6)` end-to-end | `yolo26_det_postprocess(data, rgb, model, disp, ncls, conf, max)` | **7 args, no nms**; pass `raw[0].copy()`, never transpose |

- yolo26 columns are `[x1, y1, x2, y2, score, class]` in pixel space (col 2
  reaches roughly the image width). The score is column 5.
- yolov5 needs three scale heads plus anchors
  (`aicube.anchorbasedet_post_process`) and does not fit either signature.

Measured on the same frame, at both 640 and 800 preview width: yolov8n_320
person 0.852 / tv 0.882; yolov8n_224 person 0.804; fruit_v8_320 0.901; yolo26n
person 0.791 / tv 0.341. All four agree on box position (person around
270, 201, 289, 268), which shows pre-processing and coordinate compensation
are consistent.

### yolov8 scores are raw, not probabilities

`yolov8n_320.kmodel` emits raw score values in roughly `-0.006 .. 0.017`,
including negatives. Pass `to_numpy()` straight to `aidemo` and threshold on
the raw value (0.3-0.35). **Never apply sigmoid before the post-process**: a
blank-paper noise logit around 0.02 becomes about 0.5 after sigmoid, which
exceeds any 0-1 threshold and produces boxes all over the screen. Only the
final pooled boxes (at most `BOX_POOL = 12`) get sigmoid applied, purely for
displaying a readable score.

### the post-process does not compensate letterbox padding

Passing `rgb=[h, w]`, `model=[w, h]`, `disp=[h, w]`, the routine scales both
axes by the same `disp_w / model_w` and **does not subtract the top pad from
y**. So x/w/h come out right while y is always too large by
`pad_top * disp_w / model_w`.

Measured with an 800x480 frame into a 320 model (scale 0.4, `pad_top` 64): the
same anchor is correctly at y=319 but `aidemo` reports 478, a difference of
159 ≈ 160 = 64 × 2.5. Subtracting 160 restores agreement (318).

The upstream example never exposed this because it always uses a square AI
frame (224x224 into a 224 model, pad = 0). Either recompute from
`x / scale` and `(y - pad_top) / scale`, or subtract the offset and verify it
on the board.

### y offset is always (preview_w - preview_h) / 2

`y_off = pad_top * W / M` and `pad_top = (M - H * M / W) / 2` when width is the
constraining axis, which reduces to `W / 2 - H / 2`. For an 800x480 preview it
is always 160, independent of a 224 or 320 model input (both measured at 160).
Still compute it from the formula rather than hard-coding, so a future preview
size stays correct.

## Licence plate detection and recognition

Two models from the firmware image, chained: a plate detector and a CTC string
recogniser. Both names come from the vendor and keep their spelling.

| Model | Input | Output | Cost (800x480 frame) |
| --- | --- | --- | --- |
| `yolo_license_plate_det.kmodel` | 640x640 | `(1, 13, 8400)` | 68 ms kpu + post |
| `licence_reco.kmodel` | 220x32 | `(55, 1, 74)` | 6-15 ms kpu |

Load cost is 901 ms for the detector and 77 ms for the recogniser. A whole
iteration with one plate measures about 68 ms of detection plus 22 ms of LVGL
repaint plus 6 ms of recognition, so the detector sets the rate: 9.9 fps when it
runs every iteration, 14.5 fps when it runs every second one. The app uses the
latter and keeps the preview on every iteration; the breakdown is in
`docs/performance-budget.md`.

```python
grid = raw[0].transpose().copy()          # (8400, 13); aidemo needs contiguous
res = aidemo.yolo_license_plate_det_postprocess(
    grid, [480, 800], [640, 640], [480, 800], 0.2, 0.2, 10)
# res[0] = one 8-float array per plate: x0,y0, x1,y1, x2,y2, x3,y3
```

### the plate post-process ignores display_size

Five different fourth arguments (`[480,800]`, `[800,480]`, `[640,640]`,
`[800,640]`, `[480,640]`) returned byte-identical quads for the same frame. Only
changing the second argument as well moved the numbers, and that change breaks x.
So the display-size argument is not usable for compensation: treat the output as
model-space and correct it in Python.

### plate y carries the letterbox pad, x does not

Measured on a frame whose plate really spans `(28,122)-(778,370)`: the quad came
back as `x 47..636, y 273..540`. Subtracting `pad_top * 800 / 640 = 160` from y
gives `113..380`, which matches; x needs no scaling. This is the same pad leak the
COCO detection family has, and the same `y_off` constant applies.

### do not widen the box: use the detected quad as-is

The detector under-reads the horizontal extent, and the shortfall grows with the
plate's size in frame:

| Plate width in frame | True rect | Detected rect | Aspect | Raw-quad crop |
| --- | --- | --- | --- | --- |
| 374 px | 214,180-588,304 | 219,175-595,312 | 2.74 | decodes correctly |
| 750 px | 28,122-778,370 | 47,113-636,380 | 2.20 | drops the last character |

It is tempting to fix the wide case by forcing the box to a plate's real 3.14:1
aspect. **Measured, that is worse.** Forcing the aspect moves the left edge into
background, and the province character is the first thing that breaks:

| Strategy | Frames correct (7 test frames) |
| --- | --- |
| quad as detected, no widening | **6 / 7** |
| axis-aligned rect widened to 3.14:1, 1.08 margin | 2 / 7 |
| widen right edge only, 1.00 / 1.06 / 1.12 | 1 / 7, 0 / 7, 0 / 7 |

Failure mode of every widening variant: `渝` decodes as `豫`, `川` or `新` while
every digit stays correct. The left edge is the part that matters, and widening
moves it.

Two further reasons to pass the corners through untouched:

- The detected corners are a **rotated quad**, and `ocr_rec_preprocess` rectifies
  it. Collapsing to an axis-aligned rect throws that away and, on a plate with a
  few degrees of tilt, squeezes the characters.
- Only the extreme case (a plate filling 95% of the frame width) suffers from the
  under-read; at the width a camera usually sees, the raw quad already covers the
  whole plate.

So the app subtracts `y_off` and clamps, and does nothing else to the geometry.
The crop size is what limits a distant plate, and that is handled by accepting the
detection score instead of a size floor (below).

### the province character needs voting, not a better crop

With a plate a little far away or slightly defocused, the recogniser returns a
wrong province character while every digit is right. Measured over 7 frames:

| Frame | Raw-quad reading |
| --- | --- |
| clean, small, tiny, blurred, left-clipped, shifted | `渝A999U9` |
| plate filling 95% of the frame width | `赣A999U` (last character missing) |

Because the error is a whole-character substitution, the confidence does not
separate it: the output is **logits, not probabilities** (top1 values measured at
-0.02 to -1.02, so a 0-1 threshold is meaningless), and a wrong province
character measured a *higher* margin than a correct one on some frames.

Voting on the decoded string works where a threshold does not. It does not fix a
reading that is stably wrong, though: a 61x20 plate measured `川WL8888` on 94% of
frames and that string was **correct** (verified against the captured frame by eye).
A consistently wrong reading is a model limitation that only a larger crop fixes,
which is what moving closer does. Voting fixes the frames that disagree with the
majority.

The app settles the label with a streak of identical readings plus hysteresis, and
smooths the detector box so the crop stops moving; the measured effect of each step
is in the stability section below.

### use the detection score, not the box size

`yolo_license_plate_det_postprocess` returns three lists: quads, rectangles, and
**one detection score per box** (index 2). The score is the reliable
discriminator, and it is high even for a distant plate:

| Plate in frame | Detected box | Score |
| --- | --- | --- |
| Near, filling most of the width | 588x267 | 0.736 |
| Half width | 376x137 | 0.885 |
| Reading distance (the failing case) | 61x20 | **0.885** |

A height floor was tried before the score and had to be withdrawn. `MIN_PLATE_H = 34`
rejected the 61x20 plate above — the box was correct, the score was 0.885, and the
string decoded — so the app showed nothing while the plate was plainly readable by
eye. Moving closer made the box exceed the floor, which is exactly the reported
symptom. Worse, the floor never rejected the clutter it was added for: that
measured 144x48 and 151x61, i.e. **taller** than the floor.

The gate is now: score >= 0.35, aspect within 2.0-4.6 : 1, height >= 12 px (only
degenerate boxes). Clutter is handled by the score and by the decoded-string format
check below, and candidates are ranked by score so a real plate wins a slot over
background.

### a decoded string must look like a plate

The province slot is a closed set of 38 characters, so a reading whose first
character is not one of them cannot be a plate. Measured noise such as `HA188E` is
dropped this way. Length is accepted between 6 and 8 characters, which covers the
7-character standard and the 8-character new-energy format.

This does not reject everything: clutter can decode as a plausible plate such as
`京AL8888`. Those are handled in the time domain instead.

### stability comes from time, in three steps

Measured on one 61x20 plate over 20 s at 10 fps, applying one measure at a time:

| Measure | Distinct readings | Label changes | Dominant reading |
| --- | --- | --- | --- |
| none (per-frame result shown) | 14 | 9 | 56% |
| streak of 4 identical readings | 4 | 5 | 77% |
| + hysteresis (8 to replace a shown label) | 12 | 5 | — |
| + box smoothing (EMA 0.35) | **2** | **1** | **94%** |

The decisive step is **box smoothing**, and the reason is worth recording: the
detector's box jitters by a pixel or two between frames, and the recogniser is
sensitive enough that the same plate cropped from a 1 px shifted box measured a
different second character. Averaging the box per slot (`new = 0.65*old +
0.35*detected`) converges a static plate onto one crop, after which the reading is
reproducible.

The hysteresis row shows its own cost: once a label is on screen, a variant has to
hold for 8 frames, so in that run a settled label took longer to be replaced and
more wrong readings accumulated before the change. It is kept because a caption that
alternates between two close readings is worse than one that is briefly stale, and
with box smoothing in place a variant rarely reaches the switch threshold at all.

A moving plate lags about three frames from the smoothing, which is invisible at
this frame rate.

### ocr_rec_preprocess contract

```python
crops = aidemo.ocr_rec_preprocess(frame_chw, [480, 800], [quad])
imgs, boxes = crops[0], crops[1]
```

- `frame_chw` must be `(3, H, W)`. A `(H, W, 3)` frame is read as one plane per
  pixel: a `(200,120,40)` pixel came back as grey 135, which is that colour's
  luminance, so the crop silently lands on the wrong content.
- The quad argument must be a **list of float ndarrays**. A Python list of lists
  raises `expected ndarray`; a bare ndarray raises `expected list`; int16 and
  uint16 raise `invalid ndarray dtype or layout`.
- Each crop comes back `(1, 1, H, W)`: single channel, so build the recognition
  ai2d with one input channel. A three-channel build is accepted but only one
  plane of data exists.
- Time: 4-11 ms per plate.

### the RGBP888 plane order matches RGB888

The crop is greyscale, so a red/blue swap between the two camera channels would
shift the luminance of a coloured plate without moving the box or raising
anything. Measured by snapshotting both channels of one frame and comparing the
same five positions: the packed `(H,W,3)` and planar `(3,H,W)` readings were
**identical** (total absolute difference 0 across RGB, versus 444 for the swapped
order, and a grey delta of 0.0). No plane reversal is needed between the sensor
and a frame prepared as RGB888 from a file.

### CTC decoding

Output `(55, 1, 74)`; index 0 is the blank. Reshape to `(-1, 74)`, argmax per
step, append a class only when it is non-zero and differs from the previous step.
The 74-entry table covers 38 province characters, the digits, most letters and
two punctuation marks.

### plate labels need a baked CJK font

Nunito and the built-in montserrat have no Chinese glyphs, so a decoded plate
renders as boxes. `tools/gen_font.py` bakes a 30px subset of the 38 dictionary
characters plus ASCII into `assets/fonts/plate_cjk_30.bin` (31 KB). Load it with
the `A:` drive prefix. The 30px size is the readable one: at 24px the string was
hard to read on the 800x480 panel from arm's length, which is where a plate label
is normally read.

## General OCR (print text, CN + EN)

The `ocr` app recognises arbitrary printed text with two int16 kpmodels, not the
plate chain. Both ship at `/sdcard/examples/kmodel/`.

| Model | Input | Output shape | Notes |
|---|---|---|---|
| `ocr_det_int16.kmodel` | 640x640 | `(1, 640, 640, 2)` NHWC | channel 0 is the text mask |
| `ocr_rec_int16.kmodel` | 512x32 | `(128, 1, 6549)` seq-first | CTC over `dict.txt` (6549) |

```python
boxes = aicube.ocr_post_process(raw[:, :, :, 0].reshape(-1), hwc.reshape(-1),
                                [640, 640], [800, 480], mask_thr, box_thr)
```

`raw[:, :, :, 0]` indexes the 4-D NHWC tensor directly to pull the mask channel;
the official example writes the same. Each returned box is
`[crop(1,h,w,3 HWC), [8 corner coords]]`, and the coords are already in the
ai2d input space (= screen when input == display).

### the rec output is sequence-first, not the plate layout

The plate CTC model emits `(steps, classes)`-style output where the class axis is
last after a reshape; this OCR model emits `(128, 1, 6549)`, so the class axis is
index 2 and the step axis is index 0. Decode with
`argmax(axis=len(shape)-1).reshape(-1)` then CTC-dedup, treating
`blank = len(dict) - 1` (the last entry), not index 0. Copying the plate
`argmax(axis=1)` mislabels every step.

### the dict already covers Chinese and English

`dict.txt` is 94 ASCII plus 6455 CJK entries, so one rec model reads both
scripts; no second model or language switch is needed.

### the display font is the open-time cost, not the scan cost

Rendering recognised text needs a baked CJK font (`ocr_cjk_16.bin`). `font_load`
costs ~1.2 ms per glyph and is not cached by the loader, so glyph count is the
only lever: the shipped 4036 glyphs (GB2312 level-1 hanzi the dictionary uses,
plus every punctuation and symbol character it can emit, plus a space) load in
4900 ms, the full 6547-glyph dict in 8276 ms. Dropping the punctuation instead
renders every comma and full stop as a box, which is what the first build did.

Load it on the **first scan**, not at open: at open it froze the screen for
4.9 s before the preview ever appeared, which the user reads as a hang. The app
open then costs 2.2 s (models only) and the preview runs at 28.4 fps from the
first frame. The font is module-cached, so only the first scan pays it; see
`docs/performance-budget.md`.

## Human pose (17 keypoints)

| Model | Output shape | kpu cost | Post-process |
|---|---|---|---|
| `yolo11n-pose.kmodel` | `(1, 56, 2100)` | 19.1 ms | `person_kp_postprocess` |
| `yolov8n-pose.kmodel` | `(1, 56, 2100)` | 22.6 ms | `person_kp_postprocess` |
| `yolo26n-pose.kmodel` | `(1, 300, 57)` | 20.9 ms | `yolo26_pose_postprocess` |

All three ship in the firmware image at `/sdcard/examples/kmodel/`. Measured at
800x480 into a 320 model, mean of five runs on the same frame.

```python
res = aidemo.person_kp_postprocess(raw[0], [H, W], [mw, mh], conf, nms)
```

Five positional arguments, no display-size parameter. **Pass `raw[0]` as is**:
transposing it makes the C layer compare an int against an ndarray and raise.
A sixth argument raises "takes 5 positional arguments".

`res` is `[boxes, kps, scores]`:

| Item | Shape | Contents |
|---|---|---|
| `boxes` | `(N, 4)` int16 | x, y, w, h |
| `kps` | `(N, 17, 3)` | x, y, score per joint |
| `scores` | `(N,)` | person score |

Keypoint scores are probabilities (measured 0.65-0.99 on a real person), unlike
the raw logits the YOLO detection family emits.

### pose y needs no letterbox correction

The detection post-process leaves the top pad in y; this one does not. Measured
with the same 800x480 into 320 frame: box y=85 h=399 with the nose at y=120,
i.e. `nose - box_top = 35 = 0.09 * h`, which is where a nose belongs. The
detection models report the same box 160 px too low. Do not copy the `y_off`
subtraction from the detection path.

### ulab arrays have no `__len__` attribute

`hasattr(arr, "__len__")` is **False** for a ulab ndarray even though `len(arr)`
works. Code that uses it to tell a row from a scalar silently treats every array
as a scalar; in the pose app that dropped all 17 keypoints. Use `try: len(obj)`.

### background detections are real, not duplicates

The model fires on printed photographs and edge objects as well as live people.
Measured on a wall of posters: 4 boxes with 11-17 confident joints each, which
no joint-count filter can separate. An app that claims a person count must
either draw all of them or report only the one it follows.

## RetinaFace face detection

Files: `/sdcard/examples/kmodel/face_detection_320.kmodel` (584576 B) and
`/sdcard/examples/utils/prior_data_320.bin` (67200 B).

```python
anchors = np.frombuffer(open(path, "rb").read(), np.float).reshape((4200, 4))
```

- `kpu.outputs_size() == 9`: three scales × (8-channel cls / 4-channel bbox /
  20-channel landmark), ordered `cls[40, 20, 10]`, `bbox[40, 20, 10]`,
  `lm[40, 20, 10]`, all float32 (dtype 102). **Convert and release all nine.**
- `aidemo.face_det_post_process(conf, nms, model_w, anchors, rgb_param, outs)`
  returns `[boxes, landmarks]`, where boxes are `(N, 4)` in `x, y, w, h` and
  landmarks are `(N, 10)` for five points.
- With no faces the result is an empty list, not `([], [])`. Guard for it.
- Boxes carry **no score**; there are only four columns.

### the fifth argument (rgb888p_size) semantics

- Scaling uses a standard letterbox, `scale = min(model_w / param_w, model_h /
  param_h)`, and output coordinates are model coordinates divided by that
  scale. Swapping w and h yields the same scale because `min` is symmetric.
- It also treats `param_w × param_h` as a **clip rectangle** and clips boxes to
  it.
- Passing `[H, W] = [480, 800]` turns a large face on the right half into
  `x = 480, w = 0` — completely invisible. That was the actual cause of
  "large faces on the right are not detected".
- Passing `[W, H] = [800, 480]` fixes x but clips faces near the bottom to
  `h = 7`, because the returned y already includes the pad and can reach 640.
- **The correct value is `[W, H + y_off]` = `[800, 640]`**: the scale stays
  0.4 and the rectangle no longer clips. This requires `H + y_off <= W`.
- `[800, 960]` changes the scale (0.4 → 0.333) and corrupts every coordinate.

The y offset is exactly derivable: `y_out = (pad_top + screen_y * scale) /
scale = pad_top / scale + screen_y`, so `y_off = pad_top / scale`, which is
`pad_top * W / M` = 160 for 800x480 into 320. x/w/h need no correction.

Pad colour is `[104, 117, 123]` (the training mean) with pad parameters
`[0, 0, 0, 0, top, top, 0, 0]`.

**The AI channel sees almost the whole sensor.** Both Face apps configure
chn1/chn2 through `camera_fov.set_preview`, which passes the explicit window
`(60, 0, 1800, 1080)`: 93.75% of the width and the full height, downscaled
2.25:1. Anything outside that 3% strip on each side is never seen, so a face
leaving it cannot be tracked although it is still in front of the lens. Both
channels must share the window; the overlay maths below depends only on the
output size, not on the window. See `board-hardware.md` and
`tools/probes/_probe_sensor_fov.py`.

A narrower window buys detection range back at the cost of field of view: the
firmware's `crop=True` keeps 1600x960 instead of 1800x1080, which makes a
subject 12.5% larger in the model input and is the right choice if far-away
faces matter more than the edges of the frame.

Measured cost (800x480 preview, 320 model): ai2d 1.5 ms + kpu 4.0 ms + post
3.0 ms = 8.5 ms; 26 fps inside the app including LVGL repaint. At conf 0.5 a
centred face is detected in 90/90 frames. A four-face scene (three small, one
large on the right) gives 60/60 detections with zero degenerate boxes after
the fix, largest box 146x186. `kpu.load` takes about 390 ms, 543 ms including
sensor init.

Threshold sensitivity, one captured frame re-run at every conf (so the frame
is identical): 18 boxes at 0.02, 11 at 0.05, 10 for every value from 0.10
through 0.85, then 8 at 0.90, 7 at 0.95, 6 at 0.97 and 4 at 0.98. Detected
scores sit mostly above 0.9, so **any conf inside 0.10-0.85 yields the same
frame**; a preset pair like 0.25/0.50/0.70 is invisible to the user because
it sits entirely inside that plateau. Move the presets to the ends (about
0.05 and 0.95) to make the control observable.

Opening an audio output stream after this chain still works, so the RetinaFace
single-model path does not reproduce the audio contamination seen with the
full Vision chain. See `reset-matrix.md`.

## Face landmark (106 points)

Files: `face_landmark.kmodel` (1409104 B, 192x192 input) plus the RetinaFace
detector above. There is **no `aidemo.face_landmark_post_process`** on this
firmware: the post-processing is plain Python.

Chain per frame:

1. Detect, take the **largest** box (screen coordinates, so subtract `y_off`).
2. Build an affine matrix that maps the box into the 192 square with a 1.5x
   margin, centred: `scale = 192 / (max(w, h) * 1.5)`, translation
   `96 - centre * scale`.
3. `ai2d.affine(interp_method.cv2_bilinear, 0, 0, 127, 1, [m00, m01, m02,
   m10, m11, m12])`, then `build([1,3,480,800], [1,3,192,192])`, then `run`.
4. `kpu.outputs_size() == 1`, shape `[1, 212]` = 106 points. Release it.
5. Normalise to pixels with `p = (p + 1) * 96` (assign, do not accumulate —
   writing `p += (p + 1) * 96` pushes every point far off screen).
6. `aidemo.invert_affine_transform(m)` then `x' = x*i0 + y*i1 + i2`,
   `y' = x*i3 + y*i4 + i5` for each point.

Measured on board (800x480 preview, largest box 108x132 at (113,119)): all 106
points inside the screen; the eye centres land within 1 px and 4 px of the
detector's own 5-point output, and the mouth centre within 3 px. That agreement
is the correctness check for steps 5 and 6.

Costs: detector 9.6 ms (ai2d + kpu + post), `affine + build` **5.78 ms**, landmark
run 1.32 ms. `affine + build` dominates, so the app rebuilds it every other frame
and reuses the previous points in between. Model load: detector 206 ms, landmark
106 ms; both models resident leaves 3.65 MB free.

The mask is **not a triangulation of the landmarks**. The 106-point model puts
points on the silhouette and on the features and none inside the cheeks, so a
Delaunay on those points leaves the cheeks empty and packs triangles onto the
eyes and mouth. An automatic lattice degrades below about 30 nodes for the same
reason: the landmark outline is unevenly spaced and the lower face has no
landmarks, so the triangles become long and skewed and the eyes vanish into
them.

`tools/gen_face_mesh.py` therefore places **26 nodes by hand** at even fractions
of the face box (forehead 3, temples 2, brows 4, eyes 4, nose 4, mouth 2,
cheeks 4, jaw 2, chin 1), triangulates them once, and binds each node to its
three nearest landmarks by inverse-distance weights:
`x = sum(w_i * lm[i_i])`. The topology is constant; only positions move.

Two details that are easy to get wrong:

- **Barycentric binding cannot be used.** Forehead, cheek, jaw and chin nodes sit
  outside every landmark triangle, where `find_simplex` fails and the weights
  extrapolate wildly. Inverse-distance weighting is stable everywhere.
- **The forehead offset must be negative.** The highest landmarks are the brows,
  so the bbox top IS the brow line; a node fraction of 0 lands on the eyebrows
  and the mesh stops there instead of reaching the hairline.

Ink is teal rather than white, because white washed out against bright scenes.
The mesh uses `0x00B894` for edges and `0x5CE8C0` for nodes; the Contour view
uses `0x00E5A0` for **all** of its polylines. Per-feature hues (amber brows,
pink lips) were hard to pick out on skin, and one colour matching the face
outline reads clearly, so the contour no longer distinguishes anatomy by colour.

Cost: 26 nodes and 63 edges put the app at about 45 ms/frame, 22 fps at 800x480
under FULL render. The detector plus the preview repaint already cost 36-40 ms,
so the overlay is only part of the frame and trimming more edges buys little.

Opening an audio output stream after this chain still works, so the RetinaFace
single-model path does not reproduce the audio contamination seen with the
full Vision chain. See `reset-matrix.md`.

## Face recognition (enrolled identities)

Model: `face_recognition_mobile.kmodel` (2780824 B, 112x112 input). The large
`face_recognition.kmodel` is 46333280 B, which cannot coexist with the GUI heap,
so only the mobile variant is usable.

Chain per face:

1. Detect and take the largest box (screen coordinates, so subtract `y_off`).
2. Build a **similarity** transform (not a plain affine) that maps the five
   landmarks onto the canonical 112x112 face:
   `[38.2946, 51.6963, 73.5318, 51.5014, 56.0252, 71.7366, 41.5493, 92.3655,
   70.7299, 92.2041]`. Use the closed-form 2x2 SVD: this firmware has no general
   linear algebra.
3. `ai2d.affine(cv2_bilinear, 0, 0, 127, 1, m6)` then
   `build([1,3,480,800], [1,3,112,112])` then `run`.
4. `kpu.outputs_size() == 1`, shape `[1, 512]`. There is **no
   `aidemo.face_recognition_post_process`**; the post-process is reading the
   tensor.
5. Normalise to unit length, compare with `cos/2 + 0.5`.

Two facts that are wrong in the obvious implementation:

- **The feature is 512-dimensional, not 128.** The reference code names
  `feature_num = 128`; using that stride compares half of one person to half of
  another and silently produces nonsense.
- **Normalise with ulab, never a Python loop.** A loop over 512 floats measured
  **11.1 ms** per call and dominated the frame; `np.dot` plus array division do
  the same in **0.02 ms**. Keep features as ulab arrays end to end (`.tobytes()`
  to save, `np.frombuffer` to load) instead of Python lists.

Measured: recognition 4.35 ms including the affine and its rebuild, detection
9.6 ms, whole app 39 ms/frame (25 fps). Alignment check: mapped eyes land at
y 53.65 / 53.11 against the canonical 51.70 / 51.50, and the nose at 71.15
against 71.74, so the transform and the `y_off` subtraction are both right.

Threshold: same-subject pairs over 12 frames measured min **0.9827**, median
0.9905, sd 0.0025 (66 pairs) **within one session**. That is the easy case. Two
other measurements set the real bound:

| Comparison | Score |
| --- | --- |
| same subject, photo-enrolled, matched after a cold restart | **0.936-0.961** |
| different enrolled real subjects | at most **0.7054** |
| synthetic unrelated vector | 0.52 |

A cold restart moves the same face's feature because the camera starts from
different auto-exposure and white-balance defaults than the enrollment session
did, so the session-to-session minimum is about 0.94, not 0.98. The app uses
**0.85**: above the 0.7054 impostor ceiling with a 0.14 margin, and below the
0.936 restart floor with a 0.09 margin. A threshold of 0.90 sat too close to
the restart floor and turned known faces into `unknown` after a reboot while the
score was still displayed. The score scale is `cos/2 + 0.5`, confirmed by the
synthetic vector.

Storage: features live in `/data/face/recognize`, one `f<NNN>.bin` of 512
float32 (2048 B) per identity plus a `db.json` index holding the display names.
The directory is on `/data` because the SD card has about 76 MB free while
`/data` has 28 GB; see `board-hardware.md`. Writes go to a `.tmp` file and are
renamed into place, so a power cut cannot leave a truncated 2048-byte feature.
`os.mkdir` builds one level only, so the app creates `/data/face` before
`/data/face/recognize`.

### Photo enrollment and the PHOTO gate

Each index entry carries `"src": "live"` or `"src": "photo"`, and the index
carries a top-level `"photos_allowed"` flag. An index written before this
feature has neither key: `src` defaults to `live` and `photos_allowed` to
`true`, so upgrading never silently narrows an existing database.

At enrollment the app runs `face_liveness_rgb.kmodel` **once** on the current
frame to choose the tag; the steady recognition loop never runs it, so the
25 fps is unchanged. A face scoring below 0.50 (a printed photo held to the
camera) is tagged `photo`. While `photos_allowed` is false such an enrollment
is refused, and while it is false the matcher skips every `photo` entry — one
list lookup per compare, so the hot loop cost is unchanged. The top-bar PHOTO
switch flips the flag and persists it to the index immediately, because the
setting is user intent that must survive a restart even with no later
enrollment.

Verified on board: with the switch on a `photo` entry matches (score 1.000 for
its own feature); with it off the same feature returns no match while a `live`
entry still matches. The `src` tag and the flag both survive a reload.

### Multi-angle samples

One identity owns up to **3** feature vectors, one per viewing angle, and the
match takes the best score over all of them. Names therefore repeat in the
index: `db_names`, `db_feats` and `db_srcs` stay flat, and duplicate names are
the entire mechanism. `_db_match` needs no extra branch, because the global
maximum over that flat list is already the maximum over every angle of every
identity. An index written before this feature loads unchanged, since each old
entry is simply an identity with one sample.

Enrollment types the name once. Each further OK press during the session saves
another angle, tagged with the same `src` as the first sample; the session ends
at 3 samples or after 20 s without a new one. Typing an existing name drops that
identity's old samples first, so re-enrolling rebuilds it from the new angles
instead of mixing stale and fresh vectors.

Cost: 3 samples per identity = 3 dot products, about 0.03 ms on a 39 ms frame.
`MAX_ENROLL` is 200 feature vectors, i.e. 66 identities at 3 samples each.

**The threshold has to be re-measured for a multi-sample database.** Three
samples raise the score for the right person, but they also give an impostor
three chances, so the 0.7054 single-sample impostor ceiling measured between
different enrolled subjects no longer bounds the impostor distribution. The
value in `ENROLL_MATCH` was measured with one sample per identity; a database
enrolled at three angles needs its own cross-angle matrix before the value can
be trusted.

### Greeting voice (TTS)

`audio/tts.py` turns `你好，<name>` into a 24 kHz mono WAV. The model is split
into three kmodels and that split is the model's own contract, not a choice:

| Stage | Model | Output |
| --- | --- | --- |
| encoder | `zh_fastspeech_1_f32.kmodel` | per-phoneme encodings + durations |
| decoder | `zh_fastspeech_2.kmodel` | mel, fixed 600-frame window |
| vocoder | `hifigan.kmodel` | PCM, in 100-frame slices |

Measured on board (2026-11-02):

| Step | Cost |
| --- | --- |
| load the three models | **5970 ms** |
| synthesise `你好，<name>` | **1340 ms**, 22016 samples, 44032 B |
| load + 8 greetings in one batch | 18949 ms (8580 for the first) |
| heap across a load/release cycle | flat, three cycles |
| ASCII name in the text | **accepted**, so no fixed-phrase fallback is needed |

**Synthesis never runs in the recognition loop.** Loading the three models
costs about 6 s, so greetings are built once, at enrollment, and playback only
reads the WAV. Identities enrolled before this feature have no WAV; the manage
overlay offers a batch rebuild, which keeps one `Synth` alive across the run and
builds one name per frame so the preview advances between names.

**The frontend speaks Han characters only.** It emits silence for Latin text, and
byte count is not evidence that a name was spoken — the first version of this
feature "verified" an ASCII name because the WAV grew, while the name segment
measured 0%FS. The check that works is the amplitude envelope:

| Text | Envelope, peak %FS per 100 ms |
| --- | --- |
| `你好` | 8, 15, 12, 10, 7, 24, 15 |
| `你好，nina` | 7, 16, 13, 11, 8, 27, 15, **2, 0, 0** |
| `你好，ni na` | identical to the line above |
| `你好，nǐ nà` (tone marks) | 21 segments, 13 of them zero |
| `你好，妮娜` | 8, 19, 18, 5, 15, 16, 3, 0, **10, 10, 11, 20, 21, 14** |

So an ASCII name has to be converted to characters carrying the same syllables.
`audio/pinyin_map.py` holds 421 syllables mapped to one Han character each; it is
generated by `tools/gen_pinyin_map.py` from the firmware's own `pinyin.txt`, and
the candidate characters are ranked by how often they appear in
`small_pinyin.txt`, with single-reading characters preferred. Both filters matter:

- A character with several readings is voiced with its usual one, so 共 would say
  "gong" where "hong" was wanted.
- Ranking by occurrence picks 流/鸿/雨 rather than the rare glyphs a codepoint
  order produces.

`name_to_hanzi` then splits the name with a shortest-path search that prefers
common syllables, so `nina` becomes ni+na (你+那) rather than nin+a (囜+锕). A
name that is not pinyin at all (`tony`, `lily`) falls back to the Chinese names
of its letters, which is verbose but keeps the greeting audible.

Measured after the fix: every greeting peaks at **48.0%FS** and every name
segment carries audio (tail segments voiced 9/9 to 18/19), against 27.5%FS and a
silent name before it.

Output level: the vocoder's own peak is about **57% of full scale**, so the clip
is **normalised** to `PEAK_FS` rather than multiplied by it. Multiplying left the
greeting at 27.5%FS and audible RMS at 4.0%FS; normalising gives 48.0%FS peak,
but only 8.2%FS RMS because the source is peaky (crest factor 5.8-8.4).
`ulab.numpy` has no `abs()`, so the peak comes from `np.max` and `np.min`.

Normalising the peak is not the same as being loud. The recorded audio path
reaches **24.3%FS RMS at the same 48%FS peak** (crest 1.98) because its finaliser
compresses first and scales afterwards, so mid-level blocks gain `1/frac` while
the loudest stay on the red line. The TTS path applies the same block compressor
with its own threshold, because the recording value is tuned for a source whose
crest is already about 3:

| Compression | Peak | RMS | Crest |
| --- | --- | --- | --- |
| none (normalise only) | 48.0%FS | 8.2%FS | 5.84 |
| `frac` 0.50 (recording value) | 48.0%FS | 9.8%FS | 4.90 |
| **`frac` 0.30 (TTS value)** | **48.0%FS** | **10.7%FS** | **4.50** |
| `frac` 0.25 / 0.18 | 48.0%FS | 10.6 / 10.2%FS | 4.5 / 4.7 |
| gain floor 64 → 16 | 48.0%FS | 10.7%FS | 4.50 |

`frac` 0.30 is the plateau: lowering it further, or lowering the gain floor to
16, changes nothing, because block compression cannot push a speech waveform's
crest below about 4.5. Going louder needs a look-ahead limiter that smooths the
envelope instead of only cutting blocks, which is a different algorithm and not
implemented. The +2.5 dB is what this compressor gives.

Greeting files are a **cache, not a record**: this firmware has no `os.sync()`,
and a reset soon after a write can lose it (measured: four of eight freshly
written WAVs were gone after a restart). The greeter therefore reports a file it
could not open, and the app rebuilds that one on the next frame instead of
staying silent. The manage overlay also offers a batch rebuild for identities
enrolled before this feature.

Playback discipline, in `audio/greet.py`:

- The WAV is written through `device.WavPlayer`, i.e. the differential stereo
  path the BTL amplifier requires, opened per greeting and closed with
  `stop_stream()` then `close()`.
- **`start()` opens muted**, so `unmute()` is mandatory: without it every
  `pump()` reports "data" forever and a 0.9 s clip is still playing after 12 s
  (measured).
- One 320-frame block is only **13 ms** of audio at 24 kHz while a recognition
  frame is **39 ms**, so pumping a single block per frame starves the AO buffer.
  `pump()` writes until progress catches up with the wall clock instead (capped
  at 6 blocks). The side effect is that the frame rate roughly halves while a
  greeting plays; the audio stays continuous, which is the priority for a
  one-second event.
- The greeting is scaled to **48%FS** before the 16-bit write. The codec
  saturates near 390 mV and 48%FS is the 350 mV red line, so a full-scale write
  would clip. Same calibration as the recording path.

Trigger rules, so a person standing in front of the camera is announced once:

| Constant | Value | Purpose |
| --- | --- | --- |
| `TRIGGER_FRAMES` | 8 (0.32 s) | the name must repeat before it is spoken, filtering a one-frame misread |
| `LOST_FRAMES` | 10 (0.4 s) | the same person leaving and returning is only re-armed after this many frames with no face, so detector flicker does not repeat the greeting |
| `COOLDOWN_MS` | 8000 | the same identity is not spoken again within 8 s |

Two details that decide whether a greeting happens at all, both found by a
failing case rather than by reading the code:

- **A different name re-arms immediately.** Re-arming only after a gap with no
  face misses the ordinary case of one person replacing another while the
  detector still sees somebody in every frame, which left the second person
  unannounced until the app was restarted.
- **The frame threshold is a lower bound, not an equality test.** A trigger
  frame that lands while a previous clip still plays has to be skipped, and
  testing `streak == TRIGGER_FRAMES` then never matches again for as long as the
  person stays in view. With `streak >= TRIGGER_FRAMES` the greeting starts as
  soon as the player is free.

The per-name cooldown is what stops a misrecognising detector from alternating
between two identities and speaking continuously; the 8-frame agreement
requirement is the primary filter, and the cooldown only bounds the worst case.

Speeches are never queued: while a WAV is playing, new triggers are dropped.

## Face liveness (spoof detection)

Model: `face_liveness_rgb.kmodel` (380096 B, 112x112 input). It runs after the
detector, on a crop of the largest face.

```python
score = outputs[0].flatten()[1]      # shape [1, 2], softmax summing to 1.0
```

- **Index 1 is the live probability**, index 0 is the spoof class. Reading index 0
  inverts every verdict.
- The pipeline is crop (**expanded** by 224x192) -> resize -> pad grey `[128,128,128]`.

Both choices above are load-bearing, and both are counter-intuitive enough that
they were got wrong first:

| Pipeline | live person | screen photo | usable |
|---|---|---|---|
| expanded crop, index 1 | **0.9989-0.9997** | **0.0001-0.0004** | yes |
| tight box crop, index 1 | 0.06-0.32 | 0.11-0.33 | **no, overlapping** |

A tight crop collapses both classes onto the same values, even though it looks
like the more faithful input. The extra head-and-shoulders margin is what the
model reads. With the expanded crop the classes sit three orders of magnitude
apart, so 0.50 is safe with a wide margin.

Measured: run 1.36 ms (constant, so the cost is the ai2d crop/build), model load
78 ms, detector load 198 ms. The app holds 25.4 fps with a face in view, and
verdicts measured live 0.9989-0.9997 against 0.0001-0.0004 for a photo replayed
on a monitor.

## Memory discipline

With the GUI home screen resident, the MicroPython heap has only about 2.3 MB
left and it is fragmented. A KPU output `to_numpy()` (705 KB contiguous) fails
intermittently, even though `np.zeros(705 * 1024)` alone succeeds, because the
ai2d/kpu native arena plus LVGL buffers slice the contiguous space.

- Mark the app `"release_home": True` in the `APPS` table. `open_app` then
  loads the app screen first and calls `_release_home_memory()`, which deletes
  `home_scr`, clears `BAKED_TILES`, dots, pager and clock images, freeing about
  1.5 MB (free 2.3 → 3.9 MB). `close_app` rebuilds the home screen and
  `_reload_tile_cache()` restores tiles from SD in about 0.7 s.
- **Never delete the active screen.** Switch to the app screen first; deleting
  a live screen leaves LVGL with a dangling pointer and kills touch input.
- Inside `_infer`: release the output tensor right after `to_numpy()`;
  transpose and slicing are views and allocate nothing; skip full-frame
  sigmoid; pass non-contiguous grids to `aidemo` without copying (it accepts
  views, saving 705 KB).
- Call `gc.collect()` every 8 frames in `step()`. LVGL image sources and
  sensor frames form reference cycles, so refcounting alone never frees them.
  Without the periodic collect the contiguous heap fills and `to_numpy()`
  starts failing after a few dozen frames (observed around frame 245).

Measured after `release_home`: 205 steps over 20 s with zero errors, minimum
free heap stable at 3.69 MB with no downward trend; about 10 fps inside the
app because the LVGL FULL repaint (28-80 ms) stacks on the 31 ms inference; 12
stable LVGL objects.

### kmodel weights are native memory, not Python heap

Loading ten models and keeping them all resident changes the MicroPython heap by
about 1 KB: `load_kmodel` puts the weights and the KPU arenas outside the GC
heap. Measured with `_probe_preload_budget.py`: ten `nn.kpu()` instances held
simultaneously, heap 1664 KB before and 1674 KB after.

Inference also still works with all ten held. `_probe_preload_run.py` ran
`yolov8n_320` (320 in), `face_detection_320` (320 in) and `ocr_det_int16`
(640 in) back to back: 3/3 succeeded, ai2d 2-3 ms, kpu 25 / 4 / 223 ms, output
shapes as documented. The ten files total about 32.8 MB.

Consequence for design: a boot-time preload is a memory question about the NPU
side, not about the Python heap, and it is affordable on this board. It does
**not** replace `release_home`: that flag exists to give `to_numpy()` a
contiguous 705 KB block, which is still needed because the home screen leaves
the heap at 2.3 MB and fragmented.

## Reference numbers (yolov8n_320, 640x360 into 320, 40 frames)

- kmodel load about 560 ms; free heap about 3.92 MB afterwards.
- 32 fps. Median per-stage: snapshot 0.5 ms / ai2d 1.0 ms / kpu 28 ms /
  post 1.4 ms.
- Minimum free heap while running about 3.96 MB, stable, no leak.
- `yolov8n_320.kmodel` is a COCO 80-class model, 3654664 bytes.

## Probe notes

- `from nncase_runtime import nn` raises ImportError on the board; only
  `import nncase_runtime as nn` works.
- After a native crash the serial port reports `ClearCommError(22)` or
  `OSError(995)`; a soft reset does not help and a physical reset is needed.
- 921600 baud sometimes fails to connect; falling back to 115200 is reliable.

See `docs/reset-matrix.md` for which failures survive a soft reset.
