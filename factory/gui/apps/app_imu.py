# IMU app: live readings from the on-board QMI8658A six-axis inertial sensor
# (accelerometer + gyroscope + temperature).
#
# Hardware: I2C3, SCL=GPIO44 / SDA=GPIO45, address 0x6A or 0x6B (chosen by AD0;
# measured 0x6B on this board -- the 0x38 device on the same bus is something
# else, do not mistake it for the IMU). Accel ±4g / Gyro ±512dps; in 6DOF mode
# the ODR is set by the gyroscope = 896.8Hz (not 1000Hz, see datasheet Table 22).
# Temperature is the sensor die temperature (TEMP_H + TEMP_L/256), so 40~50℃ at
# room temperature is normal -- it is not ambient temperature. Report it as die
# temperature, otherwise users read the number as the room being 45℃.
#
# Samples come through the **shadow register lock** rather than a plain read: the
# registers keep being refreshed while we read them, so a high byte can belong to
# the next sample, and the error is exactly 256 LSB = 4 dps / 15.6 mg.
# Measured on board: a bare read gave gyro Z peak-to-peak 7.97 dps with adjacent
# deltas of exactly 256 LSB; with the lock enabled (datasheet section 13) that
# fell to 1.13 dps / max|Δ|=47 LSB and the sampling rate stayed at 886Hz.
#
# Screen layout: a readout row on top (A/G/T) + six curves on a chart (one scale
# column on each side) + a status row at the bottom, filling the 800x424 content
# area vertically. Each frame the chart pushes one point and SHIFT scrolls left,
# at 50Hz.
#
# Why the readout row uses one label per column instead of one label holding a
# padded line: space padding cannot align columns in a proportional font; only a
# fixed width per column keeps them in place. Each column is also tinted like the
# curve it belongs to, so the colours act as the legend (the chart draws none).
#
# Alignment and glyph stability (the three conditions that keep the readout from
# twitching -- drop one and it shakes):
#   1) Left align: right align pins the right edge, so adding or dropping a sign
#      or a digit shifts the whole string sideways; left align fixes the start
#      column and any change only extends to the right.
#   2) Fixed sign: {:+.Nf} always carries a +/- so the minus never blinks in/out.
#   3) Monospace font: the built-in montserrat is proportional, `1` is narrower
#      than `8`, so the same digit count is not the same width. The value columns
#      therefore use a pre-baked DejaVu Sans Mono bitmap font (tools/gen_font.py
#      mono16): every character takes one advance, and after _pad widens the text
#      to a fixed length the glyph extent stays constant.
#
# The value font is a bin bitmap font (font_load reads it into memory once, a
# redraw is just a blit), not tiny_ttf: under RENDER_MODE.FULL the latter
# re-rasterises on every frame, which no amount of caching can hide.
#
# Exit: the Home button in the status bar or the physical CANCEL key, both
# handled by the launcher -- this app does not intercept keys. A missing IMU at
# construction time raises AppUnavailable, the launcher shows its notice panel,
# the same experience as a missing camera.

import os
import time
import ustruct
import math

import lvgl as lv
from machine import FPIOA, I2C

from ui import beep
from ui import paging
from core import i18n
from core import paths
from core.errors import AppUnavailable

IMU_UNAVAILABLE = ("IMU (QMI8658A) not detected.\n"
                   "The IMU did not respond on I2C3.\n"
                   "Restart the board and try again.")

# ---------------- QMI8658A registers ----------------
QMI8658_WHO_AM_I = 0x00     # expect 0x05
QMI8658_CTRL1 = 0x02        # accel control (address auto-increment / byte order)
QMI8658_CTRL2 = 0x03        # accel range + ODR
QMI8658_CTRL3 = 0x04        # gyro range + ODR
QMI8658_CTRL5 = 0x06        # low-pass filter
QMI8658_CTRL7 = 0x08        # enable bits
QMI8658_CTRL8 = 0x09        # handshake / interrupt mapping
QMI8658_RST_RESULT = 0x4D   # soft reset result (0x80 = done)
QMI8658_RESET = 0x60        # soft reset register (write 0xB0)
QMI8658_TEMP_L = 0x33
QMI8658_AX_L = 0x35
QMI8658_GX_L = 0x3B
QMI8658_STATUSINT = 0x2D    # bit0=Avail, bit1=Locked, bit7=Ctrl9 CmdDone
QMI8658_CTRL9 = 0x0A
QMI8658_CAL1_L = 0x0B

CTRL9_CMD_AHB_CLOCK_GATING = 0x12
STATUSINT_AVAIL = 0x01
STATUSINT_LOCKED = 0x02
STATUSINT_CMD_DONE = 0x80

CTRL7_ACC_ENABLE = 0x01
CTRL7_GYR_ENABLE = 0x02
CTRL7_SYNC_SMPL = 0x80      # bit7: enable the shadow register lock
CTRL1_ADDR_AI = 0x40

# ---------------- wiring / bus ----------------
I2C_BUS = 3
I2C_SCL = 44
I2C_SDA = 45
I2C_FREQ = 400000
IMU_ADDRS = (0x6A, 0x6B)

# Range conversion (datasheet Table 7/8, 16-bit output):
#   accel ±4g -> 8192 LSB/g, 1 LSB = 122µg (±2g would be 16384 / 61µg)
#   gyro  ±512dps -> 64 LSB/dps, 1 LSB = 15.6mdps
ACC_SCALE = 8192.0
GYR_SCALE = 64.0

# Gyro power-up ready time = 150ms + 3/ODR ≈ 153ms (datasheet Table 8); 200ms
# leaves margin.
WARMUP_MS = 200
# Lock polling limit: at ODR 896.8Hz a new sample lands every 1.1ms, so a few I2C
# transactions always hit one.
LOCK_POLL_TRIES = 200

# ---------------- refresh ----------------
# Sample / push at 50Hz (20ms). Each frame the chart pushes one point and redraws
# the whole area: measured on board, 7 series × 150 points costs ≈13.5ms per
# frame including the 1.7ms I2C read. Only 6 series remain, so the cost is lower
# and still inside the 20ms budget.
# The point count comes from the time window: 3s × 50Hz = 150 points. A longer
# window is smoother but costs redraw time (measured: N=200 takes 17ms, N=300
# exceeds 20ms and drops frames), so 150 it is.
SAMPLE_MS = 20              # 50 Hz: sample + push one chart point + refresh row
CHART_POINTS = 150          # curve window points (= 3s @ 50Hz)
FAIL_LIMIT = 100            # 100 read failures in a row (~2s at 50Hz) = sensor gone

# ---------------- gyro zero-bias calibration ----------------
# The factory gyro bias tolerance is ±10 dps (datasheet Table 8, board level), an
# order of magnitude above the noise (σ≈0.19~0.48 dps). Measured on this board:
# X −2.2 / Y +7.7 / Z +0.1 dps -- the Y axis looks like it is "spinning while
# standing still", and without removing it no angular-rate or
# integrate-to-angle use is viable.
#
# The result lives in /data (the 6G data partition), not /sdcard (GUI resources
# only): reflashing firmware or UI leaves it alone, only swapping the card loses
# it. Text rather than binary: small, readable, no parsing risk.
# The temperature is stored too: the gyro drifts with temperature (TCO X/Y
# ±0.1 dps/℃, datasheet Table 8), so a future "temperature moved a lot, consider
# recalibrating" hint has the evidence it needs.
CAL_DIR = "/data/imu"
CAL_PATH = "/data/imu/gyro_zero.txt"
CAL_MAGIC = "IMUZ1"
CAL_COUNTDOWN_MS = 3000     # after confirm: let the board settle (hand away, desk stops)
CAL_SAMPLE_MS = 2000        # acquisition length
CAL_PER_FRAME = 4           # extra reads per frame while sampling: 400 samples -> bias σ≈0.01 dps
# Stillness criterion: upper bound on the per-axis gyro standard deviation during
# acquisition. Measured on board: at rest σ=0.14~0.22 dps, while moving σ is a few
# dps (the bias itself is constant and contributes no σ), so 5 dps separates the
# two cleanly. Without this a wobbling board still "passed calibration" and only
# stored a bad bias (measured, and it happened).
CAL_MAX_SD_DPS = 5.0

# Calibration dialog states
ST_NORMAL = 0               # normal display
ST_PROMPT = 1               # ask whether to calibrate (never calibrated / gyro picked in menu)
ST_COUNTDOWN = 2            # counting down until still
ST_SAMPLING = 3             # sampling
ST_MENU = 4                 # OK in the normal state: menu with the gyro / accel choice

# Accelerometer calibration states (>= ST_ACC_PROMPT is dispatched to _acc_tick).
# Kept after ST_MENU and before the other ACC_ constants: step tests
# "state >= ST_ACC_PROMPT" to pick the acceleration branch.
ST_ACC_PROMPT = 5           # ask whether to run the six-face calibration
ST_ACC_POSE = 6             # show the current face pose, wait for OK / Sample
ST_ACC_COUNTDOWN = 7        # short wait after the press (hand away, wobble gone)
ST_ACC_SAMPLE = 8           # sampling this face (includes the still check)
ST_ACC_RESULT = 9           # residual too large / bad pairing: Retry all / Keep anyway
ST_ACC_DONE = 10            # saved notice, closes itself after a pause

# ---------------- accelerometer six-face calibration ----------------
# The accelerometer cannot be zeroed from a single pose the way the gyro can: at
# rest it always outputs gravity, and one pose cannot separate bias / sensitivity /
# mounting tilt. The standard approach averages one pose per face of the six faces
# and pairs them: b=(a+ + a-)/2, s=|a+ - a-|/2. Common-mode errors such as axis
# mismatch and a non-level desk flip sign inside the pair average, so one
# measurement per face is enough (12 positions only solves production fixture
# error, which hand-flipping does not need).
# Axis directions come from board measurements (six-face calibration log
# 2026-10-29): lying flat with the screen up the sensor -Z points up (acc z ≈
# -1.009g), but X/Y are 90° away from what the screen suggests: standing on the
# left edge gives +Y≈+1g and standing on the top edge gives -X≈+1g -- so sensor +X
# points at the bottom edge of the screen and +Y points at the left edge.
ACC_CAL_DIR = "/data/imu"
ACC_CAL_PATH = "/data/imu/acc_cal.txt"
ACC_CAL_MAGIC = "ACCC1"
ACC_POSE_PROMPTS = (
    "Face DOWN on table",    # 1/6  screen down: +Z along gravity
    "Face UP on table",      # 2/6  screen up: -Z along gravity
    "LEFT edge down",        # 3/6  left edge down: +Y along gravity
    "RIGHT edge down",       # 4/6  right edge down: -Y along gravity
    "TOP edge down",         # 5/6  top edge down: -X along gravity
    "BOTTOM edge down",      # 6/6  bottom edge down: +X along gravity
    "Face UP on table",      # 7    verification pose (same as 2/6)
)


def _pose_text(idx):
    """Pose prompt in the UI language; ACC_POSE_PROMPTS itself stays English for
    the calibration log."""
    return i18n.t("imu.pose.%d" % idx, ACC_POSE_PROMPTS[idx])
# (i+, i-, axis): the Z / Y / X pairs. The i+ face's dominant reading points
# along gravity and the i- face points against it, chosen from the measured axes
# above (left/right goes with Y, bottom/top goes with X). The ± order is
# symmetric in the formula; the positive face comes first only for readability.
ACC_PAIRS = ((0, 1, 2), (2, 3, 1), (5, 4, 0))
# Expected gravity axis and sign per face: (axis, sign). Used to check on the spot
# that the board really is where the prompt says -- if a wrong face (asked for
# LEFT, placed TOP) is not caught here, the run only fails at the very end with
# "scale out of range" in the pairing stage and the user has no idea which face
# was wrong. Index 6 = verification re-test (same as UP).
ACC_FACE_EXPECT = ((2, 1), (2, -1), (1, 1), (1, -1), (0, -1), (0, 1), (2, -1))
# Lower bound on the expected dominant reading: 0.85g ≈ 32° of allowed tilt
# between face and gravity. Hand placement on an edge is usually off by 10~15°
# (measured: all six faces were above 0.96g), while a wrong face reads ≈0 on that
# axis, so the criterion separates them widely.
ACC_FACE_MIN_DOM_G = 0.85
ACC_FACE_SAMPLE_MS = 2000   # acquisition length per face
# Wait after pressing Sample on a face: the board takes a while to stop once
# flipped (desk/hand vibration decays). Measured: 1.5s was too short and sampling
# started with residual wobble, which then failed that face on sd or stillness.
ACC_FACE_COUNTDOWN_MS = 5000  # wait per face after Sample for hand off / wobble gone
ACC_GYRO_STILL_DPS = 1.0    # still check: calibrated gyro magnitude cap, >10% offending frames rejects
ACC_MAX_SD_G = 0.015        # still check: per-axis accel sd cap (5x the σ≈2~3mg noise)
ACC_MIN_SAMPLES = 60        # too few valid samples (too many read failures) rejects; 2s normally gives ~100
ACC_VERIFY_MAG_TOL = 0.005  # cap on the |a| deviation from 1g after correction
# The horizontal-axis cap is deliberately loose (50mg≈3°): the horizontal residual
# on the verification face (screen up) comes mostly from **desk tilt**, and the
# six-face method only fixes bias/scale, not placement tilt (that needs a full
# rotation matrix). Measured on board: a normal desk already shows ~25mg (1.5°).
# This criterion only catches "the board is clearly not level"; it is not a
# calibration-quality measure -- quality is the magnitude deviation above.
ACC_VERIFY_H_TOL = 0.050    # cap on the mean of the two horizontal axes after correction
# After entering a face prompt, Sample/OK are ignored for this long: a click left
# over from the successful previous face, or flipping while the finger is still on
# OK, would make this face "start sampling the instant it appears". This is
# debounce, not the settling wait (settling is ACC_FACE_COUNTDOWN_MS after the
# press).
ACC_POSE_DEBOUNCE_MS = 500
ACC_B_TOL = 0.3             # load check: sane bias cap (3x the datasheet board-level ±100mg)
ACC_S_TOL = 0.1             # load check: cap on |s-1| (±6% tolerance with margin)
ACC_DONE_MS = 1500          # how long the saved notice stays, then the dialog closes itself
# Once the "not calibrated" auto dialog is Skipped it stays quiet for the rest of
# this power cycle (a power cycle brings it back). Module level: leaving and
# re-entering the app does not nag again, but a process restart (re-power, GUI
# restart) resets it.
_ACC_PROMPTED = [False]

# Horizontal spacing between dialog buttons (centre to centre). Buttons are
# distributed symmetrically by how many are visible, see _set_overlay.
ACC_BTN_SPACING = 182

# ---------------- layout ----------------
# The launcher starts this app with immersive=True, so the content area is the
# whole 800x480 screen (no status bar). All three pages use that screen:
#   page 1 dashboard: main area 0..438 + heading tape 438..480 (see DASH_*)
#   pages 2/3 curves: readout row 6..24 -> chart 30..436 (676×406) -> footer hint
#               444..462. Horizontally: left scale 6..58 -> chart 62..738 ->
#               right scale 742..794.
READOUT_Y = 6
AXIS_L_X = 6                 # left vertical scale column
AXIS_R_X = 742               # right vertical scale column
AXIS_W = 52
CHART_X = 62
CHART_TOP = 30
CHART_W = 676
CHART_H = 406                # 34px taller than the old 372 now the screen is full
FOOT_Y = 444

# Readout row: the whole row uses the mono font only (letters and "|" included,
# see gen_font.py mono16). Prefix, values and separators must share a font to
# share a baseline -- when the prefix was montserrat and the values mono, the two
# baselines differed by 4px at the bottom (measured on board pixels).
# Column positions are counted in character cells: mono-16 is 10px per character,
# so 76 characters = 760px, x=20..780 filling the content area (the old layout
# only reached x≈606 and left a large empty block on the right).
RD_A_X = 20                   # "A(g)"      cell 0
RD_AX = (80, 150, 220)        # Ax/Ay/Az    cells 6/13/20 (6-char values, 1 space apart)
RD_SEP1_X = 300               # "|"         cell 28
RD_G_X = 330                  # "G(dps)"    cell 31
RD_GX = (410, 480, 550)       # Gx/Gy/Gz    cells 39/46/53
RD_SEP2_X = 630               # "|"         cell 61
RD_T_X = 660                  # "T(C)"      cell 64
RD_TEMP_X = 720               # temp        cell 70 (row ends at 780)
RD_VAL_W = 60

