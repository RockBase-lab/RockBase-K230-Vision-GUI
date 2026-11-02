# Inference artefacts

These are **reference copies**, not original to this project. They are the
exact bytes two directories of the official CanMV firmware image carry:

| File | Board path | Purpose |
| --- | --- | --- |
| `kmodel/yolov8n_320.kmodel` | `/sdcard/examples/kmodel/` | 80-class COCO object detection, 320x320 input |
| `kmodel/yolov8n_224.kmodel` | `/sdcard/examples/kmodel/` | COCO object detection, 224x224 input |
| `kmodel/yolo26n.kmodel` | `/sdcard/examples/kmodel/` | COCO object detection, newer network family |
| `kmodel/fruit_det_yolov8n_320.kmodel` | `/sdcard/examples/kmodel/` | Fruit detection demo model |
| `kmodel/face_detection_320.kmodel` | `/sdcard/examples/kmodel/` | RetinaFace 320 face detection |
| `utils/prior_data_320.bin` | `/sdcard/examples/utils/` | Face-detection anchor data (4200 x 4 floats) |

## Firmware-only models

These are loaded by apps but are not committed here, because their bytes are
larger and the firmware image always carries them. They are listed so the
dependency list stays complete; the sizes are the ones measured on board.

| File | Board path | Purpose |
| --- | --- | --- |
| `yolo_license_plate_det.kmodel` | `/sdcard/examples/kmodel/` | Plate detection, 640x640 input, 3.6 MB |
| `licence_reco.kmodel` | `/sdcard/examples/kmodel/` | Plate character recognition (CTC), 220x32 input, 0.67 MB |

## Origin

The CanMV build copies these from the K230 SDK source tree:

```
${SDK_RTSMART_SRC_DIR}/libs/kmodel/ai_poc/kmodel/     -> /sdcard/examples/kmodel/
${SDK_RTSMART_SRC_DIR}/libs/utils/                    -> /sdcard/examples/utils/
```

The K230 SDK is BSD-2-Clause, Copyright (c) 2023 Canaan Bright Sight Co.,
Ltd. The files are redistributed here unchanged and remain under their
origin terms; they are not covered by this project's Apache-2.0 licence.

## Why they are committed

The apps load these from the board path, and the CanMV firmware image
already puts them there — `tools/deploy_board.py` deliberately does not
push this directory. It is kept in the repository so that:

- the dependency list, file names and byte counts are reviewable without
  unpacking a firmware image;
- replacing a model shows up as a reviewed diff instead of an untracked
  change on someone's SD card;
- the licensing question has one place to look.

Do not treat this directory as the deployment source. If a model here and
the one on the board disagree, the board wins and this copy is stale.

## Adding a model

Put the file here only if it is either:

- taken unmodified from an official CanMV firmware image, or
- trained or converted by this project, in which case state that explicitly
  in the table above along with the licence of the source weights.

Never add a model whose source weights are copyleft (AGPL, GPL): its terms
would propagate to this repository and conflict with the Apache-2.0 licence.
