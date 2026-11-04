# Device access layer (L1). Nothing above it may be imported here.
#
# Deliberately empty: importing this package must not open a PWM channel, claim
# an ADC channel or construct a Sensor. Import the submodule you need:
#
#   from hardware import backlight
#   from hardware import battery
#
# Planned contents: panel, backlight, battery, battery_trace, camera_fov.
# Click feedback (ui/beep.py) is NOT here: it binds to lv.EVENT, so it belongs to
# the interaction layer even though it drives a PWM.
