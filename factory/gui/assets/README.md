# assets/ — bitmaps, fonts and generated caches

Data only. No `.py` file belongs in this tree; a module that draws from these
files lives in `ui/`, `shell/` or `apps/`.

## Layout

```
assets/
├── icons/            app tile icons        icon_<id>.png
├── status/           status-bar icons      icon_status_*.png
├── glyphs/           clock and timer digits  clock_*.png, timer_*.png
├── widgets/          control faces: btn_*.png, icon_kb_eye*.png, hint_multitouch.png
├── fonts/            baked bitmap fonts (.bin) and the static Latin TTF
├── cache/            GENERATED: tile_*.bin, member_icon_*.png, tz_t*.bin
├── src/              SVG sources for both icon families: src_*.svg
└── LICENSE.txt       attribution for the icon set (required by its licence)
```

Every directory above has its own README with the naming rule and the generator
that writes it.

## Rules

1. **`cache/` is generated output.** It can be deleted and rebuilt; nothing here
   may be hand-edited.
2. **Icons are generated, not drawn by hand.** `tools/gen_app_icons.py` renders
   `src/src_*.svg` into `icons/icon_*.png`. A new app icon needs an `id` in
   that tool's `ICONS` table and a row in `shell/registry.py`'s `APP_ACCENTS`.
3. **Never move an icon to rename an app label.** The path is a string in
   `shell/registry.py`; the label comes from `core/i18n`.
4. **Moving an image does not require a re-bake.** Tile cache blobs validate
   `magic / BAKE_VER / size / name / folder`, not file paths. Changing a colour,
   a tile size or a font size does require bumping `BAKE_VER`.
5. **`LICENSE.txt` stays at this level** and must cover every source icon,
   including any new SVG.
6. **This tree is copied to `/sdcard/factory/gui/assets`** with the rest of the GUI.
   Every `README.md` inside it is skipped by both deploy tools, so the files that
   cost device space are only the bitmaps. Keep the bitmap total small: `/sdcard`
   is a fixed 500 MiB FAT16 with about 76 MiB free after the reference examples.

## Status

Populated but flat: all bitmaps still sit directly in `assets/`. The grouping
above is the target; the cache and fonts directories already match it.
