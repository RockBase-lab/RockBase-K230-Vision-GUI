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
| `_cap_acc_cal.py` | Calibration dialog states, snapshotted |
| `_probe_chart_perf.py` | Chart cost against point count |
| `_probe_chart_dir.py` | Chart method and constant inventory |
| `_probe_kb_layer.py` | Keyboard layer construct, poll, mode guard |
| `_cap_kbnum.py` | Keyboard snapshot on the symbol page |
| `_cap_kbtxt.py` | Keyboard snapshot on the text page |
| `_probe_beep_race.py` | Click-sound off-time under a blocking C call |
| `_probe_tz_hash.py` | Sha256 of the timezone wheel bitmaps |
| `_cap_run.py` | Run a display probe as a subprocess, capture raw output |
| `_start_launcher.py` | Start the on-board GUI with a single exec |

Snapshot probes print a run-length encoded frame plus base64 blocks. Pipe that
output through a file, then decode it with
`python tools/decode_lv_snapshot.py <capture.txt> "tmp/shot_%s.png"`. Redirect
with Python rather than a shell `>` operator, which may re-encode the file.
