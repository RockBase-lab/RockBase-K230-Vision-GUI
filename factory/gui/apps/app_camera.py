# Camera app: full-screen immersive viewfinder with hardware-key capture.
#
# Display path: chn2 (800x480, RGB888) is fed into an LVGL lv.img widget via
# as_lvgl_img_src, so the picture is composited by LVGL like every other app
# instead of taking over a display layer.
#
# Capture path: chn0 runs at the sensor's native 1920x1080. Taking a photo
# snapshots chn0 and encodes it to JPEG, which keeps the saved image at full
# resolution while the viewfinder stays smooth at display resolution.
# Measured on-board: dual-channel works with ~3.9MB heap free, to_jpeg takes
# ~420ms and yields ~265KB at quality 90.
#
# Keys: CANCEL (GPIO0) exits, handled by the launcher. OK (GPIO53) captures,
# consumed here through ctx.take_ok().
#
# Shutter feedback: pressing OK plays a synthesized "ka-cha" (see audio/shutter.py)
# and shows a full-screen 20% white overlay for FLASH_FRAMES rendered frames
# (~60ms at LVGL's 30ms refresh period). Both are driven from the frame loop,
# not from inside the capture call, because _capture() blocks ~0.5s and nothing
# is flushed while it runs.
#
# Note: tapping the screen deliberately does nothing - the whole area is the
# viewfinder, and an accidental tap used to throw the user back to the desktop.

import gc
import os
import sys
import time

import lvgl as lv
from media.sensor import Sensor, CAM_CHN_ID_0, CAM_CHN_ID_2

import config
from audio import shutter
from camera_fov import set_preview
from apps.error import (AppUnavailable, CAMERA_UNAVAILABLE, NoSignalCounter,
                       close_sensor)

# Photo output directory. Use /data rather than /sdcard: /data on this board is the
# larger data partition, while /sdcard only holds GUI assets (fonts/icons/tile cache).
# Large files (photos, music, ...) all go to /data so the asset partition is never filled.
SAVE_DIR = "/data/photos"
SAVE_PREFIX = "IMG_"
JPEG_QUALITY = 90             # measured ~265 KB at 1920x1080; raise to 99 for higher quality
TOAST_MS = 5000               # how long the save toast stays up
TOAST_COLOR = 0x00FF00        # toast text colour (green)
TOAST_W = 760                 # toast max width: overlong paths wrap instead of running off screen
CAPTURE_W = 1920              # capture resolution (sensor native)
CAPTURE_H = 1080

FLASH_FRAMES = 2              # frames the white flash lasts (LVGL redraws every ~30 ms, hence frames)
FLASH_PCT = 20                # white flash overlay opacity (%)


def _opa(pct):
    """Get an OPA enum: prefer the exact step, fall back to a nearby one when the binding
    lacks that member, and finally to fully transparent.

    Never raises: when the enum member is unavailable it is better for the flash to be
    invisible than for photo capture to break.
    """
    for name in ("_" + str(pct), "_30", "_20", "_10"):
        value = getattr(lv.OPA, name, None)
        if value is not None:
            return value
    return getattr(lv.OPA, "TRANSP", 0)


def _font(*names):
    """Look up built-in fonts by candidate name and return the first available one (font
    names differ between bindings)."""
    for name in names:
        font = getattr(lv, name, None)
        if font:
            return font
    return None


def _ensure_dir(path):
    """Create directories level by level (CanMV's os module has no os.makedirs)."""
    current = ""
    for part in path.strip("/").split("/"):
        current += "/" + part
        try:
            os.mkdir(current)
        except OSError:
            pass


