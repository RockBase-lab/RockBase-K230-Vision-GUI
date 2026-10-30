# Board hardware

Device-level facts for this board: what is fitted, how it is wired, and which
register or pin values were verified by measurement. These change only with a
board revision, so they are stated as facts rather than as procedures.

## Display and touch

- Panel: ST7701, 800x480, mounted rotated opposite to the SDK default
  landscape orientation. Corrected with `Display.FLAG_ROTATION_270` (see
  `docs/lvgl-binding.md`).
- Touch: FT5316, five-point, read with `machine.TOUCH`. Its default rotation
  already matches the corrected display, so no extra rotation argument is
  needed.
- The driver reports 4-bit `track_id` values per contact.

## Physical buttons

Two buttons, both polled by the GUI main loop at about 5 ms with 30 ms
debounce:

| Button | GPIO | Pull | Active | Idle | Pressed |
|---|---|---|---|---|---|
| CANCEL | GPIO0 | up | low | 1 | 0 |
| OK | GPIO53 | down | high | 0 | 1 |

GPIO53's other side is wired to 3.3 V. With a pull-up it reads stuck at 1, so
PULL_DOWN is required. An earlier note calling GPIO53 a status LED was wrong.

## Buzzer

- Passive buzzer on **IO43**, alternate function 2 (PWM1).
- `PWM(43, freq=2000, duty=50)` works directly; `duty(0)` silences and
  `deinit()` releases it.
- Useful band is 2-6 kHz. The 2 kHz resonance is loudest and cleanest; **1 kHz
  is inaudible** on this part.
- Recommended click: 2 kHz, 50 % duty, 60-80 ms, throttled to a 60-120 ms
  minimum interval.
- There is no volume control, only frequency response. An on/off preference
  must be persisted to `/data/config/gui.json`.
- Note that the PWM worker cannot silence the tone during a long blocking C
  call: the GIL is held and nothing Python runs. See
  `docs/performance-budget.md`.

## IMU: QMI8658A

- Address **0x6B** (AD0 = 1). A scan also shows 0x38, which is not the IMU.
- Bus: I2C3, SCL = GPIO44, SDA = GPIO45, 400 kHz.
  `fpioa.set_function(44, FPIOA.IIC3_SCL)` works (IIC3_SCL = 98, IIC3_SDA = 99).
- `WHO_AM_I` (0x05) reads 0x05. Construction plus soft reset plus
  configuration takes about 180 ms.
- `machine.I2C` has **no `deinit()`**; drop the reference instead.
- The **first read is always all zeros** — the device is not ready on the first
  frame after power-up.

### configuration in use

| Axis | Range | CTRL register | LSB |
|---|---|---|---|
| Accel | ±4 g | CTRL2 = 0x13 (aFS[6:4] = 001) | 8192 LSB/g, 122 µg/LSB |
| Gyro | ±512 dps | CTRL3 = 0x53 | 64 LSB/dps |

Changing a range requires changing both the CTRL field and the corresponding
scale constant. Getting one without the other produces a factor-of-two error;
verify with `|a| ≈ 1.000 g` when the board lies flat.

### device specifications (datasheet QST 13-52-25 Rev A)

- Accel full-scale LSB/g: ±2 g = 16384, ±4 g = 8192, ±8 g = 4096,
  ±16 g = 2048.
- Accel noise density 150 µg/√Hz; initial offset ±100 mg at board level;
  sensitivity tolerance ±6 %.
- Gyro ±512 dps = 64 LSB/dps; noise 13 mdps/√Hz; **initial offset ±10 dps at
  board level** (not ±0.5); sensitivity tolerance ±3 %; g sensitivity
  ±0.1 dps/g.
- Temperature: 256 LSB/°C, refreshed at only **8 Hz**, no absolute accuracy
  spec.
- In 6-DOF mode the ODR follows the gyro: ODR = 3 gives **896.8 Hz**, not
  1000. The default filter bandwidth is 27.5 % of ODR = 246.6 Hz; LPF mode 11
  is 13.37 % = 119.9 Hz.
- Gyro turn-on time is 150 ms + 3/ODR ≈ 153 ms. Data from the first ~150 ms
  after configuration is invalid, so a 30 ms settle is not enough.

### measured accuracy on this board

Board flat, screen up, locking enabled:

