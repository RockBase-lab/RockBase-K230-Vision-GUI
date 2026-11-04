# assets/widgets/ — control faces

Bitmaps that dress an LVGL control which cannot be styled into shape: button
faces, the keyboard's password eye, the multi-touch hint.

| Pattern | Contents |
| --- | --- |
| `btn_play.png`, `btn_rec.png`, `btn_stop_play.png`, `btn_stop_rec.png` | Audio app transport buttons |
| `btn_tone_start.png`, `btn_tone_stop.png` | tone sweep buttons |
| `icon_kb_eye.png`, `icon_kb_eye_off.png` | keyboard password reveal toggle |
| `hint_multitouch.png` | Touch app multi-touch hint |

## Rules

1. **Only for shapes LVGL cannot draw.** A solid rounded rectangle is
   `lv.obj` + radius in `shell/theme.py`; do not add a PNG for it.
2. **Never resolve these paths from the calling module's own directory.** That
   mistake made the keyboard's eye icon vanish with no log line, because
   `_load_png` turns `ENOENT` into `None`. Use `core.paths.asset_path()` and the
   single `_load_png` in `ui/image.py`.
3. **A missing asset must still degrade visibly.** A toggle with no image renders
   nothing and looks like a dead key; check `img.get_src()` on the board after
   moving any of these files.
4. **The eye icons are two states of one control**; adding a third state means
   changing the keyboard code, not just adding a file.

## Status

Scaffold. These files still live flat in `assets/`.
