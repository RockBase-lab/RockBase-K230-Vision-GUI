# Interaction layer: what the user touches, hears and sees while swiping.
#
#   ui.paging    horizontal paging, snap and drag thresholds
#   ui.kb_layer  the reusable keyboard overlay
#   ui.beep      click feedback through the PWM buzzer
#   ui.image     PNG -> lv.img_dsc_t, the one loader every layer shares
#
# beep belongs here rather than under audio/: it drives GPIO43 through PWM and
# deliberately avoids the codec -> amplifier path, which costs about 47ms per
# open and would be felt as lag on every tap. It also binds to lv.EVENT, so the
# hardware layer (which never imports lvgl) cannot own it.
#
# The panel backlight is not here: it is panel power, so it lives in
# hardware/backlight.py, because core/config.py needs its MIN_PERCENT as the
# brightness default and a core -> ui import would point upward.
#
# This package intentionally re-exports nothing, so importing it never pulls in
# lvgl or a PWM channel. Import the submodule you need:
#
#   from ui import beep
#   from ui import paging
