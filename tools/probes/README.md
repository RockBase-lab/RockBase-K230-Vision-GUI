# Board probes

One-shot scripts that run **on the board** over USB serial and exit. They exist
so a measurement recorded in `docs/` can be reproduced instead of trusted.

## Running one

```powershell
python tools/_run_remote.py tools/probes/_probe_sensor_release.py
python tools/_run_remote.py --lvgl tools/probes/_probe_chart_perf.py
python tools/_run_remote.py COM9 115200 tools/probes/_probe_tz_hash.py
```

Always add `--lvgl` when the script creates any `lv` object or calls
`lv.task_handler()`; the flag injects the same display initialisation the GUI
uses. Without it the first widget creation hangs the board.

Every probe terminates on its own. `canmv_driver.py run` keeps the port open
after the script ends and is not suitable for this purpose.

## Rules that keep the board alive

1. Soft reset (Ctrl-D) after deploying a `.py`, and delete the module from
   `sys.modules` inside a probe that re-imports changed code. Bytecode caches
   survive soft resets in practice.
2. Wrap audio and camera teardown in `try`/`finally`. A leaked PDM or AO handle
   cannot be cleared by any reset short of a power cycle. See
   `docs/reset-matrix.md`.
3. Proxy `stop()`/`deinit()` to the real sensor when substituting a stub, or the
   capture device stays claimed.
4. Never delete the LVGL active screen.
5. Run one command at a time. Chaining commands with `;` in a single shell line
   makes the serial guard's process cleanup match the parent shell.
6. A host-side disconnect does not stop the on-board script; the port then
   opens but returns nothing.
7. Never call `kpu.get_output_tensor()` before `kpu.run()`. It blocks in the
   native layer with no exception and no output, and the serial link dies with
   `ClearCommError`; only a power cycle recovers it. To learn a model's output
   layout, run it once on a synthetic frame first.

Two frame-synthesis traps, both hit while building the scan probe:

- Integer colours passed to `draw_rectangle` on a GRAYSCALE image are
  interpreted as RGB, so `255` draws blue with luminance 29. White is
  `0xFFFFFF`.
- On the host side, `qrcode.get_matrix()` returns `True` for **dark** modules.
  Inverting the polarity makes `find_qrcodes` fail to see the code.

See `docs/pitfalls.md` for the full trap index.

## Index

