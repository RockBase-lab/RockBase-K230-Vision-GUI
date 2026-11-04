# Visual constants of the home screen and the status bar.
#
# Four offline bakers parse this file as text (they cannot import it: it needs
# lvgl): tools/gen_app_icons.py, gen_tile_cache.py, gen_status_icons.py and
# gen_clock_digits.py. Keep every literal on one line in the form
# NAME = 0xRRGGBB so those regexes keep matching, and bump BAKE_VER whenever a
# value here changes the baked pixels.

# ---- Palette (graphite base + brand cyan, 2026-10-27) ----
# Intent: three cool-tinted dark levels (background < tile < border) keep the screen
# from looking uniformly grey, while colour is spent only on icons and a few
# highlight points.
COL_BG = 0x12141A          # desktop/app background (darkest, blue-black)
COL_BAR = 0x0E1116         # status-bar fill (one level deeper, so the bar recedes)
COL_TEXT = 0xFFFFFF        # primary text
COL_MUTED = 0x6B7484       # secondary text (splash hints etc.)
COL_DOT_OFF = 0x3A4250     # page dot (inactive)
ACCENT = 0x4CC9F0          # brand cyan: active page dot, clock, other highlights
STATUS_H = 56              # status-bar height (fits the large Home button); no bottom bar
# Minimum horizontal inset of a full page (centring pushes the real margin to
# GRID_MARGIN).
GRID_PAD_L = 16
# Target outer margin: icon spacing is derived backwards from it so a page fills up.
GRID_MARGIN = 24
# Grid top/bottom margin (with the dots it makes the vertical layout symmetric).
GRID_V_MARGIN = 28
# Gap from the last icon row down to the page dots.
DOTS_GAP = 16
DOT_H = 12                 # page dot height
# Clock digit cell width (the 32px digit ink is 19px wide, plus slack).
CLOCK_CELL_W = 22
# Clock colon width (the 32px colon ink is 9px wide; 7px would clip it).
CLOCK_COLON_W = 11
CLOCK_H = 36               # clock bitmap height
# Swipe threshold and snap duration come from the paging module so every
# horizontally paged app shares one set of rules; see
# `SWIPE_THRESHOLD_PX = paging.SWIPE_THRESHOLD_PX` below.
# Temporary diagnostic switch: prints release->snap latency and coast distance.
# Off by default because printing every frame costs time; set True while tuning and
# a few swipes will emit [SWIPE] lines.
SWIPE_DIAG = False

TILE_SIZE = 164            # icon block size
ICON_SIZE = 112            # icon image size inside a tile (read by tools/gen_tile_cache.py)
TILE_BG = 0x1E222B         # tile card fill (the offline baker parses this too; never edit one side)
TILE_BORDER = 0x2C3340     # tile hairline border: one level up from the background (same)
TILE_BORDER_W = 1          # border width in px (same)
TILE_RADIUS = 20           # tile corner radius (same)
TILE_PAD = 10              # minimum icon spacing (real spacing is derived from the width; fallback)
NAME_COLOR = 0xC7CDD6      # tile name text colour (parsed by the offline baker)
NAME_BOTTOM_MARGIN = 10    # px from the name ink bottom to the tile bottom (offline baker)
STATUS_BATT_W = 32         # status-bar battery icon width (wifi position depends on it)

TILE_FONT_SIZE = 28

# Version of the tile bitmap cache. Bump it whenever the values above change the
# baked pixels, otherwise the board keeps loading the old bitmaps.
BAKE_VER = 7                       # +1 when tile layout/fonts change, dropping old caches
                                   # v2: added the Gallery tile (APPS order changed, re-bake all)
                                   # v3: placeholders replaced by 10 new app icon tiles (bake all)
                                   # v4: palette A (tile fill/border and per-app icon tint)
                                   # v5: de-saturated icons settled + clock digits back to white
                                   # v6: home reordered to 13 items + folder dot badge;
                                   #     header gained a folder flag byte (folder change invalidates)
