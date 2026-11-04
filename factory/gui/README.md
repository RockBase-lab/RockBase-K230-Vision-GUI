# factory/gui — layer map

The board-side UI. Its code and bitmaps are deployed to `/sdcard/factory/gui`
(the `README.md` files are not) and run on MicroPython, not CPython.

**Read this file before adding a file anywhere in this tree.** The layout below is
the contract; a module in the wrong layer is a design defect even if it imports.

## Layers

Dependency arrows point only downward. A module may import a module in the same
layer or any layer below it, never above.

```
L6  launcher.py          entry point: sys.path bootstrap, boot, main loop
      |
L5  shell/               home screen framework (theme, registry, tiles, navigation)
      |
L4  apps/                one file = one app, exports create_app()
      |
L3  ui/  net/  audio/    reusable widgets and self-contained subsystems
      |
L2  core/                infrastructure: errors, settings, i18n, version,
      |                  asset paths, font/model pools, OCR dictionary
L1  hardware/            device access only: panel, backlight, ADC,
      |                  camera geometry
L0  (runtime)            MicroPython, lvgl, nncase_runtime, aicube, /sdcard/libs
```

`assets/` holds data, not code, and sits outside the ladder.

### Why `hardware/` is below `core/`

`core/config.py` needs `hardware/backlight.py`'s `MIN_PERCENT` as the default for
`display.brightness`. That constant must have exactly one definition. Putting
`hardware/` first makes the reference a downward one and keeps the single source.
Do not invert this by copying the number into `core/`.

## Where a new file goes

| The new code is | Goes in |
| --- | --- |
| A user-facing app with its own tile | `apps/app_<name>.py` |
| The `APPS` / `FOLDER_MEMBERS` entry, an icon path, an accent | `shell/registry.py` (still `launcher.py` until the split lands) |
| A colour, size, margin or `BAKE_VER` worth | `shell/theme.py` (still `launcher.py`) |
| Home-screen navigation, folder flyout, paging, status bar | `shell/` |
| A reusable LVGL widget used by more than one app | `ui/` |
| Click/sound feedback bound to a widget event | `ui/beep.py` |
| Audio capture, playback, TTS, shutter, tone | `audio/` |
| Wi-Fi association, NTP, network state | `net/` |
| A setting key and its default | `core/config.py` `DEFAULTS` |
| A shared user-visible string or language rule | `core/i18n.py` |
| An `assets/` path | `core/paths.py` — never join one from your own directory |
| A model or font loaded through a cache | `core/model_pool.py`, `core/font_pool.py` |
| An exception type a caller above can catch | `core/errors.py` |
| A PWM/ADC/I2C/Pin access, or camera geometry | `hardware/` |
| A PNG, font binary or tile cache | `assets/` (see its README) |
| A one-off measurement or screenshot script | `tools/probes/` at the **repo root**, never here |

If a new file does not fit any row, the design is not decided yet. Do not drop it
at the GUI root to decide later: the root holds `launcher.py` and nothing else.

## Rules

1. **No cycles.** `core/` must not import `ui/`, `net/`, `audio/`, `apps/` or
   `shell/`; `hardware/` must not import anything above the runtime. `apps/` must
   not import `shell/`; an app talks back through the `ctx` object.
2. **`shell/` never imports `launcher.py`.** The launcher runs through
   `exec(open(...).read())`, so a reverse import executes the whole file a second
   time and builds a second launcher.
3. **One asset resolver, in `core/paths.py`.** It must live at L2, not in
   `shell/`: `ui/`, `apps/` and `core/font_pool.py` all need it and none of them
   may import upward. Every `assets/` path goes through `asset_path()`, and every
   PNG through `ui.image.load_asset()`. Joining a path from the calling module's
   own directory is what once made the keyboard's eye icon vanish with no log
   line (`docs/pitfalls.md`).
4. **Package `__init__.py` files re-export nothing.** Importing a package must not
   pull in `lvgl` or claim a PWM channel. Import the submodule you need.
5. **No re-export shims for old paths.** A moved module is imported from its new
   home only.
