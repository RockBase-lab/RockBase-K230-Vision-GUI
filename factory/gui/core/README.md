# core/ — infrastructure (L2)

Shared machinery that is not a widget, not an app and not a peripheral: settings,
localisation, version identity, the error type every layer catches, and the two
resource pools (kmodel, font) plus the OCR dictionary.

## Planned contents

| Module | Owns | Comes from |
| --- | --- | --- |
| `paths.py` | `gui_root()`, `asset_path(name)` — the one place that knows the tree depth | new |
| `errors.py` | `AppUnavailable` and its payloads (`CAMERA_UNAVAILABLE`, `NoSignalCounter`) | `apps/error.py` |
| `config.py` | `DEFAULTS`, `get` / `set`, `/data/settings/gui.json` | `config.py` |
| `i18n.py` | language selection, `NAMES`, `CJK_FONT`, the string table | `i18n.py` |
| `version.py` | `GUI_VERSION` | `version.py` |
| `font_pool.py` | lazy bitmap-font handles by name | `font_pool.py` |
| `model_pool.py` | lazy `nn.kpu` handles, one instance per kmodel path | `model_pool.py` |
| `ocr_dict.py` | the OCR word list loaded once at boot | `ocr_dict.py` |

## Rules

1. **May import:** `hardware/` and the runtime. Never `ui/`, `net/`, `audio/`,
   `apps/` or `shell/`. The `core -> hardware` direction is one-way, because
   `config` reads `backlight.MIN_PERCENT`.
2. **No `lvgl` at module scope.** `model_pool` and `font_pool` are imported during
   boot before the display exists, and `paths.py` is imported by every layer. A
   PNG reader needs `lvgl`, so it lives in `ui/image.py` instead.
3. **`paths.py` replaces eight existing resolvers.** It must derive the GUI root
   from its own `__file__` by walking up until `assets/` exists, not by counting
   `/` separators: the current copies hard-code `rsplit("/", 1)` and each breaks
   when its module moves. Keep the `/sdcard/factory/gui` fallback for the case
   where `__file__` is missing under `exec()`.
4. **`config` and `i18n` are peers.** `i18n` calls `config.get` / `config.set`, so
   they must stay in the same layer; neither may move up.
5. **`errors.py` is here because every layer catches it.** It used to sit in
   `apps/`, which made `model_pool` (infrastructure) import `apps/` (business) —
   a cycle in intent even without a Python cycle. Keep the definition below both
   users.
6. **A pool returns a borrowed handle, never an owned one.** `model_pool` owns
   every `kpu` it hands out; a caller must not `release()` it, or the next app
   loses the model. Only the app's own `Ai2d` is freed in `destroy()`.
7. **One source per constant.** `config.DEFAULTS["display.brightness"]` reads
   `hardware.backlight.MIN_PERCENT`; do not restate the number.
8. **Settings writes are immediate and idempotent.** A corrupt or missing file
   must never stop the GUI: `load()` keeps `DEFAULTS` and logs one line.

## Status

Final layout. Every module listed above is here; `paths.py` is new and replaced
eight per-module root resolvers.