| Script | What it measures |
| --- | --- |
| `_probe_sensor_release.py` | Sensor singleton state and whether stop/deinit frees it |
| `_probe_vision_exit.py` | Vision exit path without a snapshot |
| `_cap_vision_full.py` | Immersive screen has no status bar; content is full screen |
| `_probe_cam_unavailable.py` | Missing-camera panel through the real launcher |
| `_probe_cam_apps_all.py` | Camera apps under constructor failure and runtime drop |
| `_probe_scan_app.py` | QR detection with a synthetic frame |
| `_gen_qr_matrix.py` | Host-side QR matrix generation for the scan probe |
| `_cap_faces_num.py` | Face app detection count and layout |
| `_probe_face_name.py` | Face group display name in the on-board source |
| `_probe_face_dots.py` | Face app landmark slices stay in sync with their boxes |
| `_probe_face_conf_sweep.py` | Box count against confidence threshold on one captured frame |
| `_probe_face_points_cost.py` | `lv.line` mesh cost and landmark model chain timings |
| `_probe_face_points_app.py` | Points app through the real launcher: frame rate, views, points |
| `_probe_face_mesh.py` | Triangulated mesh draw cost on real face geometry |
| `_cap_face_points.py` | Points app screen capture for host-side viewing |
| `_probe_landmark_assets.py` | Landmark model files and the aidemo symbols that exist |
| `_probe_landmark_dump.py` | Dumps the 106 landmark screen coordinates of one face |
| `_probe_plate_assets.py` | Plate model files on board and the aidemo symbols that exist |
| `_probe_plate_contract.py` | Plate model output dims, post-process layout, crop layout |
| `_probe_plate_crop_args.py` | Quad argument form for `ocr_rec_preprocess` (list of float ndarrays) |
| `_probe_plate_crop_layout.py` | Input layout the crop pre-process expects (CHW) |
| `_probe_plate_rec_chain.py` | Crop channel count and the recognition kpu on a crop |
| `_probe_plate_rec_input.py` | Colour vs grey crop: which reaches the right plate text |
| `_probe_plate_channel_order.py` | Channel order of `to_numpy_ref()` on an RGB888 image |
| `_probe_plate_det_domain.py` | Whether the plate post-process honours `display_size` (it does not) |
| `_probe_plate_det_sig.py` | Argument semantics of both plate post-processes (LPD_640 hangs) |
| `_probe_plate_geometry.py` | Colour-scan ground truth vs the detected quad, crop export |
| `_probe_plate_app.py` | Plate app in the real launcher: known photos, vote logic, camera loop, teardown (needs `--lvgl`) |
| `_probe_plate_stability.py` | Crop strategies per character confidence on degraded frames |
| `_probe_plate_widen.py` | Symmetric widening vs the raw quad, on four plate sizes |
| `_probe_plate_right_widen.py` | Right-edge-only widening; scores each strategy out of seven frames |
| `_probe_plate_stability_run.py` | 20 s live run: raw reading distribution vs displayed label |
| `_probe_plate_live_diag.py` | Live frame: every raw box with its score, gate verdict, and decode |
| `_probe_plate_scores.py` | What each post-process return element holds (quads, rects, scores) |
| `_probe_plate_frametime.py` | Per-segment cost of one plate iteration inside the real loop |
| `_probe_plate_loopvar.py` | Preview rate for three loop structures (detect every frame / every 2nd / none) |
| `_probe_plate_sensor_order.py` | Whether the RGBP888 plane order matches the RGB888 packed order |
| `_probe_plate_font.py` | Baked CJK plate font loads and renders (needs `--lvgl`) |
| `_probe_plate_caption_style.py` | Caption font size and ink colour (needs `--lvgl`) |
| `_cap_plate.py` | Captures the caption region to a PNG for visual checks (needs `--lvgl`) |
| `_cap_vision_folder.py` | Captures the Vision flyout, where member icons are actually drawn |
| `_probe_imu_tear.py` | Read tearing signature at exactly 256 LSB |
| `_probe_imu_lock.py` | Locking sequence effect on peak-to-peak and outliers |
| `_probe_imu_accuracy.py` | Noise, offset and magnitude for a static board |
| `_probe_imu_pages.py` | Three-page paging, wrap-around, small-drag rejection |
| `_probe_real_launcher.py` | App inside the real launcher: touch, errors, frame rate |
| `_probe_dash_dir.py` | Dashboard direction from geometry, no screenshot |
| `_probe_dash_cost.py` | Dashboard geometry and flush cost per frame |
| `_probe_motion_rate.py` | I2C read cost and step interval |
| `_probe_motion_render.py` | Text width and repaint cost |
| `_probe_motion_50hz.py` | Live-row cost: 7 labels against 70 |
| `_probe_motion_50hz_verify.py` | Five-second frame-rate statistics |
| `_probe_motion_slowframe.py` | History-row fill under a slow loop |
| `_probe_acc_cal.py` | Six-face calibration maths and file round-trip |
| `_probe_battery_adc.py` | ADC5 divider: read method agreement, pair-read corruption, enable-pin effect |
| `_cap_acc_cal.py` | Calibration dialog states, snapshotted |
| `_probe_chart_perf.py` | Chart cost against point count |
| `_probe_chart_dir.py` | Chart method and constant inventory |
| `_probe_kb_layer.py` | Keyboard layer construct, poll, mode guard |
| `_cap_kbnum.py` | Keyboard snapshot on the symbol page |
| `_cap_kbtxt.py` | Keyboard snapshot on the text page |
| `_probe_beep_race.py` | Click-sound off-time under a blocking C call |
| `_probe_tz_hash.py` | Sha256 of the timezone wheel bitmaps |
| `_probe_sensor_crop.py` | Crop rectangle the firmware keeps for an 800x480 preview |
| `_probe_sensor_fov.py` | Explicit crop window: stored attributes, snapshot cost, saved frames |
| `_probe_fov_channels.py` | Shared window on both channels, per-format snapshot cost |
| `_probe_scan_detect_cost.py` | `find_qrcodes` at 640x480 against 800x480 |
| `_probe_vision_fps.py` | Vision app in the real launcher: frame rate and box count |
| `_probe_pose_kp.py` | Pose models on board, post-process layout, keypoint offset, skeleton draw cost |
| `_probe_pose_app.py` | Pose app in the real launcher: frame rate, views, person filter |
| `_cap_pose.py` | Pose app screen capture for host-side viewing |
| `_probe_recognize_assets.py` | Recognition models, the feature database directory, aidemo symbols |
| `_probe_storage_persist.py` | Free space on `/sdcard` vs `/data` and whether a marker survives a reset |
| `_probe_storage_api.py` | `os.rename`/`remove`/nested `mkdir`, json, float32 round-trip |
| `_probe_ulab_feature.py` | Vectorised vs Python-loop normalise and dot on 512 floats |
| `_probe_recognize_model.py` | Verification chain: memory, shape, timing, same-subject scores |
| `_probe_recognize_app.py` | Recognize app end to end: enroll, reload, match, delete, fps |
| `_probe_rec_photo.py` | Photo enrollment: PHOTO switch, src tagging, match filter, persistence |
| `_probe_tts_measure.py` | TTS pipeline: model load, synthesis cost, ASCII names, heap |
| `_probe_tts_leak.py` | Three load/release cycles plus the `ttszh` frontend, to prove no leak |
| `_probe_tts_level.py` | Greeting peak/RMS and envelope, to see whether a name is voiced at all |
| `_probe_tts_pinyin.py` | Which text forms the frontend speaks: Latin, plain pinyin, tone marks, Han |
| `_probe_tts_names.py` | Rebuild all greetings and assert every name segment carries audio |
| `_probe_loudness.py` | Peak/RMS/crest of the recorded audio against the greetings |
| `_probe_no_voice.py` | Greeting files vs enrolled identities, plus the persisted switches |
| `_probe_voice_restore.py` | Turn the greeting switch back on and verify every WAV is readable |
| `_probe_tts_frac.py` | Compression threshold sweep: which value actually raises the RMS |
| `_probe_tts_floor.py` | Gain-floor sweep, to show the compressor has plateaued |
| `_probe_greet.py` | Greeting trigger matrix, playback completion, stream cleanup |
| `_probe_greet_switch.py` | Identity switches A->B, A->B->A, busy player, frozen frames, cooldown expiry |
| `_probe_greet_bench.py` | Per-block cost split: stereo conversion, WAV read, blocking write |
| `_cap_face_recognize.py` | Screen capture of the Recognize app with a match |
| `_cap_recognize_empty.py` | Screen capture of the empty-database panel |
| `_probe_back_out.py` | Layered CANCEL ladder on the real launcher, incl. page restore |
| `_probe_liveness_model.py` | Liveness output index, and both crop modes on the same frame |
| `_probe_liveness_app.py` | Liveness app end to end: verdict, presets, freeze, fps |
| `_cap_run.py` | Run a display probe as a subprocess, capture raw output |
| `_start_launcher.py` | Start the on-board GUI with a single exec |

Snapshot probes print a run-length encoded frame plus base64 blocks. Pipe that
output through a file, then decode it with
`python tools/decode_lv_snapshot.py <capture.txt> "tmp/shot_%s.png"`. Redirect
with Python rather than a shell `>` operator, which may re-encode the file.
