# Person detection: person_detect_yolov5n (640x640, single class).
# Shares the anchor-based shell with Fall and Gesture; only the model config
# differs. conf 0.2 matches the official tuning for this kmodel -- a real
# person measures ~0.88, so 0.2 keeps distance shots without noise boxes.

from apps.anchor_det import AnchorDetApp

CFG = {
    "name": "Person Y5n",
    "path": "/sdcard/examples/kmodel/person_detect_yolov5n.kmodel",
    "input": [640, 640],
    "labels": ["person"],
    "anchors": [10, 13, 16, 30, 33, 23, 30, 61, 62, 45, 59, 119,
                116, 90, 156, 198, 373, 326],
    "strides": [8, 16, 32],
    "conf": 0.20,
    "nms": 0.60,
    "min_h_frac": 0.10,   # drop specks shorter than 10% of the frame height
}


def create_app(content, ctx):
    return AnchorDetApp(content, ctx, CFG, "Person")