# Curve family colours: warm for acceleration, cool for angular rate.
# Why split by colour temperature: the two families are told apart at a glance by
# warm versus cool, which resists confusion better than the earlier "shades of
# blue vs shades of green". The three axes inside a family must also spread in hue
# (red/orange/yellow, cyan/blue/purple) -- the old three blue tones were too close
# and the three curves of one family smeared together (user feedback).
COL_ACC = (0xFF5252, 0xFF9800, 0xFFC93C)   # accel X/Y/Z: warm (red/orange/yellow)
COL_GYR = (0x00D9FF, 0x3B82F6, 0xA78BFA)   # gyro X/Y/Z: cool (cyan/blue/purple)
# Temperature is a scalar and belongs to neither family, so it uses a neutral
# light grey: the earlier amber collided with the yellow of the warm family.
COL_TEMP = 0xE5E7EB
COL_ACC_HEAD = 0xB04A3A                     # prefix A: warm, darkened
COL_GYR_HEAD = 0x2F7FA8                     # prefix G: cool, darkened
COL_TEMP_HEAD = 0x8A93A0                    # prefix T: neutral, darkened
COL_INFO = 0x6B7484                         # footer status / hints: secondary text

# The chart Y axis is a shared integer range [-100,100]; each of the two
# dimensions is normalised by its own full scale and drawn on top (one pixel
# space). The left scale labels the acceleration full scale (g) and the right one
# the angular-rate full scale (dps) -- because the axis is shared, this app draws
# both scale texts itself instead of using SECONDARY_Y (no point betting on
# whether this binding supports it).
# The readout row shows real values (g / dps / ℃); the chart only shows shape.
CHART_Y_MIN = -100
CHART_Y_MAX = 100

# Vertical auto-range: step the full scale up and down by the peak of the data
# **still visible in the window**, so the curves fill the grid as much as
# possible. Up is immediate (looking small beats clipping), down waits
# SCALE_HOLD_MS and then jumps straight to the target step -- otherwise the axis
# shrinks the moment a peak passes and oscillates every second.
# Steps are "round" values: 0.25g increments for acceleration, doubling for
# angular rate.
SCALE_HEADROOM = 1.08             # keep 8% headroom above the peak so curves clear the border
PEAK_SCAN_MS = 100                # window peak scan interval (900 abs over the window, throttled to 5 frames)
SCALE_HOLD_MS = 1200              # down-stepping requires staying under for this long
ACC_FULL_STEPS = (0.25, 0.50, 0.75, 1.00, 1.25,
                  1.50, 2.00, 2.50, 3.00, 4.00)   # last = range limit ±4g
GYR_FULL_STEPS = (16.0, 32.0, 64.0, 128.0, 256.0, 512.0)  # last = ±512dps
ACC_FULL_DEFAULT = 1.25           # at rest flat ≈1g, 1.25g puts the curve at ~80%
GYR_FULL_DEFAULT = 64.0

# ---------------- three pages ----------------
# 0 = cockpit dashboard (default), 1 = raw data curves, 2 = attitude curves. Swipe
# left for the next page, right for the previous one (swiping left on the last
# page wraps to page 1, so one hand can get back).
PAGE_DASH = 0
PAGE_RAW = 1
PAGE_ATT = 2
PAGE_COUNT = 3
# Swipe paging parameters.
# Down/Up events cannot drive the gesture: FT5316 emits a "fake Up + another Down"
# while dragging, and resetting the origin on every Down means the displacement
# never accumulates (measured: the 90px threshold is essentially unreachable).
# Instead the finger-up is detected by "quiet timeout": a gesture ends only after
# no new contact for SWIPE_RELEASE_MS.
# The displacement decision (page or snap back) lives in paging.DragPager: when the
# finger stops mid-drag the page stays half way and the choice happens on release --
# one shared implementation with the audio app, so the feel matches.
SWIPE_RELEASE_MS = 60       # no contact for this long counts as finger-up (bridges the fake Up)
# Dashboard refresh interval (25Hz; attitude changes slowly, enough and saves CPU)
DASH_MS = 40

# ---------------- attitude estimation (Mahony AHRS) ----------------
# Why quaternions instead of computing Euler angles straight from acceleration:
# acceleration only gives the gravity direction (roll/pitch), never a heading, and
# body vibration makes the readings jump. Mahony integrates the gyro and corrects
# with acceleration, giving a smooth attitude plus a heading (the heading comes
# from gyro integration and drifts slowly, which is inherent without a
# magnetometer).
AHRS_KP = 2.0               # acceleration correction gain (converges in about 1s at this value)
AHRS_GYRO_LIMIT = 300.0     # cap on the absolute angular rate so a bad sample cannot throw the quaternion
# Online learning of the gyro residual bias (temperature drift compensation):
# the stored bias is the value at "the temperature during calibration", and it
# drifts as the die temperature moves (TCO ±0.1dps/℃). Measured on this board:
# calibrated at 50℃, running at 38℃ -> residual close to 1dps. On a 6-axis part
# without a magnetometer the heading only comes from gyro integration, so that
# residual becomes a steady ~1°/s drift (the heading tape turns by itself).
# While still (all three axes small) the residual is learned and subtracted; a
# real rotation is far above the threshold and is never mistaken for bias.
BIAS_STILL_DPS = 1.5        # still only when all three axes are below this
BIAS_STILL_MS = 400         # must stay that way this long to be really still (filters pose changes)
BIAS_ALPHA = 0.02           # blend per frame (τ≈1s at 50Hz)
# Display frame alignment: instrument axes X = nose (out of the screen), Y = right
# wing (screen right), Z = belly (screen down). Measured on this board with the
# six-face calibration: "BOTTOM edge down" reads accel ≈(+1,0,0)g, i.e. +X points
# at the bottom edge of the screen. R_BD follows from that, and its quaternion
# conjugate is q_corr (nine attitude cases checked on the PC side, error 0.00°).
# Usage: q_disp = q ⊗ q_corr (right multiply), then decompose roll/pitch/yaw as ZYX.
Q_CORR = (0.0, -0.70710678, 0.0, -0.70710678)
ATT_FULL_STEPS = (10.0, 20.0, 30.0, 45.0, 60.0, 90.0, 180.0)  # attitude angle steps
ATT_FULL_DEFAULT = 45.0

# ---------------- dashboard layout / colours ----------------
# The dashboard is **immersive full screen** (800x480, covering the status bar
# too), so every coordinate here is a screen coordinate, not a content-area one
# (that is only the 800x424 below the status bar).
# Vertically: attitude indicator main area 0..438 -> heading tape 438..480. No
# footer status row is drawn (the user asked for a pure cockpit, no hint text).
DASH_MAIN_H = 438           # attitude indicator main area height
DASH_CY = 219               # attitude indicator centre y (main area midline)
DASH_CX = 400
DASH_PPD = 2.8              # pixels per degree of pitch
DASH_LINE_T = 700           # half length of the horizon/ground line: covers the screen when rotated
DASH_GROUND_W = 1200        # width of the thick ground fill (600 half width covers half a screen)
DASH_ARC_R = 150            # roll arc radius
DASH_ARC_CY = -8            # arc centre slightly above the top edge (peak on the edge, bottom y≈142)
DASH_LADDER_TOP = 152       # the pitch ladder never goes above this (it would overlap the roll arc)
DASH_TAPE_Y = DASH_MAIN_H   # heading tape top edge
DASH_TAPE_H = 42
DASH_TAPE_PPD = 4.0         # pixels per degree on the heading tape
# Top-left attitude readout: the prefix (R/P/Y) is left aligned and the value has
# its own fixed-width box that is **right aligned**.
# Why two labels: one label has one alignment only, and "label left, number right"
# needs two boxes; the value width varies (+0.9 / -12.3 / +123.4) and right
# alignment keeps the last digit and the decimal point in place, so scanning does
# not shift sideways.
DASH_TXT_X = 10             # prefix left edge
DASH_VAL_X = 26             # value box left edge (1-char prefix = 10px + 6px gap)
DASH_VAL_W = 74             # value box width: the widest "-180.0" is 6 chars = 60px, with margin

COL_SKY = 0x1A6FA8
COL_GROUND = 0x7B4F1E
COL_HORIZON = 0xFFFFFF
# Pitch ladder colours: climbing (pitch>0) rungs are white and diving rungs pale
# amber, so the two directions read apart at a glance.
COL_LADDER_POS = 0xFFFFFF
COL_LADDER_NEG = 0xD4B483
COL_AIRCRAFT = 0xFFD700
COL_ARC = 0xCFD8E3
COL_TAPE_BG = 0x0F1A2E
COL_TAPE_TICK = 0x8899AA
COL_ATT = (0x60A5FA, 0x34D399, 0xFBBF24)   # attitude curves: roll blue / pitch green / yaw amber
COL_ATT_HEAD = (0x3B6EA8, 0x1E7A58, 0x9C7A1A)   # page 3 prefixes: same hues, darkened

# Initial footer text. _refresh_foot overwrites it at runtime; this is only the
# build-time placeholder, in the current language so the first frame matches.
def _foot_initial():
    return i18n.t("imu.footer").format(50, "", 1, PAGE_COUNT,
                                       i18n.t("imu.swipe_left"))

# Value font (baked by tools/gen_font.py mono16, deployed with assets/).
# Full ASCII: the readout prefixes A(g)/G(dps)/T(C) and the "|" separators use it
# too, because only one font for the whole row gives one baseline (mixing in
# montserrat is 4px off, measured on board pixels).
MONO_FONT = "mono_dejavu_16.bin"

# Dialog fonts: the built-in set only has montserrat_14/16, and 14px is too small
# on an 800x480 screen (the user reported it was unreadable). These are pre-baked
# bin bitmap fonts (font_load reads them in once, a redraw is just a blit):
#   DIALOG_TITLE_FONT 28px Nunito Bold -- the same face as the Cancel button of the
#                     "app unavailable" panel and the on-screen keyboard, so the UI
#                     stays visually consistent.
#   DIALOG_BODY_FONT  22px Nunito Bold -- the compromise: readable while four lines
#                     of body text still fit the dialog.
# Both cover printable ASCII only (0x20-0x7E), so dialog text must stay English;
# Chinese would render as empty boxes.
DIALOG_TITLE_FONT = "kb_nunito_bold_28.bin"
DIALOG_BODY_FONT = "ui_nunito_bold_22.bin"
# Fixed-width fields (right padded with spaces): acc 3 decimals, gyr 1, temp 2.
# The digit counts follow the device resolution (datasheet + board measurements),
# not taste:
#   accel 1 LSB=122µg (±4g), noise density 150µg/√Hz, σ≈2.0~3.0mg (≈16~25 LSB).
#   4 decimals (0.1mg) is 1/20 of the noise and the last two digits are pure
#   jitter; 3 decimals (1mg) is still far finer than the noise, so it is steady
#   without losing resolution (still 8x one LSB).
#   gyro 1 LSB=15.6mdps, noise σ≈0.17~0.48dps (≈11~31 LSB). 2 decimals (0.01dps)
#   is equally over-fine; 1 decimal (0.1dps) matches the noise magnitude.
#   temp 2 decimals (0.01℃) is reasonable, but the die refreshes it at only 8Hz.
# Field width 6 fits the worst cases: acc ±4.000, gyr ±512.0, temp ±85.00.
ACC_CHARS = 6               # "+4.000"  / "-1.023"
GYR_CHARS = 6               # "+7.9  "  / "-512.0"
TEMP_CHARS = 6              # "+47.68"

# When the main loop is idle LVGL refreshes on a 30ms default period, which clamps
# 50Hz down to 33Hz; only calling refr_now from step shows the real 50Hz (it
# returns immediately with no dirty area -- measured 0ms, so no CPU wasted).
_REFR_NOW = getattr(lv, "refr_now", None)
_DISP = [None]


def _font(*names):
    """Return the first available built-in font among the candidate names.

    Bindings name their fonts differently, so callers pass several.
    """
    for name in names:
        font = getattr(lv, name, None)
        if font:
            return font
    return None


_VALUE_FONT = None
_VALUE_FONT_TRIED = False


def _value_font():
    """Font for the numeric columns: prefer the pre-baked monospace bin font.

    Monospace is what stops the jitter: the built-in montserrat is proportional,
    `1` is narrower than `8`, so equal digit counts are not equal widths. With a
    monospace font every digit and sign takes one advance, and together with the
    fixed sign field every set_text has the same glyph extent, so the readout never
    twitches sideways.
    lv.font_load needs the LVGL FS drive prefix `A:` in the path; without it the
    call silently returns None.
    If loading fails, fall back to montserrat (functionality is unaffected, the
    readout just keeps a slight jitter).
    """
    global _VALUE_FONT, _VALUE_FONT_TRIED
    if _VALUE_FONT_TRIED:
        return _VALUE_FONT
    _VALUE_FONT_TRIED = True
    load = getattr(lv, "font_load", None)
    if load is not None:
        # The path needs the A: prefix; without it the call silently returns None.
        path = "A:" + paths.asset_path("fonts", MONO_FONT)
        try:
            font = load(path)
            if font is not None:
                _VALUE_FONT = font
                print("[IMU] mono font ok: {}".format(path))
                return _VALUE_FONT
            print("[IMU] mono font load returned None: {}".format(path))
        except BaseException as error:
            print("[IMU] mono font load failed: {} | {}".format(path, error))
    _VALUE_FONT = _font("font_montserrat_16", "font_montserrat_14")
    print("[IMU] mono font missing, fallback to montserrat")
    return _VALUE_FONT


def _pad(text, width):
    """Right-pad with spaces to a fixed length.

    MicroPython's str does not always have ljust, so it is done by hand here. A
    fixed length plus left alignment keeps the text extent of every value widget
    constant, so nothing shifts when the digit count changes.
    """
    n = width - len(text)
    if n > 0:
        return text + " " * n
    return text


# ---------------- quaternion / attitude math ----------------
# Everything is a tuple (w,x,y,z) to avoid MicroPython object overhead. At 50Hz one
# integration plus one normalisation per frame is measurably negligible.
_Q_IDENT = (1.0, 0.0, 0.0, 0.0)
_RAD2DEG = 57.29577951308232
_DEG2RAD = 0.017453292519943295


def _qmul(a, b):
    aw, ax, ay, az = a
    bw, bx, by, bz = b
    return (aw * bw - ax * bx - ay * by - az * bz,
            aw * bx + ax * bw + ay * bz - az * by,
            aw * by - ax * bz + ay * bw + az * bx,
            aw * bz + ax * by - ay * bx + az * bw)


def _qnorm(q):
    w, x, y, z = q
    n = (w * w + x * x + y * y + z * z) ** 0.5
    if n < 1e-6:
        return _Q_IDENT
    inv = 1.0 / n
    return (w * inv, x * inv, y * inv, z * inv)


def _q_gravity(q):
    """Gravity direction (body frame) implied by the current quaternion.

    This expands the third column of the rotation matrix, R·[0,0,1]: Mahony
    aligns the measured acceleration direction with it.
    """
    w, x, y, z = q
    return (2.0 * (x * z - w * y), 2.0 * (w * x + y * z),
            w * w - x * x - y * y + z * z)


def _q_euler(q):
    """Quaternion -> (roll, pitch, yaw) in radians, ZYX intrinsic."""
    w, x, y, z = q
    roll = math.atan2(2.0 * (w * x + y * z), 1.0 - 2.0 * (x * x + y * y))
    sinp = 2.0 * (w * y - z * x)
    if sinp >= 1.0:
        pitch = 1.5707963267948966
    elif sinp <= -1.0:
        pitch = -1.5707963267948966
    else:
        pitch = math.asin(sinp)
    yaw = math.atan2(2.0 * (w * z + x * y), 1.0 - 2.0 * (y * y + z * z))
    return (roll, pitch, yaw)


