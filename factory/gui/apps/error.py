# Single signal for "this app cannot start because hardware is missing".
#
# Camera-class apps (Camera / Scan / Vision) have to open the sensor during
# construction. When the module is unplugged or makes poor contact, Sensor
# initialization raises - and the exception type varies with the firmware version
# (both OSError and RuntimeError have been seen), so it cannot be classified by
# type. This separates "hardware missing" from "code bug": the app raises
# AppUnavailable, the launcher shows a plain-language panel, and Cancel (or the
# CANCEL key) returns to the desktop; the underlying exception only goes to the
# serial log instead of onto the screen.

CAMERA_UNAVAILABLE = ("Camera module not detected.\n"
                      "This app cannot run without the camera.\n"
                      "Check the camera cable and try again.")

# Threshold for detecting a camera drop-out at runtime: this many consecutive
# failed frame grabs means the signal is gone. About 1.5s at 30fps. 45 rather than
# a few frames, so an occasional single-frame timeout does not trip it.
NO_SIGNAL_LIMIT = 45


class NoSignalCounter:
    """Consecutive frame-failure counter: at the threshold it reports
    "unavailable" to the launcher (once only).

    For the case where the app opened successfully but the camera drops out
    mid-session (loose or unplugged cable): the Sensor object still exists and
    snapshot() raises on every frame. An app cannot raise the panel with Cancel
    itself, so that is handed to the launcher.
    """

    def __init__(self, limit=NO_SIGNAL_LIMIT):
        self.limit = limit
        self.fails = 0
        self.reported = False

    def on_fail(self, ctx):
        if self.reported:
            return
        self.fails += 1
        if self.fails >= self.limit:
            self.reported = True
            print("[AppError] no frame for {} frames -> unavailable".format(
                self.fails))
            ctx.report_unavailable(CAMERA_UNAVAILABLE)

    def on_ok(self):
        self.fails = 0


class AppUnavailable(Exception):
    """The hardware or resource the app depends on is unavailable.

    message is shown to the user directly (newlines allowed); the underlying
    exception text only goes to the serial log.
    """

    def __init__(self, message):
        super().__init__(message)
        self.message = message


def close_sensor(sensor):
    """Best-effort release of a Sensor that may be only half initialized (the
    failure path when the camera is missing).

    Must run before AppUnavailable is raised: a Sensor init that failed may
    already have claimed mpp channels, and without releasing them the next launch
    of the same app is blocked by the leftover state. stop/deinit each get their
    own try - calling stop on a Sensor that never ran errors anyway and must not
    cause deinit to be skipped.
    """
    if sensor is None:
        return
    for name in ("stop", "deinit"):
        try:
            getattr(sensor, name)()
        except BaseException as error:
            print("[AppError] sensor.{} failed: {}".format(name, error))