- `|a| = 1.0093 g` (+0.93 %, well inside the ±6 % tolerance).
- Accel noise SD 1.7-3.0 mg (theory 2.36 mg).
- Gyro noise X 0.188 / Y 0.389 / Z 0.197 dps (theory 0.204; Y is about 1.9×,
  which the LPF does not remove and which does not correlate with accel —
  suspected 1/f noise or board-level mechanical coupling).
- Gyro zero offset X −2.22 / Y **+7.65** / Z +0.12 dps, all inside the ±10 dps
  board tolerance, with Y near the limit.
- Temperature 48.6 °C is die temperature; 20-25 °C ambient is normal.

### axis orientation

With the board flat and screen up, mean accel is `(−0.021, +0.020, −1.009) g`,
so the axis angles are X 91.2° / Y 88.8° / Z 178.3°. The sensor **−Z axis
points up**. An earlier 35.8° tilt reading came from the board being propped
up, not from a mounting tilt.

For display orientation with the BOTTOM edge down as the neutral pose, apply
`q_disp = q ⊗ Q_CORR` with `Q_CORR = (0, −0.7071, 0, −0.7071)`. Nine poses
verified on PC with 0.00° error.

### register-level read tearing and the fix

Reading the six data bytes one at a time lets the high byte come from the next
sample. The error is exactly 256 LSB, i.e. 4 dps or 15.6 mg. A histogram
clusters at ±256 LSB and the sample-to-sample difference hits exactly 256
(12 of 799 samples), which is the fingerprint.

A burst read does not fix this — the sensor keeps updating registers during the
burst, so it is three times faster but the outliers remain.

The correct fix is the datasheet's section 13 **locking (shadow register)**
sequence:

1. Disable AHB clock gating for I2C: `CAL1_L (0x0B) = 0x01`, then
   `CTRL9 (0x0A) = 0x12`, poll `STATUSINT (0x2D)` bit 7 for `CmdDone`, then
   `CTRL9 = 0x00` to acknowledge.
2. `CTRL7 = 0x83` (bit 7 `syncSmpl`, plus `aEN` and `gEN`).
3. Read `STATUSINT`; `Avail` (bit 0) = 1 triggers the lock. After `Locked`
   (bit 1) = 1, burst-read `temp (0x33, 2 B)`, `acc (0x35, 6 B)`,
   `gyr (0x3B, 6 B)`. Reading `GZ_H (0x40)` unlocks automatically.

Measured with the lock enabled: gyro Z peak-to-peak 7.97 → 1.13 dps, max
`|delta|` 256 → 47 LSB, samples at or above 200 drop from 12 to 0 out of 399,
and 886 Hz is sustained with no stall.

Reported precision should match device resolution: accel LSB is 61 µg with
2 mg noise, so four decimals are 1/20 of the noise — three decimals is right;
gyro LSB is 15.6 mdps with 0.2-0.5 dps noise, so one decimal suffices.

## Battery measurement

- Divider: 1.5 kΩ to the battery, 1 kΩ to ground, tap at the midpoint into
  ADC5. Ratio is 2/5, so `battery_mV = adc_uv * 5 / 2 / 1000`.
- Kept as an integer fraction (numerator 5, denominator 2) to avoid float mV.
- **The divider value has changed twice** (1k+1k → 3k+1k → 1.5k+1k).
  Always confirm the current ratio with hardware before relying on it.
- Vref correction: `machine.ADC.read_uv()` assumes an ideal 1.80 V reference,
  but this board's 1.8 V rail measures 1.88 V, so raw `read_uv()` is about
  4.4 % low. Scale immediately after `read_uv()`:
  `value * 1880 // 1800`. `BATTERY_ADC_VREF_ACTUAL_MV` is a per-board
  measurement, not a universal constant, and must be re-measured when the board
  revision or batch changes.

## Digital microphone (PDM/DMIC)

- `input_device_index=1` is mandatory when opening the stream; `dev=0` fails
  with `kd_mpi_ai_set_pub_attr failed:-1`.
- `read(chn=group)` must be called for **every** channel group each frame, even
  when only one group is kept. The PDM hardware pushes data to all groups
  regardless of physical wiring; skipping reads fills their internal buffers
  and eventually blocks the whole pipeline (symptom: recording hangs near the
  end).
