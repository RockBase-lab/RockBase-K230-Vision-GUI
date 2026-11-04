# shell/ — home screen framework (L5)

The phone-style desktop: theme, app registry, tile cache, display bring-up,
touch/keys, the status bar, the boot sequence, and the `Launcher` state machine
that owns navigation and app lifetime.

Nothing below this layer knows it exists. `launcher.py` is the only importer.

## Planned contents

| Module | Owns | Comes from |
| --- | --- | --- |
| `theme.py` | `COL_*`, `TILE_*`, `ICON_SIZE`, `NAME_*`, `STATUS_*`, `ACCENTS`, `BAKE_VER` | `launcher.py` header |
| `registry.py` | `APPS`, `FOLDER_MEMBERS`, `APP_ACCENTS` | `launcher.py` |
| `tile_cache.py` | `TILE2` blob format, `_cache_load` / `_cache_save`, `_bake_tile`, `bake_all_tiles` | `launcher.py` |
| `display_init.py` | `lvgl_setup`, `_flush_cb` | `launcher.py` |
| `touchscreen.py` | `TouchScreen` | `launcher.py` |
| `buttons.py` | `HardwareButtons` (CANCEL / OK) | `launcher.py` |
| `context.py` | `AppContext` handed to every `create_app` | `launcher.py` |
| `statusbar.py` | status icons, clock digits, battery indicator | `launcher.py` |
| `boot.py` | `BOOT_MODELS`, `BOOT_FONTS`, `BOOT_*_MS`, `boot_load` | `launcher.py` |
| `home.py` | `class Launcher` — navigation, folders, paging, app lifetime | `launcher.py` |

## Rules

1. **May import:** everything below (`core/`, `hardware/`, `ui/`, `net/`,
   `audio/`, `apps/`). `apps/` is reached by name at runtime
   (`__import__(meta["module"])`), never as a static import.
2. **Never import `launcher.py`.** See `__init__.py`.
3. **Asset paths come from `core/paths.py`**, not from a resolver of its own; the
   PNG reader is `ui/image.py`. Both are below this layer by design.
4. **`theme.py` and `registry.py` will be parsed as text, not imported.** Five
   tools currently read those literals out of `launcher.py` with regexes:
   `gen_tile_cache.py`, `gen_app_icons.py`, `gen_status_icons.py`,
   `gen_clock_digits.py` and `_doctor.py`. When the tables move, every one of
   them must be repointed in the same change. Keep each literal on one line in
   the documented form (`NAME = 0xRRGGBB`, `"id": "value"`), or the regexes
   fail silently and produce a stale artefact.
5. **Adding a tile is two edits, not one:** the `APPS` row in `registry.py` and
   the matching `APP_ACCENTS` row. An id without an accent renders white.
6. **`class Launcher` stays in one file.** It is large but its state and closures
   are shared; splitting it was assessed and rejected. Readability is bought by
   moving everything *around* it out of `launcher.py`.
7. **Deleting the LVGL active screen is forbidden.** Load another screen first
   (`docs/reset-matrix.md`).
8. **Tile cache entries validate `magic / BAKE_VER / size / name / folder`**, not
   file paths, so moving an icon does not require a re-bake. Changing a colour,
   size or margin does.

## Status

Scaffold. Everything above still lives inside `launcher.py` (about 2 600 lines).
