# shell/ — home screen framework (L5)

The phone-style desktop: theme, app registry, tile cache, display bring-up,
touch/keys, the status bar, the boot sequence, and the `Launcher` state machine
that owns navigation and app lifetime.

Nothing below this layer knows it exists. `launcher.py` at the GUI root is the only
importer.

## Contents

| Module | Owns |
| --- | --- |
| `theme.py` | `COL_*`, `TILE_*`, `ICON_SIZE`, `NAME_*`, `STATUS_*`, `CLOCK_*`, `GRID_*`, `ACCENT`, `ACCENTS`, `BAKE_VER` |
| `registry.py` | `APPS`, `FOLDER_MEMBERS`, `APP_ACCENTS`, and the `_sym` helper its entries use |
| `splash.py` | the boot progress screen (title, bar, percentage, two status lines) |
| `widgets.py` | `font()`, `no_hit()`, `clean()`, `mem_str()` — the LVGL rituals every screen builder needs |
| `perf.py` | `FRAME_BOOST`, `BOOST_TAIL_MS`, and the `PERF` frame-cost counters |
| `tile_cache.py` | `TILE2` blob format, `cache_load` / `cache_save`, `bake_tile`, `bake_all_tiles`, `reload_all`, the tile label font |
| `display_init.py` | `lvgl_setup`, the flush callback, `backlight_on` |
| `touchscreen.py` | `TouchScreen`, `instance()`, the raw point queue |
| `buttons.py` | `HardwareButtons` (CANCEL / OK) |
| `context.py` | `AppContext` handed to every `create_app` |
| `statusbar.py` | status icons, clock digits, battery indicator, `StatusBar` |
| `boot.py` | `BOOT_MODELS`, `BOOT_FONTS`, `BOOT_*_MS`, `boot_load` |
| `home.py` | `class Launcher` — navigation, folders, paging, app lifetime, the main loop |

## Rules

1. **May import:** everything below (`core/`, `hardware/`, `ui/`, `net/`,
   `audio/`, `apps/`). `apps/` is reached by name at runtime
   (`__import__(meta["module"])`), never as a static import.
2. **Never import `launcher.py`.** See `__init__.py`.
3. **Asset paths come from `core/paths.py`**, not from a resolver of its own; the
   PNG reader is `ui/image.py`. Both are below this layer by design.
4. **`theme.py` and `registry.py` are parsed as text, not imported.** Five tools
   read those literals with regexes: `gen_tile_cache.py`, `gen_app_icons.py`,
   `gen_status_icons.py`, `gen_clock_digits.py` and `_doctor.py`. They all go
   through `tools/_gui_source.py`, which is the only place that names these
   files; moving a table means editing that one module. Keep each literal on one
   line in the documented form (`NAME = 0xRRGGBB`, `"id": "value"`), or the
   regexes fail silently and produce a stale artefact.
5. **Adding a tile is two edits, not one:** the `APPS` row in `registry.py` and
   the matching `APP_ACCENTS` row. An id without an accent renders white.
6. **`class Launcher` stays in one file.** It is large but its state and closures
   are shared; splitting it was assessed and rejected. Everything *around* it
   lives in its own module above.
7. **Deleting the LVGL active screen is forbidden.** Load another screen first
   (`docs/reset-matrix.md`).
8. **Tile cache entries validate `magic / BAKE_VER / size / name / folder`**, not
   file paths, so moving an icon does not require a re-bake. Changing a colour,
   size or margin does.
9. **Read live module state through a function.** `touchscreen.instance()` exists
   because `from shell.touchscreen import TOUCH_INPUT` captures `None` at import
   time and never follows the assignment. The same applies to anything else this
   package rebinds after import.

## Status

Fully populated. `launcher.py` at the GUI root is now the entry point only:
`sys.path` bootstrap plus `main()` (about 100 lines).

Every module here must be imported after that bootstrap: under
`exec(open(...).read())` the GUI root is not importable until it runs, and an
import placed above it fails with `no module named 'shell'`.