- Only **group 1** carries real microphone audio; groups 0, 2 and 3 are silent
  or noise (confirmed by dumping all four groups × L/R on this board).
- The microphone sits on **group 1, lane 1** (GPIO28 = PDM_IN1; the mic L/R pin
  is tied high, hence the right channel). Measured peak per group: groups 0/2/3
  saturate around 32749 (floating noise), group 1 about 2063 (6.3 % FS).
- Samples are **left-aligned**: the high 16 bits of each 32-bit sample are the
  s16 value, so take bytes [6] and [7]; no shift needed.
- `channels` may be 2/4/6/8, giving 2/4/6/8 ÷ 2 groups. **`channels=4` is
  enough** (two groups) at half the read cost of 8.
- `frames_per_buffer=320` makes `read()` block **20.0 ms**; 640 blocks 40.0 ms.
  This is the hardware rhythm, not CPU cost.
- Skip the first four reads per group after opening (`DMIC_WARMUP_READS = 4`);
  the PDM startup transient otherwise skews peak-based automatic gain downward.
- **Never enable `stream.enable_audio3a(AUDIO_3A_ENABLE_ANS)` while recording.**
  Measured: it turns the voice into a strong musical/electrical noise, peak up
  about 7×, crest factor 27, zero-crossing rate about 417 Hz, drowning the
  voice. This was the real cause of "recording is all electrical noise", not
  channels, DC offset, ulab or playback.
- Raw PDM output carries a large DC offset and heavy sub-bass: measured
  DC = −629 (−1.9 % FS) and 77-99 % of the energy below 50 Hz, while the voice
  band above 500 Hz is only 0.2 % FS. Normalising without processing gives
  rumble plus a thump, so a first-order DC blocker is required (R = 0.995;
  cutoff ≈ 12.7 Hz at 16 kHz).
- Raising `DMIC_INPUT_VOLUME` from 35 to 70 does **not** increase captured peak
  (it fell 5.4 % → 3.8 %). The stream's `volume()` is not a linear PGA gain
  here; do not assume raising it helps.

## Audio amplifier

- PA enable pin is **GPIO71**. The amplifier is an NS4150B in BTL, so playback
  must be differential stereo `L = +s, R = −s`; in phase the outputs cancel.
- Measured with a scope: gain ×12 (BTL). At 1 kHz on the sweep page, 50 % FS
  gives 360 mV single-ended (slope about 720 mV/FS at volume 95) and 100 % FS
  gives 390 mV because the codec analog stage clips there.
- Constraint: single-ended output must stay at or below 350 mV or the speaker
  saturates. Hence `TARGET_PEAK_PCT = 48` (350 / 720 ≈ 48.6 %, rounded down for
  margin, about 346 mV single-ended).
- `audio.tone` is calibrated with `FS_MV = 1440` (differential full scale) and
  `AMP_MAX_MV = 700` (= 350 × 2, the red line), so the sweep page mV reading is
  the true differential amplifier input.
- Board regression: output peak 48.0 % FS, clipped 0, RMS +19.7 dB over raw,
  plus another 2 dB after compression.
- Playback: `PLAY_VOLUME = 95` is the calibration reference — changing it
  requires recalibrating. `PLAY_GAIN = 1.0` is final because the peak is
  already calibrated to the 48 % FS / 350 mV red line and any software gain
  above 1.0 pushes past the codec's ~390 mV saturation point.

## Camera

- Module: `gc2093_csi2` (type 33, 1920x1080 at 60 fps), **fixed focus, no VCM**.
- Every app configures its channels through `factory/gui/camera_fov.set_preview`,
  which passes an explicit window at the output aspect: 800x480 gets
  `crop=(60, 0, 1800, 1080)`, so 93.75% of the width and 100% of the height
  survive, scaled 2.25:1 with no distortion. The firmware's own `crop=True`
  picks the largest integer downscale instead and keeps only 1600x960, i.e.
  83.3% x 88.9% and 74% of the scene. A photo has no crop at all, so it still
  contains slightly more scene than the viewfinder.
- The window width is not a multiple of 16 (1800 = 8 x 225) and the firmware
  accepts it. Measured at 800x480: 34.95 ms per snapshot against 34.95 ms for
  `crop=True`, and a test target covers 0.883x the pixel width, matching
  1600/1800. The window costs nothing; it trades subject size for field of view,
  so a subject covers 11% fewer pixels and, in a 320 model, has 11% less
  detection range.
