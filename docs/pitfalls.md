# Pitfall index

A cross-cutting list of the traps that cost the most time on this board. Each
entry is one line: what you see, why it happens, what to do instead.

This file exists because the traps span the framework, the silicon and the
workflow, so no single source file can own them. Detail lives in the topic
documents; this is the checklist you scan before writing code.

- `docs/board-ai-runtime.md` — ai2d, KPU, post-processing
- `docs/lvgl-binding.md` — the LVGL binding, layout, touch
- `docs/board-hardware.md` — sensors, audio, camera, video
- `docs/performance-budget.md` — costs and the 20 ms budget
- `docs/reset-matrix.md` — which reset clears which failure

---

## A. Traps that hang the board

Recovery for every entry here is a power cycle. Programmatic resets do not help.

| Symptom | Cause | Do instead |
|---|---|---|
| No serial echo, no REPL response | Declared the ai2d input as packed (`RGB_packed`) while passing `[1, 3, H, W]` | Use `RGBP888` with the matching declaration |
| No serial echo after binding a video layer | Stream resolution is not 800x480 | Validate `w == 800 and h == 480` before creating the decoder |
| Hard hang, nothing responds | Called `close()` on an audio stream without `stop_stream()` first | Always `stop_stream()` then `close()` |
| Board is dead after a render | A variable TTF produced by `fontTools instantiateVariableFont` | Use an official static build |
| Board is dead, no Python exception | Allocated a large RGB565 canvas | Draw with `lv.line` and `lv.obj`; ARGB8888 canvas for large areas is also too slow |
| `kd_mpi_ai_set_pdm_clk failed:-1` | PDM input handle leaked | Close streams in `finally`; only a power cycle clears it |
| `kd_mpi_ao_enable_chn failed:-1` | Audio output handle leaked, or the Vision app left mpp resources | Same; power cycle |
| `kd_mpi_vdec_attach_vb_pool failed`, pool id reaching -1 | Decoder destroy raised before the VB pool was released | Repair the pool manually when channel destruction fails |
| `OSError: sensor(N) is already inited` | A second `Sensor()` in the same VM | `machine.reset()`, not Ctrl-D |
| Touch completely dead | Deleted the LVGL active screen | Load another screen first |
| Serial drops with `ClearCommError` / `OSError 995` | A KPU runtime tensor was never released | Release every tensor after `to_numpy()` |
| Board unresponsive mid-probe | Created an `lv` widget without display initialisation | Run the probe with the `--lvgl` flag |

## B. Traps that fail silently

No exception, wrong result. These are more expensive than crashes because
nothing points at the cause.

### AI and imaging pipeline

| Symptom | Cause | Do instead |
|---|---|---|
| Scores collapse to about 0.006 while the same image scores 0.95 on a PC; per-channel statistics still look correct | The AI channel was `RGB888` (packed) while ai2d declared `NCHW_FMT` | Use `RGBP888` on the AI channel |
| Boxes all over the screen | Applied sigmoid before `aidemo`; the scores are raw values that include negatives | Threshold the raw value at 0.3-0.35; apply sigmoid only for display |
| Every detection shifted down by a constant | The post-process does not subtract the letterbox top pad | Subtract `pad_top / scale` (160 for 800x480 into 320) |
| `to_numpy()` fails intermittently after some frames | lvgl image sources and sensor frames form reference cycles | `gc.collect()` every 8 frames |
| Large faces on the right half are invisible | Passed `rgb888p_size` as `[H, W]`, which is used as a clip rectangle | Pass `[W, H + y_off]` = `[800, 640]` for an 800x480 preview |
| Face boxes have no score | RetinaFace post-processing returns four columns only | Do not index a fifth column |
| The first image looks washed out or shifted | Image buffer handed to a widget without holding a reference | Keep the `Image` alive for as long as the widget shows it |

### Image display

