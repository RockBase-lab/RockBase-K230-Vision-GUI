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

Ink is teal (`0x00B894` edges, `0x5CE8C0` nodes) rather than white, because white
washed out against bright scenes; both match the app accent.

Cost: 26 nodes and 63 edges put the app at about 45 ms/frame, 22 fps at 800x480
under FULL render. The detector plus the preview repaint already cost 36-40 ms,
so the overlay is only part of the frame and trimming more edges buys little.

Opening an audio output stream after this chain still works, so the RetinaFace
single-model path does not reproduce the audio contamination seen with the
full Vision chain. See `reset-matrix.md`.

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
