# ui/ — reusable widgets (L3)

Interaction pieces that more than one screen uses. Each is an LVGL control or a
visual effect, addressed to the widget layer: it may own a widget tree, it must
not own a device or a user flow.

## Contents

| Module | Provides |
| --- | --- |
| `paging.py` | horizontal paging: snap, drag thresholds, shared page-indicator styles, `RawImageCache` |
| `kb_layer.py` | the reusable keyboard overlay, including the CJK face and the password eye |
| `beep.py` | click/tone feedback through the GPIO43 PWM buzzer, plus the `on_click` / `bind` event wrappers |
| `image.py` | `load(path)` / `load_asset(name)`, the one PNG → `lv.img_dsc_t` reader |

## Rules

1. **May import:** `core/`, `hardware/`, and the runtime. Never `apps/` or
   `shell/`. The peer subsystems `net/` and `audio/` are off-limits in practice
   too: a widget that needs sound is handed a callback instead of importing
   `audio` itself, which would make the two layers mutually importable.
2. **Re-export nothing from `__init__.py`.** Importing the package must not pull
   in `lvgl` or claim a PWM channel: `from ui import paging`.
3. **Resolve assets through `core/paths.py`**, never by joining from this
   module's own directory. That mistake once made the keyboard's eye icon
   disappear while `_load_png` silently returned `None` (`docs/pitfalls.md`). The
   PNG reader itself belongs in `ui/image.py`, not in either of the two modules
   that currently define their own copy.
4. **A new widget takes user-visible strings from `core/i18n`**, not a hard-coded
   literal. Today none of these modules imports `i18n`: localisation covers the
   home screen only, and widget strings are still literals. Do not extend that
   pattern to new code.
5. **`beep.py` stays here.** It drives a PWM, but its whole surface is
   `on_click` / `bind` over `lv.EVENT` and its job is to make a tap feel
   acknowledged. Sending it to `hardware/` would force that layer to import
   `lvgl`, and `audio/` is the codec path, not the buzzer.
6. **`backlight.py` does not belong here** and moves to `hardware/`: it is panel
   power, and `core/config.py` needs its `MIN_PERCENT` as the brightness default.
   Leaving it here forced `core/` to import upward.

## Status

Final layout. `backlight.py` moved to `hardware/`, `splash.py` to `shell/` (it
belongs to the boot flow and now takes its palette from `shell/theme.py` instead
of copying it), and `image.py` replaced the four per-module PNG readers.
