# Fall detection: yolov5n-falldown (640x640, classes Fall / NoFall).
# Shares the anchor-based shell with Person and Gesture. A "Fall" box means a
# fallen posture was detected; NoFall boxes are upright people and are drawn
# too so the user can see what the model is rejecting.

from apps.anchor_det import AnchorDetApp

CFG = {
    "name": "Fall Y5n",
    "path": "/sdcard/examples/kmodel/yolov5n-falldown.kmodel",
    "input": [640, 640],
    "labels": ["Fall", "NoFall"],
    "anchors": [10, 13, 16, 30, 33, 23, 30, 61, 62, 45, 59, 119,
                116, 90, 156, 198, 373, 326],
    "strides": [8, 16, 32],
    "conf": 0.30,
    "nms": 0.45,
    "min_h_frac": 0.10,
}


def create_app(content, ctx):
    return AnchorDetApp(content, ctx, CFG, "Fall")