| Symptom | Cause | Do instead |
|---|---|---|
| `ValueError: only support RGB565/RGB888 image` | A JPEG-decoded `Image` was passed to a widget | Convert to RGB888 after scaling |
| Colours are off by a channel | `image` stores R, G, B but the LVGL format reads B, G, R | `swap_rb()` before use |
| Entire image hue occasionally flips (yellow to blue), next run is fine | Handing cached pixels to LVGL through the helper function; its colour-format handling is unreliable | Build an explicit `lv.img_dsc_t` with `cf = RGB888`; the desktop tiles take that path and never shifted |
| Image looks like a few wrapped bands | The pixel buffer was not the same size as the widget | Match the frame size to the widget, or resize with `copy(x_scale=, y_scale=)` |
| `crop` did nothing and reported no error | Passed the ROI as a positional tuple | Use the keyword form `crop(roi=(x, y, w, h))` |
| The rightmost image column is brighter by about 8 levels | 32 bpp rendering processes four pixels at a time | Make image width a multiple of 4 |
| Shapes drawn on a grayscale frame do not show | Integer colours are interpreted as RGB, so `255` is blue with luminance 29 | Use `0xFFFFFF` for white |
| Text renders as boxes | The font lacks those code points, often the LVGL private-use symbols | Use a font that carries them, or bake a bitmap |
| Everything looks stale after a rebuild | Module bytecode cache survived the soft reset | `del sys.modules[name]` in the probe |

### Layout and widget behaviour

| Symptom | Cause | Do instead |
|---|---|---|
| Content bunched to one side; a row is wider than designed | A default `lv.obj` carries about 20 px of padding | `set_style_pad_all(0, 0)` on every self-built container |
| A thin light line on the panel's right edge | Default `FLAG.SCROLLABLE` produced a scrollbar | `clear_flag(lv.obj.FLAG.SCROLLABLE)` |
| A widget jumped somewhere unexpected and no error was raised | Passed `TEXT_ALIGN.*` to `set_align()`, whose values collide with `ALIGN.*` | Use `set_style_text_align()` for text alignment |
| Tapping an icon body does nothing, its blank edge works | An `lv.obj` child is CLICKABLE by default and swallows the hit without bubbling | `clear_flag(lv.obj.FLAG.CLICKABLE)` on decorative children |
| A single-page scroll container loses clicks | Touch jitter is read as a scroll, which cancels `CLICKED` | `clear_flag(SCROLLABLE)` when there is only one page |
| Landmark dots linger on screen while the next face loses its own | A shared widget pool was hidden by a trailing "visible count" prefix, so one skipped slot shifted every later slice | Clear each slot's own slice when you visit it and index by the item, never by a visible counter |
| Each page is offset by 11 px more than the last | flex `pad_column` defaults to 11 px and `pad_all(0)` does not clear it | `set_style_pad_column(0)` |
| A snappy animation becomes a mid-animation position | A release does not guarantee `SCROLL_END` once momentum is off | Poll the pressed state instead of waiting for the event |
| Text position is off by 4 px between two labels | Mixed proportional and monospace fonts share no baseline | Use the same font family for the whole row |
| Numbers visibly jitter | Right alignment plus a proportional font | Left align, fixed sign, monospace font |
| Short text still draws a wide black bar | Fixed width instead of content sizing | `set_style_max_width` and let the size follow content |
| A font loaded from SD returns `None` and nothing errors | Missing the `A:` drive prefix | `lv.font_load("A:/sdcard/...")` |
| A font path resolves to nothing | The asset name itself contains `fonts/`, producing `fonts/fonts/` | Keep the directory in the path only once |
| A bin font file is 0 bytes | Inline `open().write()` was never closed | Close the handle explicitly |

### API surface differences

| Symptom | Cause | Do instead |
|---|---|---|
| `TypeError: takes 3 positional arguments` | Style setters require the selector argument | `set_style_max_width(w, 0)` |
| AttributeError on a font object | Some built-in fonts exist in C but are not exported | Try candidates in a loop with try/except |
| Segfault when setting a keyboard map | `set_map` treats the argument as a C pointer | Not callable from Python |
| Charts never update the axis | The method is `set_range`, not `set_axis_range` | `set_range(axis, min, max)` |
| `AttributeError` on `os.path` | Not available in this build | Build paths manually, per-level `mkdir` with `OSError` caught |
| `NameError: __file__` | Code is executed through `exec()` | try/except and fall back to the known directory |
| `AttributeError` on `image.resize` | Declared in stubs but absent at runtime | `copy(x_scale=, y_scale=)` or `mean_pool` |
| `can't convert 'tuple' to str` on `endswith` | Tuple argument unsupported | Test suffix strings individually |
| Array reversal does nothing useful | `flip` and negative-stride slicing are a cyclic shift | Copy into a new buffer |
| Audio doubles in size | `np.clip` promotes int16 to int32 | Clamp with integers without promotion |
| Probe reports 9-24 fps for an app that runs at 50 | Sparse `step()` calls are throttled by the cadence gate | Measure through a real launcher loop |
| A widget reports width 0 | Layout has not resolved yet | Read the context or the snapshot instead |

