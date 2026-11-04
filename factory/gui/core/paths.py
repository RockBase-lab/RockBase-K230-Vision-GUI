# Single asset-path resolver for every layer above hardware/.
#
# This module exists because eight separate copies of "where is the GUI root?" had
# accumulated, each hard-coding /sdcard/factory/gui and each counting path
# separators from its own location. A module that moves one directory deeper then
# resolves the wrong path, and the failure is silent: the PNG loader turns ENOENT
# into None, so an icon simply disappears with no log line.
#
# Resolution is depth-independent: walk up from this file until a directory that
# holds assets/ is found. CanMV has no os.path, so the walk uses os.stat and plain
# string work.

import os

FALLBACK_ROOT = "/sdcard/factory/gui"

_root = None


def _dirname(path):
    """Parent directory of an absolute POSIX path, or '' at the root."""
    if "/" not in path:
        return ""
    return path.rsplit("/", 1)[0]


def _exists(path):
    try:
        os.stat(path)
        return True
    except OSError:
        return False


# Role groups under assets/. Order matters only if a name exists in two groups,
# which the generators never produce.
ASSET_GROUPS = ("icons", "widgets", "glyphs", "status", "fonts", "src", "cache")


def gui_root():
    """Absolute path of the deployed GUI root (the directory holding assets/).

    Resolution starts from this module's own __file__, which always exists: the
    entry point runs through exec(open(...).read()) and has no __file__, but it
    keeps its own fallback for the sys.path bootstrap and never calls this.
    """
    global _root
    if _root is not None:
        return _root

    path = globals().get("__file__")
    if not path:
        _root = FALLBACK_ROOT
        return _root

    # The board is POSIX, but normalising backslashes lets this module be exercised on
    # a host interpreter, which is the only way to test it off the board.
    current = _dirname(path.replace("\\", "/"))
    while current:
        if _exists(current + "/assets"):
            _root = current
            return _root
        parent = _dirname(current)
        if parent == current:
            break
        current = parent

    _root = FALLBACK_ROOT
    return _root


def asset_path(*parts):
    """Absolute path under assets/. Use this instead of joining from a module dir.

    Assets are grouped by role (icons/, widgets/, glyphs/, status/, fonts/, ...), but
    callers name a file without its group. A bare name is looked up at the assets
    root first, then in ASSET_GROUPS, so regrouping a file does not silently break
    every caller (the PNG loader turns a miss into None, with no log line).
    """
    base = gui_root() + "/assets"
    path = base
    for part in parts:
        path += "/" + part
    if _exists(path):
        return path
    name = parts[-1]
    for group in ASSET_GROUPS:
        candidate = base + "/" + group + "/" + name
        if _exists(candidate):
            return candidate
    return path


def gui_path(*parts):
    """Absolute path under the GUI root (for cache/ or other non-asset trees)."""
    path = gui_root()
    for part in parts:
        path += "/" + part
    return path
