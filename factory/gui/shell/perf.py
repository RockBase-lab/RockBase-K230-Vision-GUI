# Frame cost accounting and the forced-refresh switch.
#
# LVGL's refresh period is the compile-time constant LV_DEF_REFR_PERIOD (30ms),
# which caps the refresh rate at 33fps. Measured on board, a swipe only reaches
# 22-30fps in flush (5-8ms of task_handler plus 2-3ms of flush per loop iteration,
# on top of the sleep), which reads as "not smooth" on an 800x480 panel.
# While a gesture is active -- touching, or within BOOST_TAIL_MS of release so the
# snap animation is covered -- lv.refr_now() forces an immediate repaint and
# breaks that ceiling. It stays off while idle so the CPU is not burned for
# nothing; frames with no dirty area return quickly from refr_now.

FRAME_BOOST = True
BOOST_TAIL_MS = 400   # covers SNAP_ANIM_MS (180ms) with margin

# Diagnostics only: read with a probe, never drawn. Counter updates are a few
# microseconds, cheap enough for the per-frame path.
PERF = {"flush_max": 0, "flush_sum": 0, "flush_n": 0,
        "task_max": 0, "task_sum": 0, "task_n": 0,
        "touch_max": 0, "loop_n": 0}