### Assets and deployment

| Symptom | Cause | Do instead |
|---|---|---|
| The board still shows old bitmaps | Colour or size constants changed without re-baking | Re-run the matching generator and increment `BAKE_VER` |
| A re-baked asset is skipped as "already identical" | Incremental deploy compares size only above 256 KB | Deploy that file with `--force` |
| The board boots an old UI while probes see new code | `launcher.py` was copied wholesale to `/sdcard/main.py` | Autostart must be the 112-byte three-line launcher stub |
| Code changes have no effect | No soft reset after deploying a `.py` | Ctrl-D |
| `SyntaxError` on line 1 of an uploaded file | The file carries a UTF-8 BOM | Strip it before upload |
| Every later probe fails with an empty `UnicodeError` on line 2 | A crashed probe left the transfer temp file locked | Use unique temp names; only a power cycle clears an already-locked file |

### Network

| Symptom | Cause | Do instead |
|---|---|---|
| `wlan.scan()` returns zero entries | The radio is locked to the current channel after about 3 s connected | Disconnect, wait 600 ms, scan, reconnect |
| Reported signal is about 14 dB too good | `wlan.status('rssi')` is optimistic | Use the scan cache; this is only a cold-start fallback |
| `connect()` returns without raising but never connects | Association failure is reported only by the C layer | Check `isconnected()` and record the failure |
| `AttributeError` on scan results | Entries are objects, not dicts | Attribute access; ssid is bytes |
| The radio cannot be reset | `active(False)` is refused by RT-Smart | Power cycle, and restart the access point |

## C. Traps that repeat every session

These do not corrupt anything, they just cost time on every run. Keep them in
muscle memory.

1. **Soft reset before believing a change.** Module bytecode is cached and
   `--reset` does not reliably clear it. Delete the module explicitly in probes.
2. **Measure repaint cost with invalidate plus refresh now.** Measuring through
   `task_handler()` under-reports because the 30 ms refresh period often means
   no repaint happens at all.
3. **Match the audio write block size to `frames_per_buffer`.** Mismatched sizes
   stutter; a silent standalone probe cannot prove continuity because it never
   sees the 28-80 ms frame jitter of a real GUI.
4. **Run probes one command at a time.** Chaining with `;` makes the serial
   guard's cleanup match the parent shell and the output disappears.
5. **Add `--lvgl` whenever a probe touches a widget.**
6. **`Sensor()` is a one-shot per VM.** Between two sensor probes, reset the
   machine.
7. **Never judge `wlan.connect()` success by the absence of an exception.**
8. **Do not trust a stat whose condition you did not verify.** Several earlier
   conclusions were wrong because the measuring loop never triggered the work.

## D. Diagnosis recipes

### Is it the WiFi password or the association

Run two connects: one with the stored password, one with a deliberately wrong
password. If both behave identically — same `run connect failed.` from the C
layer, the same roughly 15.5 s block, `isconnected()` false throughout — the
failure happens before password validation, so it is not a password problem.

Then check whether the AP appears in a scan, cross-check with the official
network manager, and as a last step power cycle the board **and restart the
access point**, which may have wedged its association table for a client that
keeps failing.

### Which reset do I need

Start at the cheapest level and escalate. Anything native needs a power cycle.

1. Delete the module from `sys.modules` for a stale bytecode symptom.
2. Ctrl-D for most Python-level state.
3. `machine.reset()` for the sensor singleton.
4. Power cycle for handle leaks, VB pools, a wedged supplicant, locked files and
   a deleted active screen.

The full table is in `docs/reset-matrix.md`.

### Is this number real

Before recording a measurement, confirm the loop actually did the work:

- Did the widget get dirty, or was the refresh throttled away?
- Did the probe call `step()` often enough to hit the cadence?
- Was the sensor's first frame discarded?
- Did the module come from the current source, or from cache?

If any answer is uncertain, the number is not ready to be written down.
