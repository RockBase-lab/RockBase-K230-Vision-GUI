# assets/icons/ — app icons

The 164x164 tile artwork is baked from these, and the folder flyout draws the
same PNGs at a smaller size.

| Pattern | Contents |
| --- | --- |
| `icon_<id>.png` | one app icon, 112 px ink on a transparent field |
| `../src/src_<name>.svg` | the Lucide source the PNG was rendered from |

## Rules

1. **Do not edit a PNG by hand.** It is regenerated from its SVG by
   `python tools/gen_app_icons.py`.
2. **The `<id>` must exist in `shell/registry.py`'s `APP_ACCENTS`.** An id without
   an accent renders the ink white.
3. **Reusing another app's PNG is allowed** and needs no new asset: `detect`
   points at `icon_vision.png`, for example. Add the alias to the registry only.
4. **Every source SVG must be listed in `../LICENSE.txt`** with its licence.
5. **A new icon means a re-bake of the tile cache** with `BAKE_VER` unchanged
   only if the geometry did not move; a changed icon alone does not invalidate
   existing blobs, but the tile that embeds it must be re-baked to show it.

## Status

Populated: 25 app icons, written by `tools/gen_app_icons.py` from `../src/`.
Members that reuse another entry's PNG are not duplicated here — the registry
points two ids at one file.
