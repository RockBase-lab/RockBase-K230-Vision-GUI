# LVGL image loading shared by the launcher, the keyboard and the apps.
#
# Four identical copies of this existed, one of them swallowing the error, so a
# missing asset vanished silently. Keep one implementation and one failure policy:
# return None and leave the caller to decide whether that is visible.

import lvgl as lv

from core import paths


def load(path):
    """Read a PNG into an lv.img_dsc_t whose data stays owned by the descriptor.

    Returns None when the file is unreadable. The caller must keep the descriptor
    referenced for as long as the widget draws it.
    """
    try:
        with open(path, "rb") as f:
            data = f.read()
        return lv.img_dsc_t({"data_size": len(data), "data": data})
    except OSError:
        return None


def load_asset(name):
    """Same as load(), resolving `name` under assets/ through core.paths."""
    return load(paths.asset_path(name))