def _ahrs_update(q, gx, gy, gz, ax, ay, az, dt, kp=AHRS_KP):
    """One Mahony attitude update, returns the new unit quaternion.

    gx/gy/gz are in **dps** (the raw unit this driver reads them in), ax/ay/az are
    in g. Quaternion integration needs the rate in **rad/s**, so the conversion
    happens on entry -- skipping it makes the integration 57.3x too fast (measured
    on board: the heading drifted to 149° in 2.5s at rest).

    When the acceleration magnitude is outside 0.5~2g (free fall or strong linear
    acceleration) the correction is skipped and only the gyro is integrated --
    otherwise one impact would drag the attitude with it.
    """
    gx *= _DEG2RAD
    gy *= _DEG2RAD
    gz *= _DEG2RAD
    n2 = ax * ax + ay * ay + az * az
    if 0.25 < n2 < 4.0:
        inv = 1.0 / n2 ** 0.5
        nx, ny, nz = ax * inv, ay * inv, az * inv
        vx, vy, vz = _q_gravity(q)
        ex = ny * vz - nz * vy
        ey = nz * vx - nx * vz
        ez = nx * vy - ny * vx
        gx += ex * kp
        gy += ey * kp
        gz += ez * kp
    w, x, y, z = q
    h = dt * 0.5
    q = (w + h * (-x * gx - y * gy - z * gz),
         x + h * (w * gx + y * gz - z * gy),
         y + h * (w * gy - x * gz + z * gx),
         z + h * (w * gz + x * gy - y * gx))
    return _qnorm(q)


def _mk_line(parent, color, width, npts=2):
    """Create an lv.line (drawn on the screen directly, an order faster than canvas).

    Measured: a full-width solid canvas fill takes 73ms, while a thick screen line
    covering half the screen takes only 3.6~12ms, so the dashboard is drawn
    entirely with lines and never with canvas.
    """
    ln = lv.line(parent)
    ln.set_points([{"x": 0, "y": 0}] * npts, npts)
    ln.set_style_line_width(width, 0)
    ln.set_style_line_color(lv.color_hex(color), 0)
    ln.set_style_line_opa(lv.OPA.COVER, 0)
    try:
        ln.set_style_line_rounded(False, 0)
    except BaseException:
        pass
    return ln


def _set_pts(ln, pts):
    ln.set_points(pts, len(pts))



# Cache of the bin bitmap fonts used by dialogs: name -> font (False = it failed
# before).
_DIALOG_FONTS = {}


def _dialog_font(name, fallback=("font_montserrat_16", "font_montserrat_14")):
    """Load a bin font from assets/fonts/ by file name, else fall back to montserrat.

    Why not scale the built-in font with tiny_ttf: this firmware has no larger
    compile-time font, and tiny_ttf re-rasterises on every frame under
    RENDER_MODE.FULL (measured 485ms/frame at 32px). A bin bitmap font is read into
    memory once and a redraw is just a blit.
    """
    if name in _DIALOG_FONTS:
        return _DIALOG_FONTS[name] or None
    font = None
    load = getattr(lv, "font_load", None)
    if load is not None:
        path = "A:" + paths.asset_path("fonts", name)
        try:
            font = load(path)
            if font is None:
                print("[IMU] dialog font returned None: {}".format(path))
        except BaseException as error:
            print("[IMU] dialog font failed: {} | {}".format(path, error))
    if font is None:
        font = _font(*fallback)
        print("[IMU] dialog font fallback to montserrat for {}".format(name))
    _DIALOG_FONTS[name] = font if font is not None else False
    return font


# ---------------- bias file I/O ----------------
def _ensure_dir(path):
    """Create directories level by level (CanMV's os module has no os.makedirs)."""
    current = ""
    for part in path.strip("/").split("/"):
        current += "/" + part
        try:
            os.mkdir(current)
        except OSError:
            pass


def load_gyro_zero():
    """Read the stored gyro bias. Returns (gx, gy, gz, temp) or None (not calibrated).

    A missing, corrupt or malformed file is always treated as "not calibrated":
    better to ask the user once more than to subtract bad data and bias the readout
    without anyone noticing.
    """
    try:
        f = open(CAL_PATH)
        line = f.readline()
        f.close()
    except BaseException:
        return None
    parts = line.split()
    if len(parts) < 5 or parts[0] != CAL_MAGIC:
        print("[IMU] zero file invalid: {!r}".format(line))
        return None
    try:
        return (int(parts[1]) / 1000.0, int(parts[2]) / 1000.0,
                int(parts[3]) / 1000.0, int(parts[4]) / 100.0)
    except BaseException as error:
        print("[IMU] zero file parse failed: {}".format(error))
        return None


def save_gyro_zero(gx, gy, gz, temp):
    """Write the bias to disk.

    dps and ℃ are stored as scaled integers so text formatting and parsing cannot
    lose precision.
    """
    line = "{} {} {} {} {}\n".format(
        CAL_MAGIC, int(round(gx * 1000)), int(round(gy * 1000)),
        int(round(gz * 1000)), int(round(temp * 100)))
    try:
        _ensure_dir(CAL_DIR)
        f = open(CAL_PATH, "w")
        f.write(line)
        f.close()          # must close explicitly on board or the write may not land
        print("[IMU] zero saved: {}".format(line.strip()))
        return True
    except BaseException as error:
        print("[IMU] zero save failed: {}".format(error))
        return False


def clear_gyro_zero():
    """Delete the bias file (for debugging or forcing a recalibration)."""
    try:
        os.remove(CAL_PATH)
        return True
    except BaseException:
        return False


def load_acc_cal():
    """Read the stored accelerometer calibration. Returns (bias_g[3], scale[3], temp) or None.

    Same policy as the gyro file: missing, corrupt or implausible all mean "not
    calibrated" -- ask again rather than correct with bad data. An implausible
    value (bias beyond ±300mg or scale off by ±10%) usually means the board was
    shaken during calibration, so the bad file is renamed to .bad to stop it from
    failing over and over on every boot.
    """
    try:
        f = open(ACC_CAL_PATH)
        line = f.readline()
        f.close()
    except BaseException:
        return None
    parts = line.split()
    ok = False
    if len(parts) == 8 and parts[0] == ACC_CAL_MAGIC:
        try:
            b = [int(parts[1]) / 1000.0, int(parts[2]) / 1000.0,
                 int(parts[3]) / 1000.0]
            s = [int(parts[4]) / 1e6, int(parts[5]) / 1e6, int(parts[6]) / 1e6]
            ok = (max(abs(b[0]), abs(b[1]), abs(b[2])) < ACC_B_TOL and
                  max(abs(s[0] - 1.0), abs(s[1] - 1.0),
                      abs(s[2] - 1.0)) < ACC_S_TOL)
        except BaseException:
            ok = False
    if ok:
        return (b, s, _acc_cal_temp(line))
    print("[IMU] acc cal file invalid: {!r}".format(line))
    try:
        os.rename(ACC_CAL_PATH, ACC_CAL_PATH + ".bad")
        print("[IMU] renamed bad acc cal -> {}.bad".format(ACC_CAL_PATH))
    except BaseException:
        pass
    return None


def _acc_cal_temp(line):
    """Temperature field (centi-℃) from a calibration line. Validation already
    checked the format; this only catches parse errors."""
    try:
        return int(line.split()[7]) / 100.0
    except BaseException:
        return 0.0


# ---------------- calibration logs (/data/imu, overwritten per run) ----------------
# When calibration fails ("one face looks wrong", "the bias is off") the dialog
# alone cannot locate it, so every step is written out: the gyro log records the
# mean, per-axis standard deviation and verdict; the six-face log records each
# face's mean/sd/motion-violation count/verdict, every pair's bias/scale, and the
# verification residual.
# **Each calibration has its own log**: gyro_cal_log.txt and acc_cal_log.txt. The
# two flows are independent and trigger at different times, and overwriting a
# shared file would wipe the other one's records.
# A module-level handle is enough (only one calibration runs at a time):
# _cal_log_open(path, title) opens with truncation, _cal_log_line writes a line,
# _cal_log_close closes.
# Any IO failure only prints; it never disturbs the calibration flow.
CAL_LOG_DIR = CAL_DIR
GYRO_LOG_PATH = CAL_DIR + "/gyro_cal_log.txt"
ACC_LOG_PATH = ACC_CAL_DIR + "/acc_cal_log.txt"
_LOG_F = [None]


def _cal_log_open(path, title):
    _cal_log_close()
    try:
        _ensure_dir(CAL_LOG_DIR)
        f = open(path, "w")
        _LOG_F[0] = f
        try:
            f.write("==== %s ====\n" % title)
            f.flush()
        except BaseException as error:
            # Close the handle even on a write failure: a handle left open locks the
            # file across a soft reset and the next boot's open gets EPERM.
            _cal_log_close()
            print("[IMU] cal log write failed: {}".format(error))
    except BaseException as error:
        _LOG_F[0] = None
        print("[IMU] cal log open failed: {}".format(error))


def _cal_log_line(text):
    f = _LOG_F[0]
    if f is None:
        return
    try:
        f.write(text + "\n")
        f.flush()
    except BaseException as error:
        _cal_log_close()
        print("[IMU] cal log write failed: {}".format(error))


def _cal_log_close():
    f = _LOG_F[0]
    _LOG_F[0] = None
    if f is None:
        return
    try:
        f.close()
    except BaseException:
        pass


def save_acc_cal(bias, scale, temp):
    """Store bias/g and scale(≈1) as scaled integers (mg / ppm), matching the IMUZ1
    text style."""
    line = "{} {} {} {} {} {} {} {}\n".format(
        ACC_CAL_MAGIC,
        int(round(bias[0] * 1000)), int(round(bias[1] * 1000)),
        int(round(bias[2] * 1000)),
        int(round(scale[0] * 1e6)), int(round(scale[1] * 1e6)),
        int(round(scale[2] * 1e6)),
        int(round(temp * 100)))
    try:
        _ensure_dir(ACC_CAL_DIR)
        f = open(ACC_CAL_PATH, "w")
        f.write(line)
        f.close()          # must close explicitly on board or the write may not land
        print("[IMU] acc cal saved: {}".format(line.strip()))
        return True
    except BaseException as error:
        print("[IMU] acc cal save failed: {}".format(error))
        return False


def _push_frame():
    """Push the current frame to the screen immediately so the 20ms rhythm yields a
    steady 50Hz."""
    if _REFR_NOW is None:
        return
    if _DISP[0] is None:
        getter = getattr(lv, "disp_get_default", None)
        if getter is None:
            return
        try:
            _DISP[0] = getter()
        except BaseException:
            return
    try:
        _REFR_NOW(_DISP[0])
    except BaseException:
        pass


class QMI8658A(object):
    """Minimal QMI8658A driver: only the init and the three readings this app needs."""

    def __init__(self, i2c, addr):
        self.i2c = i2c
        self.addr = addr

    def _read(self, reg, n=1):
        return self.i2c.readfrom_mem(self.addr, reg, n)

    def _read_u8(self, reg):
        return self._read(reg, 1)[0]

    def _write(self, reg, value):
        self.i2c.writeto_mem(self.addr, reg, bytes([value]))

    def who_am_i(self):
        return self._read_u8(QMI8658_WHO_AM_I)

    def _ctrl9(self, command):
        """Run one CTRL9 command: write it -> wait for CmdDone -> write 0x00 to acknowledge."""
        self._write(QMI8658_CTRL9, command)
        for _ in range(50):
            if self._read_u8(QMI8658_STATUSINT) & STATUSINT_CMD_DONE:
                break
            time.sleep_ms(1)
        self._write(QMI8658_CTRL9, 0x00)

    def soft_reset(self):
        self._write(QMI8658_RESET, 0xB0)
        time.sleep_ms(20)
        # RST_RESULT reads 0x80 once the reset is done; wait up to ~500ms.
        for _ in range(50):
            if self._read_u8(QMI8658_RST_RESULT) == 0x80:
                break
            time.sleep_ms(10)

    def configure(self):
        # CTRL1: address auto-increment + little-endian output; CTRL2/3: range[6:4] +
        # ODR[3:0]; enable last in CTRL7.
        self._write(QMI8658_CTRL1, CTRL1_ADDR_AI)

        # Prerequisite for the lock (datasheet 13.2.1): internal AHB clock gating must
        # be off over I2C, otherwise two consecutive samples can still get mixed.
        # CAL1_L=0x01 plus CTRL9 command 0x12.
        self._write(QMI8658_CAL1_L, 0x01)
        self._ctrl9(CTRL9_CMD_AHB_CLOCK_GATING)

        self._write(QMI8658_CTRL2, (1 << 4) | 3)    # ±4g @ 896.8Hz (6DOF)
        self._write(QMI8658_CTRL3, (5 << 4) | 3)    # ±512dps @ 896.8Hz
        self._write(QMI8658_CTRL8, 0x80)            # STATUS_INT.bit7 as CTRL9 handshake
        self._write(QMI8658_CTRL5, 0x00)            # low-pass disabled
        # CTRL7=0x83: bit7 syncSmpl enables the lock + accel + gyro.
        self._write(QMI8658_CTRL7,
                    CTRL7_SYNC_SMPL | CTRL7_ACC_ENABLE | CTRL7_GYR_ENABLE)

        # Gyro power-up ready time = 150ms + 3/ODR ≈ 153ms (datasheet Table 8).
        # Under-warming returns invalid or half-updated data (measured: the
        # temperature showed a 48℃ peak-to-peak jump).
        time.sleep_ms(WARMUP_MS)

    def release(self):
        """Leave the lock mode and restore AHB clock gating (datasheet 13.4)."""
        try:
            self._write(QMI8658_CTRL7,
                        CTRL7_ACC_ENABLE | CTRL7_GYR_ENABLE)
            time.sleep_ms(20)
            self._write(QMI8658_CAL1_L, 0x00)
            self._ctrl9(CTRL9_CMD_AHB_CLOCK_GATING)
        except BaseException as error:
            print("[IMU] lock release: {}".format(error))

    def _read_locked(self):
        """Read one complete sample as described in datasheet section 13 (shadow register lock).

        Why the lock and not just a burst read: the registers are updated during
        the burst too, so a high byte can come from the next sample and the error is
        exactly 256 LSB = 4 dps / 15.6 mg. Measured on board: the burst read is 3x
        faster but the ±256 LSB outliers remain.

        Flow: read STATUSINT, Avail=1 triggers the lock; once Locked=1 burst out
        temp + acc + gyr; reading GZ_H(0x40) releases the lock automatically.
        Data_Lock_Delay is only 6µs at ODR=896.8Hz, far shorter than one I2C
        transaction, so no extra delay is needed.
        """
        i2c = self.i2c
        addr = self.addr
        for _ in range(LOCK_POLL_TRIES):
            st = i2c.readfrom_mem(addr, QMI8658_STATUSINT, 1)[0]
            if (st & (STATUSINT_AVAIL | STATUSINT_LOCKED)) == \
                    (STATUSINT_AVAIL | STATUSINT_LOCKED):
                break
        else:
            raise OSError("imu lock timeout")
        temp = ustruct.unpack("<h", i2c.readfrom_mem(addr, QMI8658_TEMP_L, 2))[0]
        ax, ay, az = ustruct.unpack("<hhh", i2c.readfrom_mem(addr, QMI8658_AX_L, 6))
        gx, gy, gz = ustruct.unpack("<hhh", i2c.readfrom_mem(addr, QMI8658_GX_L, 6))
        return (ax / ACC_SCALE, ay / ACC_SCALE, az / ACC_SCALE,
                gx / GYR_SCALE, gy / GYR_SCALE, gz / GYR_SCALE,
                temp / 256.0)

    def read_sample(self):
        """Read accel in g, gyro in dps and temp in ℃ in one go. Raises the
        underlying I2C error on failure."""
        return self._read_locked()


def _close_i2c(i2c):
    """Release I2C on a best-effort basis. This firmware's machine.I2C has no
    deinit() (measured AttributeError on board): call it when present, otherwise
    just drop the reference -- the bus is a resident peripheral that needs no
    explicit close.
    """
    deinit = getattr(i2c, "deinit", None)
    if deinit is None:
        return
    try:
        deinit()
    except BaseException:
        pass


