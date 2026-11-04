# assets/src/ — SVG sources

The vector source of every bitmap the generators produce. The PNG is a build
product; this is the source of truth. Re-rendering at another size, or after an
accent change, needs these files.

| Pattern | Used by |
| --- | --- |
| `src_<name>.svg` | `tools/gen_app_icons.py` → `../icons/icon_<id>.png` |
| `src_status_<name>.svg` | `tools/gen_status_icons.py` → `../status/icon_status_<name>.png` |

## Rules

1. **`src_` is the prefix for app-icon sources and `src_status_` for status-bar
   icons.** The two generators scan the same directory and each claims its own
   prefix; do not rename one into the other's namespace.
2. **One file per icon.** The name is the upstream icon name
   (`src_brain-circuit.svg`), which is not always the app id (`vision`).
3. **Do not optimise by hand**, and do not hand-edit the generated
   `src_status_*.svg`: `gen_status_icons.py` writes those, deriving the colour
   from `COL_BAR`. Editing one is overwritten on the next run.
4. **Add the licence to `../LICENSE.txt`** in the same change that adds a file.
   Lucide is ISC; anything from another set must state its own terms.
5. **These files have no runtime use** but are deployed anyway, because the
   deploy tool copies `assets/` wholesale. Keep them small.

## Status

Scaffold. All 34 SVG files still live flat in `assets/`.
