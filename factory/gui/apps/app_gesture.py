# Gesture: live hand detection (hand_det.kmodel, yolov5 anchor-based, 512x512).
# v1 shows hand boxes only; the full dynamic-gesture pipeline (hand keypoints +
# a temporal classifier over five frame inputs) needs two more models and is
# left for a later pass.

from apps.anchor_det import AnchorDetApp

CFG = {
    "name": "Hand Det",
    "path": "/sdcard/examples/kmodel/hand_det.kmodel",
    "input": [512, 512],
    "labels": ["hand"],
    "anchors": [26, 27, 53, 52, 75, 71, 80, 99, 106, 82, 99, 134,
                140, 113, 161, 172, 245, 276],
    "strides": [8, 16, 32],
    "conf": 0.20,
    "nms": 0.50,
    "min_h_frac": 0.08,
}


def create_app(content, ctx):
    return AnchorDetApp(content, ctx, CFG, "Gesture")
