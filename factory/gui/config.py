# Persistent GUI settings shared by the launcher and every app.
#
# One flat JSON object at /data/settings/gui.json. Keys are dotted strings
# ("ui.click_sound") so a reader asks for exactly the preference it needs
# without walking nested dicts on every frame.
#
# Rules that matter on this board:
#   - A missing or corrupt file must never stop the GUI from starting: load()
#     keeps DEFAULTS and logs one line.
#   - Writes are immediate (a settings change is user intent) and go through
#     ensure_dir first, because CanMV has no os.makedirs.
#   - get() with no file access after the first call: reads are dict lookups,
#     safe to call from a per-frame path.

import json
import os

from ui import backlight

PATH = "/data/settings/gui.json"

DEFAULTS = {
    # Click tone on buttons, keyboard keys and the physical keys.
    "ui.click_sound": True,
    # Synthesised shutter sound in the Camera app.
    "camera.shutter_sound": True,
    # Panel backlight, percent. Ships at the minimum readable level: the panel is
    # the largest fixed load on the pack, and the Settings stepper shares the floor.
    "display.brightness": backlight.MIN_PERCENT,
}

_values = None


def _ensure_dir(path):
    """Create a directory level by level (CanMV has no os.path / os.makedirs)."""
    current = ""
    for part in path.strip("/").split("/"):
        current += "/" + part
        try:
            os.mkdir(current)
        except OSError:
            pass


def _missing(error):
    """True for ENOENT: a first boot or a settings file the user deleted. Both
    are normal, so they must not print an error line on every boot."""
    try:
        return error.args[0] == 2
    except BaseException:
        return False


def load():
    """Read the settings file into memory, layering it over DEFAULTS."""
    global _values
    values = dict(DEFAULTS)
    try:
        with open(PATH, "r") as f:
            data = json.loads(f.read())
        if isinstance(data, dict):
            for key in data:
                values[key] = data[key]
    except BaseException as error:
        # Defaults are valid state: never let a bad file block the GUI.
        if not _missing(error):
            print("[settings] load {} failed ({}), using defaults".format(PATH, error))
    _values = values
    return values


def _all():
    if _values is None:
        load()
    return _values


def get(key, default=None):
    values = _all()
    if key in values:
        return values[key]
    return DEFAULTS.get(key, default)


def get_bool(key, default=False):
    return bool(get(key, default))


def get_int(key, default=0):
    try:
        return int(get(key, default))
    except BaseException:
        return default


def set(key, value, save=True):
    """Store one preference. Persists by default: a caller that batches writes
    can pass save=False and call save() once."""
    _all()[key] = value
    if save:
        save_to_disk()


def save_to_disk():
    _ensure_dir("/data/settings")
    try:
        with open(PATH, "w") as f:
            f.write(json.dumps(_all()))
        return True
    except BaseException as error:
        print("[settings] save {} failed: {}".format(PATH, error))
        return False


def reset():
    """Restore every default and persist. Returns the new value table."""
    global _values
    _values = dict(DEFAULTS)
    save_to_disk()
    return _values