def _open_imu():
    """Configure the pin mux, scan the addresses and initialise to ±4g / ±512dps.
    Raises AppUnavailable when the part is missing."""
    try:
        fpioa = FPIOA()
        scl_fn = getattr(FPIOA, "IIC3_SCL", None)
        sda_fn = getattr(FPIOA, "IIC3_SDA", None)
        if scl_fn is not None and sda_fn is not None:
            fpioa.set_function(I2C_SCL, scl_fn)
            fpioa.set_function(I2C_SDA, sda_fn)
        else:
            print("[IMU] FPIOA lacks the IIC3 constant; pin muxing skipped "
                  "(keeping the current mux)")
    except BaseException as error:
        # A failed mux is not fatal: some firmware already sets these pins up at
        # power-up.
        print("[IMU] fpioa setup skipped: {}".format(error))

    i2c = I2C(I2C_BUS, freq=I2C_FREQ)
    found = None
    try:
        devices = i2c.scan()
        print("[IMU] i2c{} scan: {}".format(I2C_BUS, [hex(d) for d in devices]))
        for addr in IMU_ADDRS:
            if addr in devices:
                found = addr
                break
    except BaseException as error:
        print("[IMU] i2c scan failed: {}".format(error))

    if found is None:
        _close_i2c(i2c)
        raise AppUnavailable(IMU_UNAVAILABLE)

    imu = QMI8658A(i2c, found)
    try:
        imu.soft_reset()
        whoami = imu.who_am_i()
        print("[IMU] QMI8658A @0x{:02X} WHO_AM_I=0x{:02X}".format(found, whoami))
        if whoami != 0x05:
            print("[IMU] warning: unexpected WHO_AM_I")
        imu.configure()
    except BaseException as error:
        print("[IMU] imu init failed: {}".format(error))
        _close_i2c(i2c)
        raise AppUnavailable(IMU_UNAVAILABLE)
    return imu


