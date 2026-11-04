# apps/ — one file, one app (L4)

Every module here is an application the user opens from a tile. The contract is
fixed and small:

```python
def create_app(content, ctx):
    """content: an lv.obj spanning the area below the status bar.
    ctx:     context object; call ctx.request_exit() to return home."""
    return app          # app.step() once per loop, app.destroy() on exit
```

`step()` must be non-blocking — it is called once per main-loop iteration.
`destroy()` releases hardware the app claimed (sensor, audio stream, files) and
runs on every exit path.

## Contents

| File | App |
| --- | --- |
| `app_camera.py` | still capture with preview |
| `app_gallery.py` | 2x2 thumbnails and a full-screen viewer over `/data/photos` |
| `app_scan.py` | QR / barcode scan |
| `app_vision.py` | live yolov8n detection (Detect) |
| `app_pose.py` | 17-keypoint skeleton |
| `app_segment.py` | instance masks |
| `app_orient.py` | oriented boxes (YOLO OBB) |
| `app_plate.py` | licence plate detect + recognise |
| `app_ocr.py` | OCR detect + recognise |
| `app_face_det.py` | face detection |
| `app_face_points.py` | face landmarks |
| `app_face_liveness.py` | RGB liveness score |
| `app_face_recognize.py` | detect → align → embed against a local database |
| `app_mask.py` | face parsing |
| `app_gesture.py` | hand detect + keypoints + gesture |
| `app_person.py` | person detection (anchor YOLOv5 shell) |
| `app_fall.py` | fall detection (anchor YOLOv5 shell) |
| `app_touch.py` | touch panel diagnostics |
| `app_imu.py` | IMU dashboard and curves |
| `app_audio.py` | record / play / tone |
| `app_network.py` | Wi-Fi scan, connect, NTP |
| `app_battery.py` | pack voltage and history |
| `app_settings.py` | every user preference |
| `app_placeholder.py` | bring-up placeholder; no product value |

`anchor_base.py` is **not** an app: it is the shared shell for the anchor-based
YOLOv5 models (`app_person`, `app_fall`). It is the only non-app module that
belongs here, because it exists only to be subclassed by apps in this directory.

## Rules

1. **May import:** `core/`, `hardware/`, `ui/`, `net/`, `audio/`. Never `shell/`.
   An app talks back only through `ctx`.
2. **Everything an app creates, it destroys.** Release kpu output tensors right
   after `to_numpy()`; `stop_stream()` before `close()`; a missed release leaks
   native memory and drops the serial link.
3. **Never `release()` a `kpu` from `core/model_pool`.** The pool owns it. Free
   only the app's own `Ai2d`, which is per-app because input geometry differs.
4. **Raise `AppUnavailable`** (from `core/errors`) for a missing model or absent
   camera instead of crashing the home screen; the launcher renders the standard
   unavailable panel.
5. **Runtime files go under `/data/<app>/`**, never `/sdcard` — that partition is
   76 MiB and holds only deployed content.
6. **Asset paths come from `core/paths.py`.** Four apps currently carry their own
   `assets_dir()` with a hard-coded `/sdcard/factory/gui/assets` fallback
   (`app_audio`, `app_imu`, `app_plate`, `app_touch`) and `app_network` builds a
   cache path the same way. Do not add a fifth; call `core.paths.asset_path()`.
7. **Adding an app** also needs a `shell/registry.py` entry (or a
   `FOLDER_MEMBERS` row), an `APP_ACCENTS` id and a re-baked tile. See
   `AGENTS.md` → `## Adding an app`.

## What must not be added here

Anything that is not an app: shared helpers, one-off scripts, probes, test
harnesses. A probe belongs in `tools/probes/` at the repo root; a helper belongs
in `core/`, `hardware/` or `ui/` according to the layer map.

## Status

Populated. Three files are in the wrong place and move out later:
`error.py` → `core/errors.py`, `batt_trace.py` → `hardware/battery_trace.py`,
`anchor_det.py` → `anchor_base.py` (rename only).
