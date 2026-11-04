# Panel identity for the RockBase board: which driver, what geometry, how it is
# rotated. Constants only, so every layer can read them without touching lvgl.
#
# The ST7701 on this board is mounted rotated the opposite way to the SDK's
# default landscape orientation, so DISPLAY_FLAG corrects it at the display layer.
# The FT5316 touch driver's default rotation already matches the corrected panel,
# which is why TOUCH(0) takes no extra rotate argument.
#
# ALIGN_UP(800, 16) == 800 during bring-up because 800 is a multiple of 16; the
# alignment call is kept so a future panel width that is not a multiple still
# yields a buffer the DSI path accepts. Measured on board: FLAG_ROTATION_270
# accepts an 800x480 buffer.

from media.display import Display
from media.media import ALIGN_UP

WIDTH = ALIGN_UP(800, 16)
HEIGHT = 480

TYPE = Display.ST7701
FLAG = Display.FLAG_ROTATION_270
