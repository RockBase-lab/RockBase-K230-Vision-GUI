# hardware/ — device access (L1)

The only layer allowed to touch a peripheral: PWM, ADC, I2C, GPIO, the DSI panel
and the camera's frame geometry. Everything here is about the *device*; nothing
here draws a widget or decides a user flow.

## Contents

| Module | Owns |
| --- | --- |
| `panel.py` | `WIDTH` / `HEIGHT` / `TYPE` / `FLAG`, the panel identity the display bring-up needs |
| `backlight.py` | PWM channel on GPIO25, `MIN_PERCENT` / `MAX_PERCENT`, `attach` / `set_percent` |
| `battery.py` | ADC5 pack-voltage sampling, divider constants, OCV percentage |
| `battery_trace.py` | optional per-batch ADC trace to `/data/battery` |
| `camera_fov.py` | sensor geometry, preview crop, `set_preview` |

## Rules

1. **May import: the runtime only.** Never `core/`, `ui/`, `net/`, `audio/`,
   `apps/` or `shell/`. `core/config.py` imports `backlight.MIN_PERCENT`, so a
   reverse import would close a cycle; a hardware module that needs a user
   preference takes it as an argument.
2. **No LVGL.** A hardware module returns values; the caller draws them.
3. **No interaction feedback.** The click tone stays in `ui/beep.py`: it binds to
   `lv.EVENT` and exists to give tactile feedback, so it is a UI concern even
   though it drives a PWM. `backlight.py` moves here because it is panel power,
   consumed by `core/config.py`'s brightness default.
4. **`MIN_PERCENT` has exactly one definition**, in `backlight.py`. `core/config.py`
   imports it as the default for `display.brightness`; do not restate the number.
5. **`battery_trace` stays with the hardware it traces**, not with the app that
   toggles it. The Battery app turns it on; this module does the writing.
6. **Never open ADC channel 4** (`docs/board-hardware.md`).
7. **`Sensor()` is once per VM lifetime.** Build it here, but expect the caller to
   own its lifetime (`docs/reset-matrix.md`).

## Status

Populated. `panel.py` holds constants only; the bring-up that consumes them is
`shell/display_init.py`.