class CameraApp:
    def __init__(self, content, ctx):
        self.ctx = ctx
        # Shutter sound is a Settings preference. A muted session leaves the OK
        # key's buzzer click enabled instead, so the press still has feedback.
        shutter.set_muted(not config.get_bool("camera.shutter_sound", True))
        ctx.ok_beep = shutter.muted()
        self.sensor = None
        self.frame_count = 0
        self.last_fps_ms = time.ticks_ms()
        self.save_num = 0
        self.toast_until = 0
        # Photo state machine (see step): 0=idle, >0=frames of white flash left, -1=capture
        # on the next frame
        self.shot_phase = 0
        # Runtime disconnect detection: if the camera is unplugged or a cable works loose,
        # snapshot() raises every frame; once enough consecutive failures pile up, report
        # "unavailable" to the launcher (panel + Cancel back to the desktop).
        self._no_signal = NoSignalCounter()

        self.img_w = ctx.width
        self.img_h = ctx.height

        self.img_widget = lv.img(content)
        self.img_widget.set_size(self.img_w, self.img_h)
        self.img_widget.align(lv.ALIGN.CENTER, 0, 0)

        # Shutter white flash overlay: full-screen pure white at FLASH_PCT% opacity, hidden
        # normally.
        # Built after the image and before the labels, so the resolution/FPS text and the
        # save toast are not covered.
        self.flash = lv.obj(content)
        self.flash.set_size(self.img_w, self.img_h)
        self.flash.align(lv.ALIGN.CENTER, 0, 0)
        self.flash.set_style_bg_color(lv.color_hex(0xFFFFFF), 0)
        self.flash.set_style_bg_opa(_opa(FLASH_PCT), 0)
        self.flash.set_style_border_width(0, 0)
        self.flash.set_style_radius(0, 0)
        self.flash.set_style_pad_all(0, 0)
        # The overlay must not eat touch events (this app ignores taps, but do not get in
        # the way of interactions added later)
        try:
            self.flash.clear_flag(lv.obj.FLAG.CLICKABLE)
        except BaseException:
            pass
        self.flash.add_flag(lv.obj.FLAG.HIDDEN)

        # Top centre: resolution | FPS
        self.stat_label = lv.label(content)
        self.stat_label.set_text("camera init...")
        self.stat_label.set_style_text_color(lv.color_hex(0x00FF00), 0)
        self.stat_label.align(lv.ALIGN.TOP_MID, 0, 8)

        # Centre toast: hidden initially, shown for TOAST_MS after a capture
        self.toast = lv.label(content)
        self.toast.set_text("")
        self.toast.set_style_text_color(lv.color_hex(TOAST_COLOR), 0)
        self.toast.set_style_bg_color(lv.color_hex(0x000000), 0)
        self.toast.set_style_bg_opa(lv.OPA._80, 0)
        self.toast.set_style_radius(14, 0)
        self.toast.set_style_pad_all(20, 0)
        # Width follows the text: label width defaults to LV_SIZE_CONTENT, so the
        # background and rounded corners hug the text.
        # Only a max_width cap is set, to stop overlong paths running off the screen (the
        # label wraps once it exceeds it).
        # Do not use set_width(fixed): that also makes short text produce a very wide black
        # bar (tried before).
        try:
            self.toast.set_style_max_width(TOAST_W, 0)
        except BaseException:
            self.toast.set_width(TOAST_W)
        self.toast.set_style_text_align(lv.TEXT_ALIGN.CENTER, 0)
        # Font as large as possible; step down through fallbacks when the binding lacks the
        # bigger sizes
        font = _font("font_montserrat_48", "font_montserrat_32", "font_montserrat_24")
        if font is not None:
            self.toast.set_style_text_font(font, 0)
        self.toast.align(lv.ALIGN.CENTER, 0, 0)
        self.toast.add_flag(lv.obj.FLAG.HIDDEN)

        _ensure_dir(SAVE_DIR)
        self.save_num = self._next_index()

        # Sensor construction raises when the camera is missing or the connector is loose. It
        # must be converted to AppUnavailable here: the launcher uses that to show a "camera
        # unavailable" panel and let CANCEL return to the desktop, instead of leaving a screen
        # full of low-level exception output. Half-initialised handles must be closed as well,
        # otherwise the leftover channels block reopening.
        sensor = None
        try:
            sensor = Sensor(fps=30)
            sensor.reset()
            # chn2 = preview: the ISP scales the widest window that keeps the screen
            # aspect, so there are no black bars and no distortion
            set_preview(sensor, CAM_CHN_ID_2, self.img_w, self.img_h)
            sensor.set_pixformat(Sensor.RGB888, chn=CAM_CHN_ID_2)
            # chn0 = capture (native full resolution)
            sensor.set_framesize(w=CAPTURE_W, h=CAPTURE_H, chn=CAM_CHN_ID_0)
            sensor.set_pixformat(Sensor.RGB888, chn=CAM_CHN_ID_0)
            sensor.run()
        except BaseException as error:
            print("[Camera] sensor init failed: {}".format(error))
            sys.print_exception(error)
            close_sensor(sensor)
            raise AppUnavailable(CAMERA_UNAVAILABLE)
        self.sensor = sensor
        self._set_stat("-")

        # Shutter sound: on first entry a shutter.wav (~4.8 KB) is synthesised on board into
        # /data/audio/shutter.wav and reused from then on; the differential stereo PCM
        # (~10 KB) stays resident in memory.
        # The output stream is opened here and reused for the whole session - opening and
        # closing it per shot would add the fixed 1.1 s of stop_stream (measured; see the
        # header of shutter.py).
        self.sfx = shutter.ShutterSfx()
        if not shutter.muted():
            self.sfx.open()

        print("[Camera] ready: preview {}x{} | capture {}x{} | dir={} next={}".format(
            self.img_w, self.img_h, CAPTURE_W, CAPTURE_H, SAVE_DIR, self.save_num))

    def _next_index(self):
        """Scan the directory for existing IMG_xxxx.jpg files and return the next free
        number."""
        nxt = 0
        try:
            names = os.listdir(SAVE_DIR)
        except OSError:
            return 0
        for name in names:
            if not name.startswith(SAVE_PREFIX) or not name.endswith(".jpg"):
                continue
            digits = name[len(SAVE_PREFIX):-4]
            if digits.isdigit():
                value = int(digits) + 1
                if value > nxt:
                    nxt = value
        return nxt

    def _set_stat(self, fps_text):
        self.stat_label.set_text("{}x{} | FPS: {}".format(self.img_w, self.img_h, fps_text))

    def _show_toast(self, text):
        self.toast.set_text(text)
        self.toast.clear_flag(lv.obj.FLAG.HIDDEN)
        self.toast_until = time.ticks_add(time.ticks_ms(), TOAST_MS)

    def _shoot(self):
        """OK pressed: play the shutter sound first, then show the white flash.

        The actual capture is deferred to later frames (see the state machine in step): the
        white frame must first be flushed to the panel by LVGL, and nothing can be redrawn
        while _capture() blocks - capturing straight away would mean the white frame is
        never visible.
        """
        try:
            self.sfx.play()
        except BaseException as error:
            print("[Camera] sfx failed: {}".format(error))
        # Cover only the viewfinder, not the HUD: the overlay is created after the image and
        # before the labels, so move_foreground is not needed - raising it to the front would
        # whiten the FPS text and the toast as well.
        self.flash.clear_flag(lv.obj.FLAG.HIDDEN)
        self.shot_phase = FLASH_FRAMES

    def _capture(self):
        """Capture one full-resolution chn0 frame -> JPEG -> disk. Blocks for about 0.5 s."""
        if self.sensor is None:
            return
        # Show Saving... first and suspend the auto-hide; restart the timer once saving is
        # done
        self.toast.set_text("Saving...")
        self.toast.clear_flag(lv.obj.FLAG.HIDDEN)
        self.toast_until = 0
        t0 = time.ticks_ms()
        try:
            img = self.sensor.snapshot(chn=CAM_CHN_ID_0)
            jpg = img.to_jpeg()
            name = "{}{:04d}.jpg".format(SAVE_PREFIX, self.save_num)
            path = SAVE_DIR + "/" + name
            jpg.save(path, quality=JPEG_QUALITY)
            self.save_num += 1
            size = os.stat(path)[6]
            elapsed = time.ticks_diff(time.ticks_ms(), t0)
            print("[Camera] saved {} ({}KB) in {}ms".format(path, size // 1024, elapsed))
            self._show_toast("Saved\n{}\n{}x{}  {}KB".format(
                path, CAPTURE_W, CAPTURE_H, size // 1024))
        except BaseException as error:
            print("[Camera] capture failed: {}".format(error))
            sys.print_exception(error)
            self._show_toast("Save failed:\n{}".format(error))
        finally:
            gc.collect()

    def step(self):
        if self.sensor is None:
            return

        # OK key: read and clear it first no matter which flash/capture phase we are in,
        # otherwise a press during the flash leaves the flag set and an extra photo is taken
        # once the app is idle again.
        ok = self.ctx.take_ok()
        if self.shot_phase > 0:
            # Hold the white flash: let this frame reach the panel first
            self.shot_phase -= 1
            if self.shot_phase == 0:
                self.flash.add_flag(lv.obj.FLAG.HIDDEN)
                self.shot_phase = -1     # capture on the next frame: flush the "no flash" frame first
        elif self.shot_phase < 0:
            self.shot_phase = 0
            self._capture()
        elif ok:
            self._shoot()

        # Hide the toast once it expires
        if self.toast_until and \
                time.ticks_diff(time.ticks_ms(), self.toast_until) >= 0:
            self.toast_until = 0
            self.toast.add_flag(lv.obj.FLAG.HIDDEN)

        try:
            img = self.sensor.snapshot(chn=CAM_CHN_ID_2)
            img.as_lvgl_img_src(self.img_widget)
            self.img_widget.invalidate()
        except BaseException as error:
            self.stat_label.set_text("snapshot error: {}".format(error))
            self._no_signal.on_fail(self.ctx)
            return
        self._no_signal.on_ok()

        self.frame_count += 1
        now = time.ticks_ms()
        elapsed = time.ticks_diff(now, self.last_fps_ms)
        if elapsed >= 1000:
            fps = self.frame_count * 1000.0 / elapsed
            self._set_stat("{:.1f}".format(fps))
            self.frame_count = 0
            self.last_fps_ms = now

    def destroy(self):
        print("[Camera] destroy begin")
        self.shot_phase = 0
        # Close the shutter sound stream: stop_stream takes a fixed ~1.1 s and cannot be
        # hidden (it does not release the GIL, so a thread does not help either), so pay it
        # only here; never skip stop_stream and call close directly (that hangs the board).
        try:
            self.sfx.close()
        except BaseException as e:
            print("[Camera]   sfx.close error:", e)
        if self.sensor is not None:
            try:
                self.sensor.stop()
                print("[Camera]   sensor.stop ok")
            except BaseException as e:
                print("[Camera]   sensor.stop error:", e)
            try:
                self.sensor.deinit()
                print("[Camera]   sensor.deinit ok")
            except BaseException as e:
                print("[Camera]   sensor.deinit error:", e)
            self.sensor = None
        print("[Camera] destroy done")


def create_app(content, ctx):
    return CameraApp(content, ctx)