class _ChartView(object):
    """One curve page with auto-ranging Y axes driven by window peaks.

    The raw-data page and the attitude page share exactly the same curve logic;
    only the number of series, the step tables and the scale positions differ, so
    both live here. One instance per page, each holding its own window history, so
    they do not interfere (switching pages only toggles visibility while data keeps
    accumulating in the background).

    groups: ((first series index, end index, step table, default full scale,
              format string, show 0 at the middle tick), ...)
      Series inside one group share a full scale and one scale column.
    axis_xs: scale column x per group; None = that group draws no scale.
    """

    def __init__(self, name, colors, groups, axis_xs,
                 chart_x, chart_top, chart_w, chart_h, axis_w):
        self.name = name
        self.colors = colors
        self.groups = groups
        self.axis_xs = axis_xs
        self.cx = chart_x
        self.cy = chart_top
        self.cw = chart_w
        self.ch = chart_h
        self.aw = axis_w
        self.chart = None
        self.series = []
        self.axes = []
        self.hist = []
        self.full = [g[3] for g in groups]
        self.hold = [None] * len(groups)
        self.peak = [0.0] * len(groups)
        self.peak_ms = None
        self.filled = False

    # ---------- build ----------
    def build(self, parent, font):
        ch = lv.chart(parent)
        ch.set_pos(self.cx, self.cy)
        ch.set_size(self.cw, self.ch)
        ch.set_type(lv.chart.TYPE.LINE)
        ch.set_update_mode(lv.chart.UPDATE_MODE.SHIFT)
        ch.set_point_count(CHART_POINTS)
        ch.set_range(lv.chart.AXIS.PRIMARY_Y, CHART_Y_MIN, CHART_Y_MAX)
        ch.set_div_line_count(10, 8)
        ch.set_style_line_width(2, 0)
        ch.set_style_line_color(lv.color_hex(0x2A3340), 0)
        ch.set_style_bg_color(lv.color_hex(0x141A22), 0)
        ch.set_style_bg_opa(lv.OPA.COVER, 0)
        ch.set_style_border_width(1, 0)
        ch.set_style_border_color(lv.color_hex(0x39414D), 0)
        ch.set_style_radius(8, 0)
        ch.set_style_pad_all(4, 0)
        ch.clear_flag(lv.obj.FLAG.SCROLLABLE)
        self.chart = ch
        for col in self.colors:
            self.series.append(ch.add_series(lv.color_hex(col),
                                             lv.chart.AXIS.PRIMARY_Y))
        ys = (self.cy - 4, self.cy + self.ch // 2 - 9, self.cy + self.ch - 15)
        for gi in range(len(self.groups)):
            ax = self.axis_xs[gi]
            if ax is None:
                self.axes.append(None)
                continue
            labels = []
            # The scale colour comes from the group's first series
            # (colors[group[0]]), never colors[gi]: the angular-rate group starts at
            # index 3 and colors[1] belongs to the acceleration family -- using that
            # painted the right axis warm (measured on board).
            axis_color = self.colors[self.groups[gi][0]]
            for i in range(3):
                lb = lv.label(parent)
                lb.set_text("")
                lb.set_width(self.aw)
                lb.set_style_text_align(lv.TEXT_ALIGN.LEFT, 0)
                lb.set_style_text_color(lv.color_hex(axis_color), 0)
                if font is not None:
                    try:
                        lb.set_style_text_font(font, 0)
                    except BaseException:
                        pass
                lb.set_pos(ax, ys[i])
                labels.append(lb)
            self.axes.append(labels)
        self.refresh_axes()

    # ---------- auto-range ----------
    def _group_of(self, i):
        for gi, g in enumerate(self.groups):
            if g[0] <= i < g[1]:
                return gi
        return 0

    @staticmethod
    def _want(need, steps):
        for s in steps:
            if need <= s:
                return s
        return steps[-1]

    def _scan_peak(self):
        """Peak of the data still visible in the window, per group. Down-stepping must
        use it, not just the current frame."""
        res = [0.0] * len(self.groups)
        for s in self.hist:
            for gi, g in enumerate(self.groups):
                m = res[gi]
                for i in range(g[0], g[1]):
                    v = abs(s[i])
                    if v > m:
                        m = v
                res[gi] = m
        return res

    def refresh_axes(self):
        for gi, g in enumerate(self.groups):
            labels = self.axes[gi] if gi < len(self.axes) else None
            if not labels:
                continue
            f = self.full[gi]
            fmt = g[4]
            labels[0].set_text(fmt % f)
            labels[1].set_text("0" if g[5] else "")
            labels[2].set_text(fmt % -f)

    # ---------- data ----------
    def _push(self, sample):
        n = len(self.series)
        for i in range(n):
            gi = self._group_of(i)
            v = int(round(sample[i] * (CHART_Y_MAX / self.full[gi])))
            if v > CHART_Y_MAX:
                v = CHART_Y_MAX
            elif v < CHART_Y_MIN:
                v = CHART_Y_MIN
            self.chart.set_next_value(self.series[i], v)

    def refill(self):
        """Redraw the whole window at the current scale (scale change / first show / page return).

        Under SHIFT mode pushing CHART_POINTS points overwrites the whole ring
        buffer, so after a redraw the entire window shares one scale and there is no
        step between "old scale in the first half, new scale in the second". When
        history is short the earliest sample is repeated to fill it, keeping the
        sentinel point (32767) from being drawn at the top.
        """
        if not self.hist:
            self.filled = True
            return
        first = self.hist[0]
        for _ in range(CHART_POINTS - len(self.hist)):
            self._push(first)
        for s in self.hist:
            self._push(s)
        self.filled = True

    def update(self, sample, now, active=True):
        """Record one sample and redraw as needed. active=False only accumulates history.

        Pushing while the page is hidden is pointless (and burns CPU), but the
        history must keep growing or the page is empty when it comes back.
        """
        self.hist.append(sample)
        if len(self.hist) > CHART_POINTS:
            self.hist.pop(0)
        if self.peak_ms is None or \
                time.ticks_diff(now, self.peak_ms) >= PEAK_SCAN_MS:
            self.peak_ms = now
            self.peak = self._scan_peak()
        changed = False
        for gi, g in enumerate(self.groups):
            need = self.peak[gi]
            for i in range(g[0], g[1]):
                v = abs(sample[i])
                if v > need:
                    need = v
            need *= SCALE_HEADROOM
            want = self._want(need, g[2])
            cur = self.full[gi]
            if want > cur:
                # Step up immediately: looking small beats clipping the peak flat.
                self.full[gi] = want
                self.hold[gi] = None
                changed = True
            elif want < cur:
                if self.hold[gi] is None:
                    self.hold[gi] = now
                elif time.ticks_diff(now, self.hold[gi]) >= SCALE_HOLD_MS:
                    self.full[gi] = want
                    self.hold[gi] = None
                    changed = True
            else:
                self.hold[gi] = None
        if changed:
            self.refresh_axes()
            if active:
                self.refill()
        elif not self.filled:
            if active:
                self.refill()
        elif active:
            self._push(sample)


class ImuApp(object):
    def __init__(self, content, ctx):
        self.ctx = ctx
        self.imu = _open_imu()
        self._fails = 0
        self._samples = 0
        self._next_ms = None
        self._rate_ms = None
        self._rate_base = 0
        self._rate = 0
        # ---- pages ----
        self._page = PAGE_DASH
        self._pg = [None, None, None]     # container per page
        self._pager = None                # DragPager (built in _build_ui)
        self._sw_active = False
        self._sw_block = False            # this gesture was judged vertical, no paging at all
        self._sw_x = 0                    # gesture anchor (only for the vertical test)
        self._sw_y = 0
        self._sw_lx = 0                   # latest contact position (fed to the pager)
        self._sw_ly = 0
        self._sw_last_ms = 0
        self._cal_status = ""             # calibration segment of the footer row
        self._foots = []                  # one footer label per page
        self._dash_ms = 0
        # ---- attitude ----
        self._q = _Q_IDENT
        self._ahrs_ms = None
        self._yaw0 = None                 # heading zero (heading of the first sample)
        self._bias = [0.0, 0.0, 0.0]      # residual bias learned at runtime (not persisted)
        self._still_ms = None             # start of the current still period
        self._roll = 0.0                  # degrees
        self._pitch = 0.0
        self._yaw = 0.0
        # ---- curve pages ----
        # Raw data: accel XYZ + gyro XYZ, two auto-ranging groups, one scale each.
        self._raw_view = _ChartView(
            "raw",
            (COL_ACC[0], COL_ACC[1], COL_ACC[2],
             COL_GYR[0], COL_GYR[1], COL_GYR[2]),
            ((0, 3, ACC_FULL_STEPS, ACC_FULL_DEFAULT, "%+.2f", True),
             (3, 6, GYR_FULL_STEPS, GYR_FULL_DEFAULT, "%+.0f", False)),
            (AXIS_L_X, AXIS_R_X), CHART_X, CHART_TOP, CHART_W, CHART_H, AXIS_W)
        # Attitude: roll/pitch/yaw, one group sharing the angle steps, left scale only.
        self._att_view = _ChartView(
            "att", (COL_ATT[0], COL_ATT[1], COL_ATT[2]),
            ((0, 3, ATT_FULL_STEPS, ATT_FULL_DEFAULT, "%+.0f", True),),
            (AXIS_L_X,), CHART_X, CHART_TOP, CHART_W, CHART_H, AXIS_W)
        self._readout = []          # 7 value labels of the raw page readout row
        self._att_readout = []      # 3 value labels of the attitude page readout row
        self._readout_vals = None
        self._att_vals = None
        # ---- dashboard ----
        self._d_ground = None
        self._d_horizon = None
        self._d_rungs = []
        self._d_rail = []
        self._d_ticks = []
        self._d_ptr = None
        self._d_text = []
        self._d_hdg = None
        self._d_tape_ticks = []
        self._d_tape_ptr = None

        # ---- gyro bias ----
        # self._zero = (gx, gy, gz, temp) or None. Raw values are shown minus this;
        # calibration samples the un-subtracted raw values so a new bias is never
        # computed from already-corrected data.
        self._zero = load_gyro_zero()
        self._state = ST_NORMAL
        self._cal_action = None      # "start" / "skip", set by the button callback, read by step
        self._cal_deadline = None    # deadline of the countdown / acquisition
        self._cal_t0 = None          # acquisition start (progress is time based)
        self._cal_sum = [0.0, 0.0, 0.0]
        self._cal_sq = [0.0, 0.0, 0.0]   # sum of squares: per-axis σ at the end for the still check
        self._cal_temp = 0.0
        self._cal_n = 0
        self._cal_last_pct = -1      # last progress step shown (5% steps, deduplicated)
        self._from_menu = False      # whether the current prompt was entered from the menu
        self._overlay = None

        # ---- accelerometer six-face calibration ----
        # self._acc_cal = (bias_g[3], scale[3], temp) or None; display a=(raw-b)/s.
        self._acc_cal = load_acc_cal()
        self._acc_face = 0           # current face index 0..5, 6 = verification re-test
        self._acc_means = [None] * 7 # mean per face ((ax,ay,az))
        self._acc_action = None      # "sample"/"skip"/"retry"/"keep"/"back"
        self._acc_t0 = None
        self._acc_deadline = None
        self._pending = None         # (bias, scale, temp) waiting to be saved
        self._acc_sum = [0.0, 0.0, 0.0]
        self._acc_gsum = [0.0, 0.0, 0.0]
        self._acc_sq = [0.0, 0.0, 0.0]
        self._acc_temp = 0.0
        self._acc_n = 0
        self._acc_move = 0           # frames violating stillness
        self._acc_last_pct = -1
        self._acc_pose_ms = 0        # time the current POSE state was entered (debounce window)
        self._acc_why = ""           # reason of the last failed check / pairing

        self._build_ui(content)
        print("[IMU] ready: chart {}pts @{}ms | zero={} "
              "acc_cal={}".format(
                  CHART_POINTS, SAMPLE_MS,
                  "none" if self._zero is None else
                  "(%+.1f,%+.1f,%+.1f)@%.1fC" % self._zero,
                  "ok" if self._acc_cal is not None else "none"))
        # Boot calibration chain: gyro first (5s flat, cheap); once the gyro flow
        # ends (saved or Skipped) an uncalibrated accelerometer is guided through the
        # six faces. Skipping the six faces only silences this power cycle
        # (_ACC_PROMPTED); a power cycle asks again.
        if self._zero is None:
            self._enter_prompt(i18n.t("imu.need_gyro"))
        elif self._acc_cal is None and not _ACC_PROMPTED[0]:
            self._enter_acc_prompt(i18n.t("imu.need_acc"))
        else:
            self._update_info()

    # ---------- UI ----------
    def _label(self, parent, text, x, y, width, color, align, font):
        lb = lv.label(parent)
        lb.set_text(text)
        if width:
            lb.set_width(width)
            lb.set_style_text_align(align, 0)
        lb.set_style_text_color(lv.color_hex(color), 0)
        font = i18n.font_for(text, font)
        if font is not None:
            try:
                lb.set_style_text_font(font, 0)
            except BaseException:
                pass
        lb.set_pos(x, y)
        return lb

    def _page_box(self, parent, w=None, h=None):
        """Page container: fills the content area, transparent, not scrollable.
        Switching pages = showing/hiding a whole box."""
        box = lv.obj(parent)
        box.set_pos(0, 0)
        box.set_size(w or self.ctx.width, h or self.ctx.height)
        box.set_style_bg_opa(lv.OPA.TRANSP, 0)
        box.set_style_border_width(0, 0)
        box.set_style_pad_all(0, 0)
        box.set_style_radius(0, 0)
        box.clear_flag(lv.obj.FLAG.SCROLLABLE)
        return box

    def _build_ui(self, content):
        self._content = content
        value_font = _value_font()
        small = _font("font_montserrat_14")
        # All three pages fill the content area directly. The launcher starts this
        # app with immersive=True, so the content area is the whole 800x480 screen
        # (no status bar, no Home button) and all three pages have the same size.
        self._pg[PAGE_DASH] = self._page_box(content)
        self._pg[PAGE_RAW] = self._page_box(content)
        self._pg[PAGE_ATT] = self._page_box(content)

        # ================= page 1: cockpit dashboard =================
        box = self._pg[PAGE_DASH]
        # Sky: a static backdrop filling the main area. The ground is one very thick
        # line laid over the lower half, so only "ground" is drawn as a thick stroke
        # and half the fill cost is saved (measured on board: one thick layer ≈3.6ms,
        # two would double it).
        sky = lv.obj(box)
        sky.set_pos(0, 0)
        sky.set_size(self.ctx.width, DASH_MAIN_H)
        sky.set_style_bg_color(lv.color_hex(COL_SKY), 0)
        sky.set_style_bg_opa(lv.OPA.COVER, 0)
        sky.set_style_border_width(0, 0)
        sky.set_style_radius(0, 0)
        sky.set_style_pad_all(0, 0)
        sky.clear_flag(lv.obj.FLAG.SCROLLABLE)

        self._d_ground = _mk_line(box, COL_GROUND, DASH_GROUND_W, 2)
        self._d_horizon = _mk_line(box, COL_HORIZON, 3, 2)
        # Pitch ladder: the lower 3 rungs (dive) are amber, the upper 3 (climb)
        # white. Indices 0..2 = -30..-10.
        for i in range(6):
            self._d_rungs.append(
                _mk_line(box, COL_LADDER_NEG if i < 3 else COL_LADDER_POS, 2, 2))
        # Roll arc: the rail is 25 short straight segments (lv.arc gives no control
        # over thickness or rounding, and the ticks must be drawn by hand to be
        # accurate), plus 11 inward ticks and 1 gold pointer.
        self._d_rail = []
        for i in range(25):
            self._d_rail.append(_mk_line(box, COL_ARC, 2, 2))
        for i in range(11):
            self._d_ticks.append(_mk_line(box, COL_ARC, 2, 2))
        self._d_ptr = _mk_line(box, COL_AIRCRAFT, 5, 2)

        # Fixed aircraft symbol (static, positioned once at build time).
        # A black outline is drawn first and the gold body on top: level with the
        # horizon the gold bars and the white horizon blur into one line, and only
        # the outline keeps the shape readable (seen in board screenshots).
        WING_OUT, WING_IN = 66, 10
        for seg in ((-WING_OUT, -WING_IN), (WING_IN, WING_OUT)):
            back = _mk_line(box, 0x000000, 11, 2)
            _set_pts(back, [{"x": DASH_CX + seg[0], "y": DASH_CY},
                            {"x": DASH_CX + seg[1], "y": DASH_CY}])
        back_tail = _mk_line(box, 0x000000, 11, 2)
        _set_pts(back_tail, [{"x": DASH_CX, "y": DASH_CY + 5},
                             {"x": DASH_CX, "y": DASH_CY + 22}])
        dot_back = lv.obj(box)
        dot_back.set_pos(DASH_CX - 8, DASH_CY - 8)
        dot_back.set_size(16, 16)
        dot_back.set_style_bg_color(lv.color_hex(0x000000), 0)
        dot_back.set_style_bg_opa(lv.OPA.COVER, 0)
        dot_back.set_style_border_width(0, 0)
        dot_back.set_style_radius(0x7FFF, 0)
        dot_back.set_style_pad_all(0, 0)
        dot_back.clear_flag(lv.obj.FLAG.SCROLLABLE)

        for seg in ((-WING_OUT, -WING_IN), (WING_IN, WING_OUT)):
            ln = _mk_line(box, COL_AIRCRAFT, 5, 2)
            _set_pts(ln, [{"x": DASH_CX + seg[0], "y": DASH_CY},
                          {"x": DASH_CX + seg[1], "y": DASH_CY}])
        tail = _mk_line(box, COL_AIRCRAFT, 5, 2)
        _set_pts(tail, [{"x": DASH_CX, "y": DASH_CY + 6},
                        {"x": DASH_CX, "y": DASH_CY + 20}])
        dot = lv.obj(box)
        dot.set_pos(DASH_CX - 6, DASH_CY - 6)
        dot.set_size(12, 12)
        dot.set_style_bg_color(lv.color_hex(COL_AIRCRAFT), 0)
        dot.set_style_bg_opa(lv.OPA.COVER, 0)
        dot.set_style_border_width(0, 0)
        # This binding has no lv.RADIUS constant, so the circle radius is LVGL's
        # LV_RADIUS_CIRCLE (0x7FFF) directly. set_style_radius takes an integer and
        # anything larger is clamped to half the short side.
        dot.set_style_radius(0x7FFF, 0)
        dot.set_style_pad_all(0, 0)
        dot.clear_flag(lv.obj.FLAG.SCROLLABLE)

        # Attitude readout (top left; the left end of the arc is at x≈250, so they
        # do not collide): one prefix label (left aligned) + one value label (right
        # aligned inside a fixed-width box).
        self._d_pre = []
        for i, ch in enumerate(("R", "P", "Y")):
            y = 6 + i * 21
            self._d_pre.append(
                self._label(box, ch, DASH_TXT_X, y, 0, 0xFFFFFF,
                            lv.TEXT_ALIGN.LEFT, value_font))
            self._d_text.append(
                self._label(box, "", DASH_VAL_X, y, DASH_VAL_W, 0xFFFFFF,
                            lv.TEXT_ALIGN.RIGHT, value_font))

        # Heading tape (bottom)
        tape = lv.obj(box)
        tape.set_pos(0, DASH_TAPE_Y)
        tape.set_size(self.ctx.width, DASH_TAPE_H)
        tape.set_style_bg_color(lv.color_hex(COL_TAPE_BG), 0)
        tape.set_style_bg_opa(lv.OPA.COVER, 0)
        tape.set_style_border_width(0, 0)
        tape.set_style_radius(0, 0)
        tape.set_style_pad_all(0, 0)
        tape.clear_flag(lv.obj.FLAG.SCROLLABLE)
        for i in range(21):
            self._d_tape_ticks.append(_mk_line(box, COL_TAPE_TICK, 2, 2))
        self._d_tape_ptr = _mk_line(box, COL_AIRCRAFT, 7, 2)
        _set_pts(self._d_tape_ptr, [{"x": DASH_CX, "y": DASH_TAPE_Y},
                                    {"x": DASH_CX, "y": DASH_TAPE_Y + 9}])
        self._d_hdg = self._label(box, "", DASH_CX - 32, DASH_TAPE_Y + 14, 64,
                                  COL_AIRCRAFT, lv.TEXT_ALIGN.CENTER,
                                  value_font)
        # The dashboard carries no footer row: the user asked for a pure cockpit with
        # no hint text. Exit still works through the physical CANCEL key, or by
        # swiping to page 2/3 and using Home in the status bar.
        self._update_dash(0.0, 0.0, 0.0)

        # ================= page 2: raw data curves =================
        box = self._pg[PAGE_RAW]
        # Readout row: prefix, separators and values all in mono so they share one
        # font (two fonts differ by 4px of baseline).
        self._label(box, "A(g)", RD_A_X, READOUT_Y, 0, COL_ACC_HEAD,
                    lv.TEXT_ALIGN.LEFT, value_font)
        self._label(box, "G(dps)", RD_G_X, READOUT_Y, 0, COL_GYR_HEAD,
                    lv.TEXT_ALIGN.LEFT, value_font)
        self._label(box, "T(C)", RD_T_X, READOUT_Y, 0, COL_TEMP_HEAD,
                    lv.TEXT_ALIGN.LEFT, value_font)
        for x in (RD_SEP1_X, RD_SEP2_X):
            self._label(box, "|", x, READOUT_Y, 0, COL_INFO,
                        lv.TEXT_ALIGN.LEFT, value_font)
        for c in range(3):
            self._readout.append(self._label(box, "", RD_AX[c], READOUT_Y,
                                             RD_VAL_W, COL_ACC[c],
                                             lv.TEXT_ALIGN.LEFT, value_font))
        for c in range(3):
            self._readout.append(self._label(box, "", RD_GX[c], READOUT_Y,
                                             RD_VAL_W, COL_GYR[c],
                                             lv.TEXT_ALIGN.LEFT, value_font))
        self._readout.append(self._label(box, "", RD_TEMP_X, READOUT_Y,
                                         RD_VAL_W, COL_TEMP,
                                         lv.TEXT_ALIGN.LEFT, value_font))
        self._raw_view.build(box, value_font)
        self._foots.append(self._label(box, _foot_initial(), 20, FOOT_Y, 0,
                                       COL_INFO, lv.TEXT_ALIGN.LEFT, small))

        # ================= page 3: attitude curves =================
        box = self._pg[PAGE_ATT]
        # Three groups (6-char prefix 60px + 20px gap + 6-char value 60px = 140px
        # each) centred horizontally as one block. Laying them out from RD_A_X on the
        # left squeezed the block into the left half and left a large empty area on
        # the right (user reported it was not centred).
        gw = 140
        x0 = (self.ctx.width - gw * 3) // 2
        for i, pre in enumerate(("R(deg)", "P(deg)", "Y(deg)")):
            gx = x0 + i * gw
            self._label(box, pre, gx, READOUT_Y, 0, COL_ATT_HEAD[i],
                        lv.TEXT_ALIGN.LEFT, value_font)
            self._att_readout.append(
                self._label(box, "", gx + 80, READOUT_Y, RD_VAL_W, COL_ATT[i],
                            lv.TEXT_ALIGN.LEFT, value_font))
        self._att_view.build(box, value_font)
        self._foots.append(self._label(box, _foot_initial(), 20, FOOT_Y, 0,
                                       COL_INFO, lv.TEXT_ALIGN.LEFT, small))

        # All three pages share one pager (the same implementation the audio app uses,
        # so the feel matches): the drag tracks the finger, release decides between
        # snapping back and paging, and swiping on the last page wraps to the first.
        self._pager = paging.DragPager(
            self._pg, getattr(self.ctx, "screen_w", 800),
            circular=True, on_page_changed=self._prepare_page)
        self._page = PAGE_DASH
        self._prepare_page(PAGE_DASH)

    # ---------- page switching / swiping ----------
    def _prepare_page(self, target):
        """Prepare the target page's data before the switch (redraw curves, refresh footer).

        DragPager calls this when "the target page is decided" (the moment the snap
        animation starts), so the incoming page is already populated and data does
        not pop in half way through the swipe.
        """
        self._page = target
        # Entering a curve page requires a full redraw: while hidden it only
        # accumulates history and pushes no points, so the chart's internal buffer
        # still holds the picture from when the page was left, and using it directly
        # shows a frozen stretch of old curve.
        if target == PAGE_RAW:
            self._raw_view.refill()
            self._readout_vals = None      # force a readout refresh next frame (no stale values)
        elif target == PAGE_ATT:
            self._att_view.refill()
            self._att_vals = None
        self._refresh_foot()

    def _set_page(self, page):
        """Jump to the given page without animation (build time / back to the first
        page after calibration)."""
        target = page % PAGE_COUNT
        self._pager.go(target, animate=False)
        self._page = target

    def _poll_swipe(self, now):
        """Feed the raw touch stream to DragPager (finger-tracking drag + release decision + wrap).

        The gesture start/end is decided purely by the "quiet timeout", **never by Up
        events**: FT5316 emits fake Ups while dragging (Down+Up pairs in the same
        batch), and resetting state on Down/Up resets the origin to wherever the
        finger is, so the displacement never accumulates (measured symptom: "the
        swipe just does not work", and it is not a threshold issue).

        This function only reports "is it held + current x"; the displacement maths,
        tracking placement, release decision and snap animation all live in DragPager
        (the audio app shares that implementation, so the feel matches).
        """
        touch = getattr(self.ctx, "touch", None)
        if touch is None:
            return
        # No gesture handling while a dialog is open: during calibration step takes
        # another branch and never calls this function, so the queue fills up to its
        # cap (64 points). Left alone, closing the dialog would read a pile of stale
        # contacts and flip a page out of nowhere. Drain and drop them here.
        if self._overlay is not None:
            try:
                touch.read_raw()
            except BaseException:
                pass
            self._sw_active = False
            self._sw_block = False
            self._pager.poll(now, False, 0, 0)
            return
        try:
            raw = touch.read_raw()
        except BaseException:
            raw = None
        if raw:
            self._sw_last_ms = now
            sx = raw[-1][0]                # several points in one batch: the last one wins
            sy = raw[-1][1]
            self._sw_lx = sx
            self._sw_ly = sy
            if not self._sw_active:
                # New gesture: record the anchor (only used to tell whether it is vertical)
                self._sw_active = True
                self._sw_block = False
                self._sw_x = raw[0][0]
                self._sw_y = raw[0][1]
            # Clearly dominant vertical = in-page drag, so the whole gesture pages
            # nothing (_sw_block holds until the finger lifts, otherwise the anchor
            # keeps resetting mid-drag and the vertical test stops working)
            if not self._sw_block:
                ady = abs(sy - self._sw_y)
                adx = abs(sx - self._sw_x)
                if ady > adx * 1.5 + 20:
                    self._sw_block = True
                    self._pager.poll(now, False, sx, sy)
                    return
                self._pager.poll(now, True, sx, sy)
            return
        # No new contact this round: the gesture ends once the quiet period exceeds
        # the holdoff
        if self._sw_active and \
                time.ticks_diff(now, self._sw_last_ms) > SWIPE_RELEASE_MS:
            self._sw_active = False
            self._sw_block = False
        if self._sw_block:
            return
        # The **latest position** must be passed, never the anchor: with a still
        # finger most frames carry no new contact, and passing the anchor recomputes
        # the displacement as 0 -- the page then snaps back for no reason while the
        # finger is held down (measured on board).
        self._pager.poll(now, self._sw_active, self._sw_lx, self._sw_ly)

    # ---------- dashboard drawing ----------
    def _update_dash(self, roll_deg, pitch_deg, yaw_deg):
        """Redraw the attitude indicator + roll arc + heading tape from the angles.

        Geometry (screen coordinates, y downwards):
          horizon direction = (cos R, -sin R)   roll R>0 = bank right (right end up)
          ground normal     = (sin R,  cos R)   ground lies on the positive side
          pitch offset      = pitch * PPD; climbing (>0) moves the horizon down
        """
        sr = math.sin(roll_deg * _DEG2RAD)
        cr = math.cos(roll_deg * _DEG2RAD)
        pp = pitch_deg * DASH_PPD
        lim = DASH_CY - 6
        if pp > lim:
            pp = lim
        elif pp < -lim:
            pp = -lim
        hcx = DASH_CX
        hcy = DASH_CY + pp
        t = DASH_LINE_T

        # Ground: one very thick line whose centre is offset half a line width along
        # the ground normal, so its inner edge lands exactly on the horizon and only
        # the "below the horizon" half plane is visible on screen.
        half = DASH_GROUND_W * 0.5
        gx = hcx + sr * half
        gy = hcy + cr * half
        _set_pts(self._d_ground, [
            {"x": int(gx - t * cr), "y": int(gy + t * sr)},
            {"x": int(gx + t * cr), "y": int(gy - t * sr)}])
        _set_pts(self._d_horizon, [
            {"x": int(hcx - t * cr), "y": int(hcy + t * sr)},
            {"x": int(hcx + t * cr), "y": int(hcy - t * sr)}])

        # Pitch ladder: ±10/20/30, rungs run along the horizon direction and their
        # centre follows the pitch offset. Rungs falling inside the roll arc area
        # (y < DASH_LADDER_TOP) are hidden off screen: a real attitude indicator does
        # the same, and as the horizon drops the higher rungs come into view.
        for i, dp in enumerate((-30, -20, -10, 10, 20, 30)):
            rcx = hcx - dp * DASH_PPD * sr
            rcy = hcy - dp * DASH_PPD * cr
            rlen = 46 if abs(dp) >= 20 else 32
            if rcy < DASH_LADDER_TOP or rcy > DASH_MAIN_H + 20:
                _set_pts(self._d_rungs[i], [{"x": -400, "y": -400},
                                            {"x": -400, "y": -400}])
                continue
            _set_pts(self._d_rungs[i], [
                {"x": int(rcx - rlen * cr), "y": int(rcy + rlen * sr)},
                {"x": int(rcx + rlen * cr), "y": int(rcy - rlen * sr)}])

        # Roll arc: rail (25 segments) + ticks + pointer (centre at the top edge
        # midline, 0° = straight down)
        for i in range(25):
            a0 = (30.0 + i * 5.0) * _DEG2RAD
            a1 = (30.0 + (i + 1) * 5.0) * _DEG2RAD
            _set_pts(self._d_rail[i], [
                {"x": int(DASH_CX + DASH_ARC_R * math.cos(a0)),
                 "y": int(DASH_ARC_CY + DASH_ARC_R * math.sin(a0))},
                {"x": int(DASH_CX + DASH_ARC_R * math.cos(a1)),
                 "y": int(DASH_ARC_CY + DASH_ARC_R * math.sin(a1))}])
        for i, tr in enumerate((-60, -45, -30, -20, -10, 0,
                                10, 20, 30, 45, 60)):
            ang = (90.0 - tr) * _DEG2RAD
            ca = math.cos(ang)
            sa = math.sin(ang)
            # Ticks point into the arc (away from the centre = down the screen): the
            # centre sits above the screen, so a larger radius is "inward". A smaller
            # radius stacked every tick above the rail like a row of burrs.
            inner = DASH_ARC_R + (12 if tr % 30 == 0 else 7)
            _set_pts(self._d_ticks[i], [
                {"x": int(DASH_CX + DASH_ARC_R * ca),
                 "y": int(DASH_ARC_CY + DASH_ARC_R * sa)},
                {"x": int(DASH_CX + inner * ca),
                 "y": int(DASH_ARC_CY + inner * sa)}])
        pa = (90.0 - roll_deg) * _DEG2RAD
        if pa < 0.3491:
            pa = 0.3491                 # clamp to ±70° (the arc only covers 30~150°)
        elif pa > 2.7925:
            pa = 2.7925
        # The pointer straddles the rail (10px in / 10px out): drawn to one side only
        # it looks detached.
        _set_pts(self._d_ptr, [
            {"x": int(DASH_CX + (DASH_ARC_R + 10) * math.cos(pa)),
             "y": int(DASH_ARC_CY + (DASH_ARC_R + 10) * math.sin(pa))},
            {"x": int(DASH_CX + (DASH_ARC_R - 10) * math.cos(pa)),
             "y": int(DASH_ARC_CY + (DASH_ARC_R - 10) * math.sin(pa))}])

        # Readout: the prefix is a fixed label on the left, so only the value is
        # updated here and it **must not be zero padded**. The value box has a fixed
        # width and right alignment, so padding is not needed to widen it -- padding
        # only adds leading zeros when the number is short (the old %07.1f turned
        # 359.0 into "00359.0").
        self._d_text[0].set_text("%+.1f" % roll_deg)
        self._d_text[1].set_text("%+.1f" % pitch_deg)
        self._d_text[2].set_text("%.1f" % yaw_deg)

        # Heading tape: 21 ticks at 10° slide with the heading, with a centred readout
        first = int(math.floor((yaw_deg - 100.0) / 10.0)) * 10
        for i in range(21):
            v = first + i * 10
            sx = DASH_CX + (v - yaw_deg) * DASH_TAPE_PPD
            h = 14 if (v % 90) == 0 else (10 if (v % 30) == 0 else 6)
            _set_pts(self._d_tape_ticks[i], [
                {"x": int(sx), "y": DASH_TAPE_Y + DASH_TAPE_H - h},
                {"x": int(sx), "y": DASH_TAPE_Y + DASH_TAPE_H}])
        self._d_hdg.set_text("%03d" % (int(round(yaw_deg)) % 360))


    # ---------- auto-ranging ----------
    # Moved down into _ChartView (each page's full scale / peak / redraw is owned by
    # its own instance).
    # ---------- page data ----------
    def _chart_update(self, ax, ay, az, gx, gy, gz, now):
        """Feed samples to the raw page and the attitude page at the same time.

        Both pages keep their own window history (accumulated even in the background),
        but only the visible page really pushes points, so switching pages immediately
        shows a full 3s curve and the hidden page costs no drawing time.
        """
        self._raw_view.update((ax, ay, az, gx, gy, gz), now,
                              self._page == PAGE_RAW)
        self._att_view.update((self._roll, self._pitch, self._yaw), now,
                              self._page == PAGE_ATT)

    # ---------- attitude estimation (Mahony) ----------
    def _track_bias(self, gx, gy, gz, now):
        """Learn the gyro residual bias while still (temperature drift compensation).

        The stored bias is the value at the temperature of the calibration, and it
        drifts as the die temperature changes (TCO ±0.1dps/℃). A 6-axis part has no
        magnetometer, so the heading only comes from gyro integration and that
        residual becomes a steady ~1°/s drift -- the heading tape turns by itself.

        Learning only happens when all three axes are small for a sustained period: a
        real rotation is far above 1.5dps and is never mistaken for bias, and the
        transition while changing pose is blocked by the 400ms gate.
        """
        m = max(abs(gx), abs(gy), abs(gz))
        if m > BIAS_STILL_DPS:
            self._still_ms = None
            return
        if self._still_ms is None:
            self._still_ms = now
            return
        if time.ticks_diff(now, self._still_ms) < BIAS_STILL_MS:
            return
        a = BIAS_ALPHA
        self._bias[0] += (gx - self._bias[0]) * a
        self._bias[1] += (gy - self._bias[1]) * a
        self._bias[2] += (gz - self._bias[2]) * a

    # ---------- calibration dialog ----------
    def _update_info(self):
        """Refresh the calibration segment of the footer row (the chart pages have no
        top info row)."""
        self._cal_status = "{} | {}".format(
            i18n.t("imu.gyro_cal") if self._zero is not None
            else i18n.t("imu.gyro_raw"),
            i18n.t("imu.acc_cal") if self._acc_cal is not None
            else i18n.t("imu.acc_raw"))
        self._refresh_foot()

    def _refresh_foot(self):
        """Footer row per page: rate + calibration state + page number + hint.

        One label per page (inside its own container), all updated together.

        The text must stay short: this uses montserrat-14 (proportional), and 800px
        holds about 70 characters before it is clipped (measured in board
        screenshots: adding the page number cut "Home or CANCEL: exit" in half).
        The sample counter and the full key description were therefore dropped and
        compressed to "OK:cal CANCEL:exit".
        """
        page = self._page + 1
        hint = i18n.t("imu.swipe_right") if page >= PAGE_COUNT \
            else i18n.t("imu.swipe_left")
        text = i18n.t("imu.footer").format(
            self._rate, self._cal_status, page, PAGE_COUNT, hint)
        f = i18n.font_for(text, None)
        for lb in self._foots:
            lb.set_text(text)
            if f is not None:
                try:
                    lb.set_style_text_font(f, 0)
                except BaseException:
                    pass

    def _menu_body(self):
        """Menu body: list the current state of both calibrations."""
        return i18n.t("imu.menu_body") % (
            i18n.t("imu.calibrated") if self._zero is not None
            else i18n.t("imu.raw_state"),
            i18n.t("imu.calibrated") if self._acc_cal is not None
            else i18n.t("imu.raw_state"))

    def _build_overlay(self):
        """Build the calibration dialog (backdrop + card + buttons). Built once, then reused.

        The backdrop ignores clicks: calibration is a required step, and "tap the empty
        area to dismiss" makes users think it can be skipped, which is more confusing.
        Use Skip instead.

        Font sizes (the user reported the built-in 14/16px was unreadable): title and
        buttons use the 28px Nunito Bold bin font (the same face as the Cancel button of
        the launcher's unavailable panel and the on-screen keyboard), body uses 22px --
        any larger and four lines no longer fit. Both are bitmap fonts, so a redraw is
        only a blit and the dialog does not slow redraws down.
        """
        title_font = _dialog_font(DIALOG_TITLE_FONT)
        body_font = _dialog_font(DIALOG_BODY_FONT)
        self._ov_title_font = title_font
        self._ov_body_font = body_font

        backdrop = lv.obj(self._content)
        backdrop.set_size(self.ctx.width, self.ctx.height)
        backdrop.set_pos(0, 0)
        backdrop.set_style_bg_color(lv.color_hex(0x000000), 0)
        backdrop.set_style_bg_opa(lv.OPA._60, 0)
        backdrop.set_style_border_width(0, 0)
        backdrop.set_style_radius(0, 0)
        backdrop.set_style_pad_all(0, 0)
        backdrop.clear_flag(lv.obj.FLAG.SCROLLABLE)

        # Vertical budget (600x344): title 28px at 24..54 -> body 22px at
        # 72..(72+4×26=176) -> buttons 200x64 at -30 from the bottom (250..314).
        # After the font grew, 560x300 overflowed, so the card is wider and taller and
        # leaves ~70px between the last body line and the buttons.
        panel = lv.obj(backdrop)
        panel.set_size(600, 344)
        panel.align(lv.ALIGN.CENTER, 0, 0)
        panel.set_style_bg_color(lv.color_hex(0x1E222B), 0)
        panel.set_style_bg_opa(lv.OPA.COVER, 0)
        panel.set_style_border_width(1, 0)
        panel.set_style_border_color(lv.color_hex(0x39414D), 0)
        panel.set_style_radius(20, 0)
        panel.set_style_pad_all(0, 0)
        panel.clear_flag(lv.obj.FLAG.SCROLLABLE)

        title = lv.label(panel)
        title.set_text(i18n.t("imu.gyro_title"))
        title.set_style_text_color(lv.color_hex(0xFFFFFF), 0)
        tf = i18n.font_for(i18n.t("imu.gyro_title"), title_font, i18n.CJK_FONT_DIALOG)
        if tf is not None:
            title.set_style_text_font(tf, 0)
        title.align(lv.ALIGN.TOP_MID, 0, 24)

        body = lv.label(panel)
        body.set_text("")
        # Body is one step dimmer than the title but lighter than the old 0x9CA3AF: at
        # 22px that grey reads as washed out.
        body.set_style_text_color(lv.color_hex(0xB9C0CC), 0)
        if body_font is not None:
            body.set_style_text_font(body_font, 0)
        body.set_style_text_align(lv.TEXT_ALIGN.CENTER, 0)
        try:
            body.set_style_max_width(560, 0)
        except BaseException:
            body.set_width(560)
        body.align(lv.ALIGN.TOP_MID, 0, 72)

        self._btn_labels = []
        self._btn_acts = [None, None, None]

        def make_btn(text, bg, x, idx):
            btn = lv.btn(panel)
            # 170px: with three buttons (the calibration menu) 3x200 plus spacing would
            # exceed the 600px card.
            btn.set_size(170, 64)
            btn.align(lv.ALIGN.BOTTOM_MID, x, -30)
            btn.set_style_bg_color(lv.color_hex(bg), 0)
            btn.set_style_bg_opa(lv.OPA.COVER, 0)
            btn.set_style_radius(18, 0)
            btn.set_style_border_width(0, 0)
            btn.set_style_shadow_width(0, 0)
            btn.set_style_outline_width(0, 0)
            try:
                btn.set_style_transform_width(4, lv.STATE.PRESSED)
                btn.set_style_transform_height(4, lv.STATE.PRESSED)
            except BaseException:
                pass
            lb = lv.label(btn)
            lb.set_text(text)
            lb.set_style_text_color(lv.color_hex(0xFFFFFF), 0)
            bf = i18n.font_for(text, title_font, i18n.CJK_FONT_DIALOG)
            if bf is not None:
                lb.set_style_text_font(bf, 0)
            lb.center()
            self._btn_labels.append(lb)
            # The action is stored on the Python side (per slot): lv objects are C
            # instances and attaching arbitrary attributes to them is unreliable.
            # _set_overlay changes text/colour/action only and never rebuilds buttons.
            beep.on_click(btn, lambda e, _i=idx: self._on_btn(self._btn_acts[_i]))
            return btn

        # All three buttons are always present and switch text/colour/visibility by
        # dialog state (the menu needs three, every other state uses the first two).
        self._btns = (
            make_btn(i18n.t("imu.btn_start"), 0x16A34A, -182, 0),
            make_btn(i18n.t("imu.btn_skip"), 0x374151, 0, 1),
            make_btn(i18n.t("imu.btn_back"), 0x374151, 182, 2))
        self._ov_title = title
        self._ov_body = body
        self._ov_panel = panel
        self._overlay = backdrop

    def _on_btn(self, action):
        """Buttons only set a flag; the real work happens in step (heavy work in a
        callback stalls the frame).

        Routed by state: acceleration states write _acc_action, everything else writes
        _cal_action.
        """
        if action is None:
            return
        if self._state >= ST_ACC_PROMPT:
            self._acc_action = action
        else:
            self._cal_action = action

    def _set_overlay(self, title, body, buttons):
        """Update the dialog text and buttons.

        buttons: None hides the buttons (countdown / sampling); otherwise
        ((text, bg, act), ...) with at most 3, laid out left to right. The action names
        are consumed by the state machine in step.
        """
        if self._overlay is None:
            self._build_overlay()
        self._ov_title.set_text(title)
        tf = i18n.font_for(title, self._ov_title_font, i18n.CJK_FONT_DIALOG)
        if tf is not None:
            self._ov_title.set_style_text_font(tf, 0)
        self._ov_body.set_text(body)
        bf = i18n.font_for(body, self._ov_body_font, i18n.CJK_FONT_16)
        if bf is not None:
            self._ov_body.set_style_text_font(bf, 0)
        for idx, btn in enumerate(self._btns):
            if buttons and idx < len(buttons):
                text, bg, act = buttons[idx]
                self._btn_labels[idx].set_text(text)
                bf = i18n.font_for(text, self._ov_title_font, i18n.CJK_FONT_DIALOG)
                if bf is not None:
                    self._btn_labels[idx].set_style_text_font(bf, 0)
                self._btn_acts[idx] = act
                btn.set_style_bg_color(lv.color_hex(bg), 0)
                # Distribute symmetrically by visible button count: fixed x=-182/0/182
                # is "three buttons centred" and shifts left with two (measured, and it
                # did happen). For n buttons the centre is
                # -(n-1)/2*spacing + idx*spacing.
                n = len(buttons)
                off = (2 * idx - (n - 1)) * ACC_BTN_SPACING // 2
                btn.align(lv.ALIGN.BOTTOM_MID, off, -30)
                btn.clear_flag(lv.obj.FLAG.HIDDEN)
            else:
                self._btn_acts[idx] = None
                btn.add_flag(lv.obj.FLAG.HIDDEN)
        # With buttons the body starts at 72 (not bottom aligned); without buttons
        # (countdown / sampling) it moves to 120 so the single line sits near the
        # visual centre of the card.
        self._ov_body.align(lv.ALIGN.TOP_MID, 0, 72 if buttons else 120)

    def _enter_prompt(self, reason, from_menu=False):
        """Enter the "calibrate or not" prompt. from_menu: entered from the menu, where
        Skip becomes Back."""
        self._state = ST_PROMPT
        self._from_menu = from_menu
        self._cal_action = None
        buttons = ((i18n.t("imu.btn_start"), 0x16A34A, "start"),
                   (i18n.t("imu.btn_back") if from_menu
                    else i18n.t("imu.btn_skip"), 0x374151,
                    "back" if from_menu else "skip"))
        self._set_overlay(
            i18n.t("imu.gyro_title"),
            i18n.t("imu.gyro_prompt") % reason,
            buttons)

    def _enter_menu(self):
        """OK in the normal state: the gyro / accel calibration menu (re-entry point for
        both)."""
        self._state = ST_MENU
        self._cal_action = None
        self._set_overlay(i18n.t("imu.menu_title"), self._menu_body(),
                          ((i18n.t("imu.btn_gyro"), 0x16A34A, "gyro"),
                           (i18n.t("imu.btn_accel"), 0x2563EB, "accel"),
                           (i18n.t("imu.btn_back"), 0x374151, "back")))

    def _close_overlay(self):
        # Every calibration exit passes through here: the log handle must be closed or
        # a soft reset locks the file.
        _cal_log_close()
        self._state = ST_NORMAL
        self._cal_action = None
        self._acc_action = None
        self._from_menu = False
        # Drop every touch point seen while the dialog was open, and drop the
        # in-progress gesture state: otherwise the button-press displacement would be
        # read as a swipe the instant the dialog closes and flip a page.
        touch = getattr(self.ctx, "touch", None)
        if touch is not None:
            clear = getattr(touch, "clear_raw", None)
            if clear is not None:
                try:
                    clear()
                except BaseException:
                    pass
        self._sw_active = False
        if self._overlay is not None:
            try:
                self._overlay.delete()
            except BaseException:
                pass
            self._overlay = None
        self._update_info()

    def _maybe_acc_prompt(self):
        """Follow the gyro flow (saved or Skipped) with the accelerometer guidance.

        The order is not only about cost: the six-face still check needs a calibrated
        gyro. Skipping only silences this power cycle (_ACC_PROMPTED is a module-level
        flag and is not persisted); a power cycle asks again.
        """
        if self._acc_cal is None and not _ACC_PROMPTED[0]:
            self._enter_acc_prompt(i18n.t("imu.acc_not_yet"))

    # ---------- calibration state machine (advanced in step, never blocking the UI) ----------
    def _cal_tick(self, now):
        """Return True when this frame was consumed by calibration, skipping the normal
        display path."""
        if self._cal_action is not None:
            action = self._cal_action
            self._cal_action = None
            if action == "back":
                if self._state in (ST_PROMPT, ST_COUNTDOWN, ST_SAMPLING):
                    _cal_log_line("ABORT: user backed out of gyro cal")
                if self._state == ST_MENU:
                    self._close_overlay()
                    return False
                self._enter_menu()   # Back inside the prompt: back to the menu, keeping context
                return True
            if action == "skip":
                if self._state in (ST_PROMPT, ST_COUNTDOWN, ST_SAMPLING):
                    _cal_log_line("ABORT: user skipped gyro cal")
                self._close_overlay()
                self._maybe_acc_prompt()
                return False
            if action == "start" and self._state == ST_PROMPT:
                _cal_log_open(GYRO_LOG_PATH, "gyro zero cal")
                _cal_log_line("prev zero: %s" % (
                    "none" if self._zero is None else
                    "(%+.3f,%+.3f,%+.3f)@%.2fC" % self._zero))
                self._state = ST_COUNTDOWN
                self._cal_deadline = time.ticks_add(now, CAL_COUNTDOWN_MS)
            elif self._state == ST_MENU:
                if action == "gyro":
                    self._enter_prompt(i18n.t("imu.recal_gyro"), True)
                elif action == "accel":
                    self._enter_acc_prompt(i18n.t("imu.recal_acc"),
                                          True)
                return True

        if self._state in (ST_PROMPT, ST_MENU):
            return True

        if self._state == ST_COUNTDOWN:
            left = time.ticks_diff(self._cal_deadline, now)
            if left > 0:
                secs = (left + 999) // 1000
                self._set_overlay(
                    i18n.t("imu.hold_still"),
                    i18n.t("imu.countdown") % secs,
                    False)
                return True
            # Countdown over -> start sampling
            self._state = ST_SAMPLING
            self._cal_t0 = now
            self._cal_deadline = time.ticks_add(now, CAL_SAMPLE_MS)
            self._cal_sum = [0.0, 0.0, 0.0]
            self._cal_sq = [0.0, 0.0, 0.0]
            self._cal_temp = 0.0
            self._cal_n = 0
            self._cal_last_pct = -1
            print("[IMU] cal start: {}ms countdown done".format(CAL_COUNTDOWN_MS))

        if self._state == ST_SAMPLING:
            # Several reads this frame; averaging makes the bias steadier (noise falls
            # as √n).
            for _ in range(CAL_PER_FRAME):
                try:
                    s = self.imu.read_sample()
                except BaseException as error:
                    print("[IMU] cal read failed: {}".format(error))
                    continue
                self._cal_sum[0] += s[3]
                self._cal_sum[1] += s[4]
                self._cal_sum[2] += s[5]
                self._cal_sq[0] += s[3] * s[3]
                self._cal_sq[1] += s[4] * s[4]
                self._cal_sq[2] += s[5] * s[5]
                self._cal_temp += s[6]
                self._cal_n += 1
            # Progress is time based: it reaches 100% at any frame rate, so the user can
            # expect the end after 2 seconds.
            elapsed = time.ticks_diff(now, self._cal_t0)
            pct = min(100, elapsed * 100 // CAL_SAMPLE_MS)
            step_pct = pct // 5 * 5
            if step_pct != self._cal_last_pct:
                self._cal_last_pct = step_pct
                self._set_overlay(
                    i18n.t("imu.calibrating"),
                    i18n.t("imu.collecting") % (pct, self._cal_n),
                    False)
            if time.ticks_diff(self._cal_deadline, now) > 0:
                return True
            self._finish_calibration()
            return False

        return False

    def _finish_calibration(self):
        if self._cal_n <= 0:
            print("[IMU] cal failed: no samples")
            _cal_log_line("FAIL: no samples read")
            self._close_overlay()
            return
        n = float(self._cal_n)
        gx = self._cal_sum[0] / n
        gy = self._cal_sum[1] / n
        gz = self._cal_sum[2] / n
        temp = self._cal_temp / n
        # Still check: while shaking the mean still "looks like a number" but it is an
        # average polluted by motion, and storing it biases every later reading. σ is
        # insensitive to a constant bias and very sensitive to motion, so the run is
        # rejected outright.
        sds = []
        for i in range(3):
            var = self._cal_sq[i] / n - (self._cal_sum[i] / n) ** 2
            if var < 0:
                var = 0.0
            sds.append(var ** 0.5)
        worst = max(sds)
        print("[IMU] cal done: n=%d zero=(%+.3f,%+.3f,%+.3f) temp=%.2f "
              "sd=(%.2f,%.2f,%.2f)" %
              (self._cal_n, gx, gy, gz, temp, sds[0], sds[1], sds[2]))
        _cal_log_line(
            "n=%d mean=(%+.3f,%+.3f,%+.3f) sd=(%.3f,%.3f,%.3f) temp=%.2f "
            "-> %s" % (n, gx, gy, gz, sds[0], sds[1], sds[2], temp,
                       "FAIL motion (sd %.2f > %.2f dps)"
                       % (worst, CAL_MAX_SD_DPS)
                       if worst > CAL_MAX_SD_DPS else "OK"))
        if worst > CAL_MAX_SD_DPS:
            print("[IMU] cal rejected: motion sd=%.2f dps" % worst)
            beep.tone(1000)                # failure: same long beep as the accel calibration
            _cal_log_close()
            self._enter_prompt(
                i18n.t("imu.motion") % worst,
                self._from_menu)
            return
        self._zero = (gx, gy, gz, temp)
        save_gyro_zero(gx, gy, gz, temp)
        # A fresh calibration must clear the residual bias learned at runtime: _zero
        # already covers the true bias at this temperature, so keeping _bias would
        # subtract it twice and pull the attitude off.
        self._bias = [0.0, 0.0, 0.0]
        self._still_ms = None
        _cal_log_line("SAVED gyro_zero.txt")
        beep.double_tone(50, 100)          # pass: same short double beep as the accel calibration
        self._close_overlay()
        self._maybe_acc_prompt()

    # ---------- accelerometer six-face calibration state machine ----------
    def _enter_acc_prompt(self, reason, from_menu=False):
        self._state = ST_ACC_PROMPT
        self._acc_action = None
        self._from_menu = from_menu
        buttons = ((i18n.t("imu.btn_start"), 0x16A34A, "start"),
                   (i18n.t("imu.btn_back") if from_menu
                    else i18n.t("imu.btn_skip"), 0x374151,
                    "back" if from_menu else "skip"))
        self._set_overlay(
            i18n.t("imu.acc_title"),
            i18n.t("imu.acc_prompt") % reason,
            buttons)

    def _acc_reset(self):
        """Clear the six face means and return to the pose prompt of face 1."""
        self._acc_face = 0
        self._acc_means = [None] * 7
        self._pending = None
        self._state = ST_ACC_POSE
        self._acc_pose_ms = time.ticks_ms()
        self._set_overlay(
            i18n.t("imu.face") % 1,
            i18n.t("imu.pose_place") % _pose_text(0),
            ((i18n.t("imu.btn_sample"), 0x16A34A, "sample"),
             (i18n.t("imu.btn_skip_all"), 0x374151, "skip")))

    def _acc_begin_face(self, now):
        """Sample/OK on the current face: short countdown to let the hand leave, then
        sample."""
        self._state = ST_ACC_COUNTDOWN
        self._acc_deadline = time.ticks_add(now, ACC_FACE_COUNTDOWN_MS)
        self._acc_t0 = now

    def _acc_start_sample(self, now):
        self._state = ST_ACC_SAMPLE
        self._acc_t0 = now
        self._acc_deadline = time.ticks_add(now, ACC_FACE_SAMPLE_MS)
        self._acc_sum = [0.0, 0.0, 0.0]
        self._acc_gsum = [0.0, 0.0, 0.0]
        self._acc_sq = [0.0, 0.0, 0.0]
        self._acc_temp = 0.0
        self._acc_n = 0
        self._acc_move = 0
        self._acc_last_pct = -1

    def _acc_sample_tick(self, now):
        """Sample one frame: accumulate statistics + still check. Returns False when
        this face must be retried."""
        try:
            s = self.imu.read_sample()
        except BaseException as error:
            print("[IMU] acc read failed: {}".format(error))
            return True
        ax, ay, az = s[0], s[1], s[2]
        gx, gy, gz = s[3], s[4], s[5]
        self._acc_sum[0] += ax
        self._acc_sum[1] += ay
        self._acc_sum[2] += az
        self._acc_sq[0] += ax * ax
        self._acc_sq[1] += ay * ay
        self._acc_sq[2] += az * az
        if self._zero is not None:
            gx -= self._zero[0]
            gy -= self._zero[1]
            gz -= self._zero[2]
        # Still check: a calibrated gyro magnitude above the threshold counts as one
        # violation; a hand holding the board on an edge triggers it. An uncalibrated
        # gyro carries ±10dps of bias and breaks the test -- then stillness relies on
        # the acceleration sd below only (so the accelerometer can still be calibrated
        # after skipping the gyro).
        if self._zero is not None and \
                (gx * gx + gy * gy + gz * gz) > ACC_GYRO_STILL_DPS * \
                ACC_GYRO_STILL_DPS:
            self._acc_move += 1
        self._acc_temp += s[6]
        self._acc_n += 1
        elapsed = time.ticks_diff(now, self._acc_t0)
        pct = min(100, elapsed * 100 // ACC_FACE_SAMPLE_MS)
        step_pct = pct // 5 * 5
        if step_pct != self._acc_last_pct:
            self._acc_last_pct = step_pct
            self._set_overlay(
                i18n.t("imu.face") % (self._acc_face + 1),
                i18n.t("imu.sampling") % (_pose_text(self._acc_face), pct),
                None)
        return time.ticks_diff(self._acc_deadline, now) > 0

    def _acc_face_ok(self):
        """End of a face: still check + store the mean + write the log. Returns (ok, reason).

        Each face's mean, per-axis standard deviation, motion violations and sample
        count go to the log, and on failure reason names the criterion that failed (too
        few samples / motion / an axis over sd), pointing straight at the face.
        """
        face = self._acc_face
        n = self._acc_n
        means = [self._acc_sum[i] / n if n else 0.0 for i in range(3)]
        sds = []
        for i in range(3):
            var = self._acc_sq[i] / n - means[i] * means[i] if n else 0.0
            if var < 0:
                var = 0.0
            sds.append(var ** 0.5)
        head = ("face %d (%-16s) n=%d move=%d temp=%.2f | mean=(%+.4f,%+.4f,"
                "%+.4f) sd=(%.4f,%.4f,%.4f)" %
                (face + 1, ACC_POSE_PROMPTS[face], n, self._acc_move,
                 self._acc_temp / n if n else 0.0,
                 means[0], means[1], means[2], sds[0], sds[1], sds[2]))
        reason = ""
        if n < ACC_MIN_SAMPLES:
            reason = i18n.t("imu.fail_samples") % (n, ACC_MIN_SAMPLES)
        elif self._acc_move * 10 > n:
            # More than 10% offending frames rejects (an edge pose allows some wobble).
            reason = i18n.t("imu.fail_motion") % (
                self._acc_move, n, ACC_GYRO_STILL_DPS)
        else:
            for i in range(3):
                if sds[i] > ACC_MAX_SD_G:
                    reason = i18n.t("imu.fail_sd") % (
                        "XYZ"[i], sds[i], ACC_MAX_SD_G)
                    break
        if not reason:
            # Pose check: gravity must land on the axis expected for this face (with
            # sign). On a wrong face that axis reads ≈0, and reporting it right away
            # with the correct pose is far more useful than failing later in the pairing
            # step (measured: a wrong axis assumption produced scale=0.0018).
            axis, sign = ACC_FACE_EXPECT[face]
            dom = sign * means[axis]
            if dom < ACC_FACE_MIN_DOM_G:
                reason = (i18n.t("imu.fail_face")
                          % ("XYZ"[max(range(3), key=lambda i: abs(means[i]))],
                             "XYZ"[axis], "+" if sign > 0 else "-", dom))
        if reason:
            _cal_log_line(head + " -> " + reason)
            return (False, reason)
        self._acc_means[face] = (means[0], means[1], means[2])
        _cal_log_line(head + " -> OK")
        return (True, "")

    def _acc_next_face(self, now):
        self._acc_face += 1
        self._acc_pose_ms = now
        if self._acc_face >= 6:
            # Six faces done -> verification re-test (screen up, same pose as 2/6, index 6)
            self._acc_face = 6
            self._state = ST_ACC_POSE
            self._set_overlay(
                i18n.t("imu.verify"),
                i18n.t("imu.verify_body") % _pose_text(6),
                ((i18n.t("imu.btn_sample"), 0x16A34A, "sample"),
                 (i18n.t("imu.btn_skip"), 0x374151, "skip")))
            return True
        self._state = ST_ACC_POSE
        self._set_overlay(
            i18n.t("imu.face") % (self._acc_face + 1),
            i18n.t("imu.pose_place") % _pose_text(self._acc_face),
            ((i18n.t("imu.btn_sample"), 0x16A34A, "sample"),
             (i18n.t("imu.btn_skip_all"), 0x374151, "skip")))
        return True

    def _acc_compute(self):
        """Compute bias/scale from the six face means. Returns (bias[3], scale[3], temp) or None.

        Each pair (plus face / minus face / axis) logs its raw readings and the
        resulting bias/scale; a scale outside [0.5,1.5] marks that pair as bad (usually
        a misplaced face or a polluted reading), and the reason goes into self._acc_why
        so the dialog and the log can point at it.
        """
        m = self._acc_means
        for ip, im, axis in ACC_PAIRS:
            if m[ip] is None or m[im] is None:
                self._acc_why = i18n.t("imu.why_missing") % (ip + 1, im + 1)
                _cal_log_line("compute FAIL: " + self._acc_why)
                return None
        bias = [0.0, 0.0, 0.0]
        scale = [1.0, 1.0, 1.0]
        for ip, im, axis in ACC_PAIRS:
            # This axis on the + face (signed, should be ≈+1g) and on the - face (≈-1g).
            ap = m[ip][axis]
            am = m[im][axis]
            bias[axis] = (ap + am) / 2.0
            s = abs(ap - am) / 2.0
            _cal_log_line(
                "pair axis %s: face%d=%+.4f face%d=%+.4f -> bias=%+.4f "
                "scale=%.4f" % ("XYZ"[axis], ip + 1, ap, im + 1, am,
                                bias[axis], s))
            if s < 0.5 or s > 1.5:
                self._acc_why = (i18n.t("imu.why_scale") %
                                 ("XYZ"[axis], s))
                _cal_log_line("compute FAIL: " + self._acc_why)
                return None
            scale[axis] = s
        temp = self._acc_temp / max(1, self._acc_n)
        _cal_log_line("bias=(%+.4f,%+.4f,%+.4f) scale=(%.4f,%.4f,%.4f) "
                      "temp=%.2f" % (bias[0], bias[1], bias[2],
                                     scale[0], scale[1], scale[2], temp))
        return (bias, scale, temp)

    def _acc_verify(self, mean):
        """Check the verification face (screen up) against the fresh bias/scale. Returns
        (mag_err, h_err)."""
        b, s, _ = self._pending
        ax = (mean[0] - b[0]) / s[0]
        ay = (mean[1] - b[1]) / s[1]
        az = (mean[2] - b[2]) / s[2]
        mag = (ax * ax + ay * ay + az * az) ** 0.5
        return (abs(mag - 1.0), max(abs(ax), abs(ay)))

    def _acc_save_and_done(self):
        b, s, temp = self._pending
        self._acc_cal = (b, s, temp)
        save_acc_cal(b, s, temp)
        _cal_log_line("SAVED acc_cal.txt")
        _cal_log_close()
        self._state = ST_ACC_DONE
        self._acc_deadline = time.ticks_add(time.ticks_ms(), ACC_DONE_MS)
        mag_err, h_err = self._acc_verify(self._acc_means[6]) \
            if self._acc_means[6] is not None else (0.0, 0.0)
        self._set_overlay(
            i18n.t("imu.saved"),
            i18n.t("imu.saved_body") % (mag_err * 1000.0),
            None)

    def _acc_tick(self, now):
        """Accelerometer calibration state machine. Returns True = this frame was handled."""
        if self._acc_action is not None:
            action = self._acc_action
            self._acc_action = None
            if action == "skip":
                _cal_log_line("ABORT: user skipped at face %d" %
                              (self._acc_face + 1))
                _cal_log_close()
                self._close_overlay()
                _ACC_PROMPTED[0] = True   # no auto dialog again this power cycle (not persisted)
                return False
            if action == "back":
                _cal_log_line("ABORT: user back at face %d" %
                              (self._acc_face + 1))
                _cal_log_close()
                if self._state == ST_ACC_PROMPT and self._from_menu:
                    self._enter_menu()
                else:
                    self._close_overlay()
                    _ACC_PROMPTED[0] = True
                return False
            if action in ("start", "retry") and \
                    self._state in (ST_ACC_PROMPT, ST_ACC_RESULT):
                if action == "start":
                    _cal_log_open(ACC_LOG_PATH, "accel six-face cal @ %s" %
                                  time.ticks_ms())
                    _cal_log_line("gyro zero: %s" % (
                        "none" if self._zero is None else
                        "(%+.3f,%+.3f,%+.3f)@%.2fC" % self._zero))
                self._acc_reset()
                return True
            if action == "sample" and self._state == ST_ACC_POSE:
                # Debounce: ignore presses in the short window right after the face
                # prompt (a leftover click from the previous face, or flipping while
                # still holding OK). The real settling wait is the countdown after the
                # press.
                if time.ticks_diff(now, self._acc_pose_ms) < \
                        ACC_POSE_DEBOUNCE_MS:
                    return True
                self._acc_begin_face(now)
                return True
            if action == "keep" and self._state == ST_ACC_RESULT:
                self._acc_save_and_done()
                return True

        if self._state == ST_ACC_PROMPT:
            return True

        if self._state == ST_ACC_POSE:
            return True

        if self._state == ST_ACC_COUNTDOWN:
            left = time.ticks_diff(self._acc_deadline, now)
            if left > 0:
                secs = (left + 999) // 1000
                self._set_overlay(
                    i18n.t("imu.face") % (self._acc_face + 1),
                    i18n.t("imu.hold_count") % secs, None)
                return True
            self._acc_start_sample(now)
            return True

        if self._state == ST_ACC_SAMPLE:
            more = self._acc_sample_tick(now)
            if more:
                return True
            # Sampling done: judge this face.
            if self._acc_face == 6:
                # verification face
                ok, why = self._acc_face_ok()
                if not ok:
                    self._state = ST_ACC_POSE
                    self._acc_pose_ms = now
                    self._set_overlay(
                        i18n.t("imu.verify"),
                        i18n.t("imu.verify_retry") % _pose_text(6),
                        ((i18n.t("imu.btn_sample"), 0x16A34A, "sample"),
                         (i18n.t("imu.btn_skip"), 0x374151, "skip")))
                    beep.tone(1000)
                    return True
                self._pending = self._acc_compute()
                if self._pending is None:
                    # Bad pairing (a face reading is implausible): have the user redo it.
                    # The specific pair is already in the log.
                    self._state = ST_ACC_RESULT
                    self._set_overlay(
                        i18n.t("imu.check_failed"),
                        i18n.t("imu.retry_keep") %
                        getattr(self, "_acc_why", i18n.t("imu.face_wrong")),
                        ((i18n.t("imu.btn_retry"), 0x16A34A, "retry"),
                         (i18n.t("imu.btn_back"), 0x374151, "back")))
                    beep.tone(1000)
                    return True
                mag_err, h_err = self._acc_verify(self._acc_means[6])
                _cal_log_line(
                    "verify: |a|-1g=%.4f (tol %.3f) horiz=%.4f (tol %.3f) -> %s"
                    % (mag_err, ACC_VERIFY_MAG_TOL, h_err, ACC_VERIFY_H_TOL,
                       "PASS" if (mag_err <= ACC_VERIFY_MAG_TOL and
                                  h_err <= ACC_VERIFY_H_TOL) else "FAIL"))
                if mag_err <= ACC_VERIFY_MAG_TOL and h_err <= ACC_VERIFY_H_TOL:
                    self._acc_save_and_done()
                else:
                    self._state = ST_ACC_RESULT
                    self._set_overlay(
                        i18n.t("imu.residual_high"),
                        i18n.t("imu.residual_retry") % (mag_err * 1000.0),
                        ((i18n.t("imu.btn_retry"), 0x16A34A, "retry"),
                         (i18n.t("imu.btn_keep"), 0x374151, "keep"),
                         (i18n.t("imu.btn_back"), 0x374151, "back")))
                    beep.tone(1000)
                return True
            # ordinary face
            ok, why = self._acc_face_ok()
            if ok:
                beep.double_tone(50, 100)  # face passed: short double beep (50ms on + 100ms gap)
                return self._acc_next_face(now)
            beep.tone(1000)                # failure: 1s long beep
            self._state = ST_ACC_POSE
            self._acc_pose_ms = now
            self._set_overlay(
                i18n.t("imu.face") % (self._acc_face + 1),
                i18n.t("imu.face_retry") %
                (_pose_text(self._acc_face), why or i18n.t("imu.motion_plain")),
                ((i18n.t("imu.btn_retry"), 0x16A34A, "sample"),
                 (i18n.t("imu.btn_skip_all"), 0x374151, "skip")))
            return True

        if self._state == ST_ACC_RESULT:
            return True

        if self._state == ST_ACC_DONE:
            if time.ticks_diff(self._acc_deadline, now) > 0:
                return True
            self._close_overlay()
            return False

        return False

    # ---------- main loop ----------
    def step(self):
        now = time.ticks_ms()
        # OK key: pressing it when calibrated means recalibrate (the dialog lets you
        # confirm or skip). It must be consumed every frame, otherwise the press waits
        # until after calibration finishes.
        ok_pressed = self.ctx.take_ok()
        # Accumulating fixed cadence: add SAMPLE_MS to the next deadline each frame
        # instead of "at least 20ms since last time". The latter jitters across tick
        # rounding boundaries into alternating 10ms/30ms (measured spacing min=10,
        # max=33), while the accumulating schedule clusters at 19~21ms.
        due = self._next_ms
        if due is None:
            self._next_ms = time.ticks_add(now, SAMPLE_MS)
        else:
            if time.ticks_diff(now, due) < 0:
                return
            self._next_ms = time.ticks_add(due, SAMPLE_MS)
            if time.ticks_diff(now, self._next_ms) >= 0:
                # The previous frame was blocked by another task (now late): align to
                # now+20. Keeping due+20 would sample again immediately, collapsing the
                # interval to 10ms (measured, and it happened).
                self._next_ms = time.ticks_add(now, SAMPLE_MS)

        # Calibration flow (prompt / countdown / sampling): advanced on the 20ms tick,
        # never blocking. The tables are left alone while the dialog backdrop covers
        # them, and the normal path resumes once it finishes.
        if self._state >= ST_ACC_PROMPT:
            # OK maps to the on-screen button action of the current state;
            # SAMPLE/COUNTDOWN/DONE deliberately ignore it -- interrupting during
            # sampling is the most common source of bad data.
            if ok_pressed:
                if self._state == ST_ACC_PROMPT:
                    self._acc_action = "start"
                elif self._state == ST_ACC_POSE:
                    self._acc_action = "sample"
            self._acc_tick(now)
            _push_frame()
            return

        if self._state != ST_NORMAL:
            if ok_pressed and self._state == ST_PROMPT:
                self._cal_action = "start"
            self._cal_tick(now)
            _push_frame()
            return

        if ok_pressed:
            self._enter_menu()
            _push_frame()
            return

        # Page swiping: only in the normal state (no page movement under a calibration
        # dialog, which could be triggered by accident).
        self._poll_swipe(now)

        try:
            ax, ay, az, gx, gy, gz, temp = self.imu.read_sample()
        except BaseException as error:
            self._fails += 1
            if self._fails >= FAIL_LIMIT:
                print("[IMU] imu lost: {}".format(error))
                self.ctx.report_unavailable(IMU_UNAVAILABLE)
                self._fails = 0
            return
        self._fails = 0
        self._samples += 1

        # Subtract the gyro bias: the factory tolerance is ±10dps, an order of
        # magnitude above the noise (this board's Y axis reads +7.7dps), so without it
        # the board "spins while standing still".
        if self._zero is not None:
            gx -= self._zero[0]
            gy -= self._zero[1]
            gz -= self._zero[2]

        # Subtract the accel bias then divide by sensitivity: the b/s from the six-face
        # calibration (a=(raw-b)/s). Uncalibrated, the raw value is shown as is (the
        # info row says RAW). Note the calibration samples the uncorrected raw values,
        # never the corrected ones.
        if self._acc_cal is not None:
            b, s3 = self._acc_cal[0], self._acc_cal[1]
            ax = (ax - b[0]) / s3[0]
            ay = (ay - b[1]) / s3[1]
            az = (az - b[2]) / s3[2]

        # Fixed sign ({:+.Nf}): always a + or -, so the minus never blinks; _pad then
        # widens to a fixed length and the mono font keeps every field at a constant
        # character count. The digit counts follow the device resolution (see the
        # ACC_CHARS/GYR_CHARS comments).
        values = (_pad("{:+.3f}".format(ax), ACC_CHARS),
                  _pad("{:+.3f}".format(ay), ACC_CHARS),
                  _pad("{:+.3f}".format(az), ACC_CHARS),
                  _pad("{:+.1f}".format(gx), GYR_CHARS),
                  _pad("{:+.1f}".format(gy), GYR_CHARS),
                  _pad("{:+.1f}".format(gz), GYR_CHARS),
                  _pad("{:+.2f}".format(temp), TEMP_CHARS))

        # ---- attitude estimation (Mahony) ----
        # dt is the measured interval: integrating at a nominal 20ms while redraws jitter
        # makes the angle accumulate error. It is clamped to 10~100ms so an occasional
        # long stall cannot throw the quaternion off in one step.
        if self._ahrs_ms is None:
            self._ahrs_ms = now
            dt = 0.02
        else:
            gap = time.ticks_diff(now, self._ahrs_ms)
            self._ahrs_ms = now
            if gap < 10:
                gap = 10
            elif gap > 100:
                gap = 100
            dt = gap * 0.001
        # Temperature drift compensation: learn a bit of the residual bias first, then
        # feed the filter (the readout row does not subtract it -- that row shows the
        # real sensor output minus the stored bias, so sensor drift stays visible).
        self._track_bias(gx, gy, gz, now)
        self._q = _ahrs_update(self._q,
                               gx - self._bias[0], gy - self._bias[1],
                               gz - self._bias[2], ax, ay, az, dt)
        # Euler angles taken after aligning to the display frame (nine attitude cases
        # checked on the PC side, error 0.00°)
        e = _q_euler(_qmul(self._q, Q_CORR))
        self._roll = e[0] * _RAD2DEG
        self._pitch = e[1] * _RAD2DEG
        yaw = e[2] * _RAD2DEG
        # Heading zero: without a magnetometer there is no absolute heading, only a
        # relative one physically. The attitude when entering the app is taken as 0°,
        # and rotation about the vertical axis afterwards is the relative heading --
        # otherwise the page opens on an arbitrary number (measured 179° on this board)
        # and looks broken.
        if self._yaw0 is None:
            self._yaw0 = yaw
        yaw -= self._yaw0
        # Wrap the heading into -180..180: otherwise 359°->0° jumps the curve and the
        # tape by a full turn.
        while yaw > 180.0:
            yaw -= 360.0
        while yaw < -180.0:
            yaw += 360.0
        self._yaw = yaw

        # ---- page rendering ----
        # Chart drawing pauses while switching pages (dragging or snapping): the
        # whole-screen translation already redraws every frame, and adding chart
        # refreshes drops frames. Data still accumulates (active=False) and picks up
        # naturally afterwards. **The readout row keeps refreshing**, though: a label
        # set_text is cheap, and skipping it leaves the top of the incoming curve page
        # blank until the animation ends and the numbers pop in (seen in board
        # screenshots).
        animating = self._pager.busy()

        if self._page != PAGE_DASH:
            if self._page == PAGE_RAW:
                if self._readout_vals != values:
                    self._readout_vals = values
                    for c in range(7):
                        self._readout[c].set_text(values[c])
            else:
                # The attitude page readout uses the same range as its curves
                # (-180..180): otherwise the curve says -179 while the readout says 181
                # and they look like two different quantities. Only the dashboard page
                # uses the compass-style 0..360 (the heading tape follows compass
                # convention).
                av = (_pad("{:+.1f}".format(self._roll), 6),
                      _pad("{:+.1f}".format(self._pitch), 6),
                      _pad("{:+.1f}".format(yaw), 6))
                if self._att_vals != av:
                    self._att_vals = av
                    for c in range(3):
                        self._att_readout[c].set_text(av[c])

        if animating:
            # Animating: accumulate history only, no drawing (both views must be fed or
            # the page comes back empty)
            self._raw_view.update((ax, ay, az, gx, gy, gz), now, False)
            self._att_view.update((self._roll, self._pitch, yaw), now, False)
        elif self._page == PAGE_DASH:
            # 25Hz is enough for the dashboard (attitude changes slowly); the time saved
            # goes to sampling and the attitude maths.
            if time.ticks_diff(now, self._dash_ms) >= DASH_MS:
                self._dash_ms = now
                self._update_dash(self._roll, self._pitch,
                                  yaw + 360.0 if yaw < 0 else yaw)
        # Both pages must keep accumulating history (so switching shows a full window
        # immediately). While switching, both views were already fed with active=False
        # above (history only), so this uses animating instead of asking busy() again:
        # it avoids pushing one sample twice at the boundary.
        if not animating:
            self._chart_update(ax, ay, az, gx, gy, gz, now)

        # Measured rate: refreshed once per second (also a self-check you can eyeball).
        if self._rate_ms is None:
            self._rate_ms = now
            self._rate_base = self._samples
        elif time.ticks_diff(now, self._rate_ms) >= 1000:
            elapsed = time.ticks_diff(now, self._rate_ms)
            self._rate = (self._samples - self._rate_base) * 1000 // elapsed
            self._rate_base = self._samples
            self._rate_ms = now
            self._refresh_foot()

        # Push the frame: an idle main-loop frame waits for LVGL's default 30ms period,
        # so only forcing an immediate redraw here holds 50Hz (measured: 49.7Hz on a
        # 20ms loop, max interval 20ms).
        _push_frame()

    def destroy(self):
        # The calibration log handle must be closed: otherwise a soft reset after
        # leaving the app locks the file (errno 16) and the next boot's open gets
        # EPERM.
        _cal_log_close()
        imu = getattr(self, "imu", None)
        if imu is None:
            return
        self.imu = None
        # Leave the lock mode and restore AHB clock gating before exiting (datasheet
        # 13.4), so the sensor is not left in a lock-only state for other users.
        imu.release()
        _close_i2c(imu.i2c)


def create_app(content, ctx):
    return ImuApp(content, ctx)