- `camera_fov.set_preview` falls back to `crop=True` when the firmware rejects a
  window, and prints `[FOV]` rather than letting the AssertionError surface as
  the app's "camera unavailable" panel.
- The firmware exposes `auto_focus`, `focus_pos` and `focus_caps`, but after
  `sensor.run()` `focus_caps()` returns `(0, 0, 0)` and `focus_pos()` returns
  `None`; `auto_focus(True)` before `run()` returns True but is a silent no-op.
  There is no `set_focus`. Do not add autofocus expecting an effect.
- `focus_caps` raises "can't get sensor fd" before `run()`; `auto_focus` raises
  "call should before Sensor.run()" after it.
- A missing camera raises `RuntimeError` in the `Sensor(fps=30)` constructor
  (from `kd_mpi_sensor_adapt_get_ex`), failing in about 11 ms rather than
  hanging. With a camera fitted the constructor takes about 249 ms.
- Only one `Sensor()` can be constructed per MicroPython VM lifetime. See
  `docs/reset-matrix.md`.

## Video decoding

`app_video.py` is not part of this repository. The findings are recorded so a
future port does not repeat the failures.

- Pipeline: `kd_mp4_*` demux → `media.vdecoder.Decoder` →
  `Display.bind_layer(LAYER_VIDEO1)`. **Do not use `media.player.Player`**: it
  calls `Display.init` and `MediaManager.init/deinit` itself and tears down the
  launcher's display.
- `MediaManager.init()` must be called exactly once and **never deinitialised**,
  or the LVGL draw buffers go with it.
- The decoder pool is allocated at `MAX_WIDTH = 1920 x MAX_HEIGHT = 1088`
  regardless of stream resolution: four input buffers of 2088960 B plus six
  output buffers of 4177920 B, about 33 MB. Decoding itself is fast (45 frames
  in 241 ms ≈ 187 fps), so pacing must be done manually against presentation
  timestamps.
- `kd_mp4_get_frame` timestamps are in **microseconds**, as is
  `k_mp4_file_info_s.duration`.
- Codec constants live in `mpp.mp4_format_struct`; `mpp.mp4_format` only has the
  `kd_mp4_*` functions. The enum covers H264/H265/G711A/G711U/OPUS only — AAC
  and MP3 tracks yield no valid codec id.
- There is no seek: only `create`, `destroy`, `create_track`,
  `destroy_tracks`, `write_frame`, `get_frame`, `get_file_info`,
  `get_track_by_index`.
- `mpp.sys` does not export `VIDEO_DECODE_MOD_ID`, `VDEC_DEV_ID` or
  `NONAI_2D_CSC_MOD_ID`, so a vdec → CSC capture path cannot be assembled.
- `Display.writeback` / `writeback_dump(timeout)` work when initialised with
  `to_ide=True` and return a `py_video_frame_info`; `.v_frame.to_image()` gives
  the 480x800 physical frame (panel native orientation). **The chroma planes of
  a WBC frame are horizontally misaligned by about 64 px**, so do not do
  pixel-level colour work; the Y plane is accurate and is fine for structural
  checks.
- Video layers follow `Display.init(flag=FLAG_ROTATION_270)`. Verified with
  four-quadrant solid colour plus Y-plane values: physical-to-logical mapping is
  `logical_x = 800 - phys_y, logical_y = phys_x`, quadrant order preserved, so
  no extra orientation or mirroring is needed.
- LVGL draws OSD0, so the screen background must be made transparent:
  `content.get_parent().set_style_bg_opa(lv.OPA.TRANSP, 0)`. With a transparent
  screen LVGL no longer clears the background, so a semi-transparent bar
  accumulates ghosted text from the previous frame. Use `OPA.COVER` for the top
  and bottom bars and `invalidate()` the whole bar when its text changes.

Supported: MP4 container, H264/H265 at exactly 800x480, 8-bit SDR, video-only
(audio tracks silent). Not supported and not mitigable: AV1, VP9, MPEG4-Part2,
MKV/AVI/WebM/TS containers, any other resolution, 10-bit/HDR, AAC audio.
