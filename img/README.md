# Images

Screenshots referenced by `README.md` and `README_zh.md`, plus the shooting plan
for the ones that still need a real scene.

## Status

| File | State | Notes |
| --- | --- | --- |
| `swipe.gif` | done | The desktop showcase: 14 real pager frames stitched; 560x336, 48 colours, 385 KB |
| `folder-vision.png` | done | `Vision` folder flyout, 9 members |
| `folder-face.png` | done | `Face` folder flyout, 5 members |
| `boot-splash.png` | done | Progress bar mid model-load (`Loading models 14/16`) |
| `app-settings.png` | done | Toggles, brightness stepper, SYSTEM section |
| `app-audio.png` | done | Idle record screen |
| `app-battery.png` | done | Live reading, 3.90 V, span 4 mV |
| `app-imu.png` | done | Dashboard at roll +6 deg, horizon nearly level |
| `app-imu-raw.png` | done | Raw curves page (acceleration / gyro / temperature) |
| `app-imu-att.png` | done | Attitude page (roll / pitch / yaw curves) |
| `app-ocr.jpg` | done | Frozen frame, 12 recognised lines, FPS 12.0 |
| `app-detect.jpg` | done | YOLOv8n 320, one `mouse` at 0.69, FPS 7.8 |
| `app-plate.jpg` | done | `京AF0236`, 11-13 ms inference, FPS 8.8 |
| `app-face-points.jpg` | done | Mesh view, front-on face, 106 landmarks locked, FPS 9.4 |
| `app-pose.jpg` | done | YOLO11n-pose, 17 keypoints, 1 person, 25 ms/frame, FPS 9.5 |
| `app-gesture.jpg` | done | Classified `five`, 21 hand keypoints, 80 ms/frame, FPS 6.2 |
| `app-gesture-yeah.jpg` | done | Classified `yeah`, same pipeline |
| `app-face-det.png` | optional | Same scene as above, box overlay |
| `app-gallery.png` | **pending** | Needs photos that are publishable |
| `app-network.png` | **pending** | Needs a neutral SSID list |
| `app-touch.png` | optional | Empty until fingers are on the panel |
| `device.png` | **pending** | Physical photo, front three-quarter view |
| `hardware-ports.png` | **pending** | Physical photo, port/button callout |

## How the finished ones were captured

Two routes, both already scripted:

- **Flat screens (folders, settings, audio, battery, IMU, splash)** —
  LVGL snapshot, per-row RLE, base64 over serial:
  ```powershell
  python tools/probes/_cap_run.py tools/probes/_cap_readme_ui.py tmp/ui.txt COM56
  python tools/decode_lv_snapshot.py tmp/ui.txt "tmp/readme_%s.png"
  ```
  RLE is right for these: a flat 800x480 frame is about 46 KB.
- **Camera preview frames** — RLE expands a live frame past 1 MB and raises
  `MemoryError`, so the snapshot goes through the firmware JPEG encoder and lands
  in `/data/capture/<tag>.jpg`, then is pulled once:
  ```powershell
  python tools/_run_remote.py COM56 921600 --lvgl tools/probes/_cap_readme_vision.py
  python tools/_pull_file.py COM56 921600 /data/capture/detect.jpg img/app-detect.jpg
  ```
  The JPEG accessor on this firmware is `img.compressed(85).bytearray()`;
  `to_bytes()` does not exist and `save()` refuses a JPEG source.

A snapshot reads the LVGL buffer, not the panel, so it works with the backlight
off and shows exact pixels with no glare. Use a real camera only for
`device.png` and `hardware-ports.png`.

### The swipe animation

`swipe.gif` is not a screen recording. One snapshot costs 1-2 s on this board
(snapshot, channel swap, JPEG encode, serial), so a 180 ms animation can never be
sampled live. Instead:

1. `tools/probes/_cap_readme_swipe.py` scrolls the production pager to 14 known
   offsets and snapshots each one.
2. `tools/make_swipe_gif.py` re-times them (55 ms per frame, 900 ms hold on the
   last) and freezes the status bar to the first frame so the clock does not jump
   between captures taken a minute apart.

The grid content of every frame is a real render at a real offset; only the timing
is reconstructed. The status bar and page dots are left out of the scroll, exactly
as the launcher does it. Do not regenerate this from two PNGs by translating them:
the bar and dots would move too, which the real UI never does.

## Scenes still needed

Place the object, run the matching command, and the frame is captured as-is.
Each entry names the app and what has to be in front of the lens.

| Target | Scene to set up | Why it matters |
| --- | --- | --- |
| `app-face-det.png` | Same setup as above, captured before the Points run | Box plus 5 landmarks |
| `app-gallery.png` | Four or more captured photos that are safe to publish | Grid layout with thumbnails |
| `app-network.png` | The board joined to a Wi-Fi whose SSID and IP are safe to publish, or accept a blurred right panel | Avoids leaking a real network |

Practical notes:

- Use a stand or rest the board: a moving frame puts motion blur inside the boxes.
- Avoid a bright window behind the subject; the AI channel meters the preview and
  pushes the subject into shadow.
- Do not photograph the panel with a phone for these. The on-board capture is the
  exact UI including overlays, and needs no straightening.
- **Any image showing a person needs that person's consent before the repo goes
  public.** `app-face-points.jpg` and `app-pose.jpg` both contain a recognisable
  face; the gesture images show a hand only. Confirm consent, or replace the
  subject, or crop/blur the face before publishing.
- A gesture frame is only valid while its label is locked. The probe snaps the
  picture within a frame or two of the classifier holding the expected label;
  capturing later shows `--` in the bar even though the skeleton was right.

## Order to run

1. Reset the board before the run if the GUI is on a camera app: the sensor
   singleton survives a soft reset, so `_cap_*` probes fail with
   `sensor(N) is already inited` unless `tools/probes/_reset_hard.py` runs first.
2. `_cap_readme_vision.py` opens the apps in the order `face_det -> detect ->
   pose -> ocr -> plate` and writes one JPEG per app. Every scene must be in
   place for the whole run; otherwise trim the target list to what is ready.
3. `_pull_file.py` moves each JPEG into `img/` under the README name.
4. Re-run the README link check. A missing file means a step was skipped.

Every app closes in `finally`, so an aborted run still releases the sensor. If the
GUI stops responding afterwards, soft reset once before the next run.

## Format rules

- 800x480 native. PNG for flat UI, JPEG (quality 85) for camera frames.
- Keep each file under about 400 KB; `pngquant` for PNG, quality 85 for JPEG.
- No text or arrows burned into an image. Captions belong in Markdown.
- File names are lower-case and hyphen-separated, and both READMEs reference
  them, so renaming a file means editing two documents.
