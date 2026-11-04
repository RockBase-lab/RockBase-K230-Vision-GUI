# assets/cache/ — generated output

Pre-baked bitmaps the board reads at boot so it does not have to render them.
**Everything here is generated.** Nothing in this directory may be edited by
hand, and the whole directory may be deleted and rebuilt.

| Pattern | Format | Written by |
| --- | --- | --- |
| `tile_<id>.bin`, `tile_<id>_zh.bin` | `TILE2` header + XRGB8888 pixels | `python tools/gen_tile_cache.py` |
| `member_icon_<id>.png` | PNG, downscaled folder-flyout icon | `python tools/gen_member_icons.py` |
| `tz_t0.bin`, `tz_t1.bin`, `tz_t2.bin` | `TZS1` header + glyph strips | `python tools/gen_tz_rows.py` |

## `TILE2` blob format

```
offset 0   5 bytes  "TILE2"
offset 5   1 byte   BAKE_VER
offset 6   2 bytes  width  (LE)
offset 8   2 bytes  height (LE)
offset 10  2 bytes  colour format
offset 12  2 bytes  name length
offset 14  n bytes  UTF-8 tile name
offset 14+n 1 byte  folder flag
offset 15+n ...     raw pixels, width*height*4
```

## Rules

1. **A cache hit requires all of `magic / BAKE_VER / size / name / folder` to
   match.** A mismatch silently falls back to an on-device bake, so a stale entry
   costs boot time rather than showing wrong pixels.
2. **Bump `BAKE_VER` in `shell/theme.py` whenever a colour, tile size, icon size,
   radius, margin or font size changes**, then re-run the generator. Otherwise the
   board keeps loading the old bitmap and the change appears to do nothing.
3. **Moving an icon does not require a re-bake.** The blob stores the tile name,
   not the source path.
4. **`_zh` entries are a second variant, not a replacement.** The baker emits one
   file per language; both must exist for the language switch to stay instant.
5. **The first boot without a cache bakes on the device** at roughly 1.3 s per
   tile. That is the fallback, not the plan: ship the cache.
6. **Verify a re-bake by hash.** `sha256sum` the directory before and after a
   pure refactor; the hashes must be identical. If they are not, the geometry or
   palette moved and that is a real change.

## Status

Populated, final directory name.