6. **These READMEs are not deployed.** `tools/deploy_board.py` and
   `tools/push_factory.py` skip every `README.md`, so document freely: the text
   costs repo space, not the 76 MiB `/sdcard` partition.

## Current status

The ladder is the target. Directories marked *scaffold* exist and hold only this
README plus an `__init__.py`; their contents are still at the GUI root and move
in a later change.

| Path | Status | Still to move in |
| --- | --- | --- |
| `launcher.py` | populated, oversized | out: theme, registry, tile cache, display, touch, buttons, context, status bar, boot, `Launcher` |
| `shell/` | **scaffold** | all of the above |
| `apps/` | populated, clean | — (only `app_*.py` plus `anchor_base.py`) |
| `ui/` | populated, clean | — |
| `net/`, `audio/` | populated, final | — |
| `core/` | populated | — (`paths`, `errors`, `config`, `i18n`, `version`, `font_pool`, `model_pool`, `ocr_dict`) |
| `hardware/` | populated | `panel.py` (the `DISPLAY_*` constants still sit in `launcher.py`) |
| `assets/` | populated, flat | grouping into `icons/ status/ glyphs/ widgets/ fonts/ cache/ src/` |

Only `launcher.py` is left at the GUI root. `config.py` used to be the second root
file; it now lives in `core/`.

## Duplication that was removed

Eight modules used to answer "where is the GUI root?" independently, each with its
own `/sdcard/factory/gui` fallback and its own separator count. All of them now
call `core.paths`. Four copies of `_load_png` likewise collapsed into
`ui/image.py`.

`launcher.py` keeps one `_gui_dir` for the `sys.path` bootstrap. That cannot use
`core.paths`: the package is not importable until the GUI root is on `sys.path`,
so the bootstrap is the one place that still hard-codes the deploy path.

Do not reintroduce a resolver. A module that joins `assets/` from its own
directory breaks when it moves, and it fails silently (`_load_png` turns `ENOENT`
into `None`, so an icon just disappears).

## Coupled files outside this tree

These read this tree and must be updated in the same change as any move:

| File | Reads |
| --- | --- |
| `tools/gen_tile_cache.py` | parses `launcher.py` (theme constants, `APPS`, `APP_ACCENTS`) and `core/i18n.py` (`NAMES`) as text; writes `assets/cache/tile_*.bin` |
| `tools/gen_app_icons.py` | parses `launcher.py` for palette constants; writes `assets/icon_*.png` from `assets/src_*.svg` |
| `tools/gen_status_icons.py` | parses `launcher.py` for `COL_BAR`; writes `assets/icon_status_*.png` and `assets/src_status_*.svg` |
| `tools/gen_clock_digits.py` | parses `launcher.py`; reads `assets/Nunito-Bold-700.ttf`; writes `assets/clock_*.png`, `assets/timer_*.png` |
| `tools/gen_tz_rows.py` | reads `assets/Nunito-Bold-700.ttf`; writes `assets/cache/tz_t*.bin` |
| `tools/gen_member_icons.py` | writes `assets/cache/member_icon_*.png` |
| `tools/gen_touch_hint.py` | writes `assets/hint_multitouch.png` |
| `tools/gen_font.py` | writes `assets/fonts/*.bin` |
| `tools/gen_ui_cjk_charset.py` | reads `core/i18n.py`; writes `tools/ui_cjk_charset.txt` |
| `tools/gen_pinyin_map.py` | writes `audio/pinyin_map.py` (currently through a hard-coded absolute path) |
| `tools/_doctor.py` | parses `launcher.py` for `BAKE_VER` and the `APPS` module/icon pairs; `PACKAGES` (`ui`, `net`, `audio`) and the hard-coded import probe (`ui.paging`, `ui.beep`, `net.service`, `batt`) must both follow the migration: add `core`, `hardware`, `shell` and rename `batt` to `hardware.battery`, or a broken deploy passes |
| `tools/deploy_board.py` | walks this whole tree |
| `tools/probes/*.py` | import `hardware.camera_fov`, `core.model_pool`, `core.config` by module name, and clear them from `sys.modules` (parent `core` included) before re-importing |
| `AGENTS.md`, `README.md` | describe the app layout and the `APPS` table location |
