# The one place that knows which GUI files hold the parsed-as-text tables.
#
# The generators read the theme constants, the app registry and the i18n table with
# regexes instead of importing them: those modules import lvgl and the media stack,
# which do not exist on the host. Reading text keeps the host tools working, at the
# cost of a fragile coupling to the file layout -- so the layout lives here and
# nowhere else. Moving a table to another file means editing this module only.

import io
import os

# Board paths, relative to the GUI root.
THEME_FILES = ("shell/theme.py",)
REGISTRY_FILES = ("shell/registry.py",)
I18N_FILES = ("core/i18n.py",)


def _read(gui_dir, names):
    parts = []
    for name in names:
        path = os.path.join(gui_dir, *name.split("/"))
        if not os.path.exists(path):
            raise SystemExit("[gui-source] missing %s under %s" % (name, gui_dir))
        parts.append(io.open(path, encoding="utf-8").read())
    return "\n".join(parts)


def theme_text(gui_dir):
    """Colour, size and margin constants plus BAKE_VER."""
    return _read(gui_dir, THEME_FILES)


def registry_text(gui_dir):
    """APPS, FOLDER_MEMBERS and APP_ACCENTS."""
    return _read(gui_dir, REGISTRY_FILES)


def i18n_text(gui_dir):
    """The NAMES table."""
    return _read(gui_dir, I18N_FILES)


def all_text(gui_dir):
    """Theme plus registry: the default source for the bakers."""
    return _read(gui_dir, tuple(THEME_FILES) + tuple(REGISTRY_FILES))


def board_source_code(gui_path, var="GUI_SRC"):
    """Code that loads the same tables on the board into `var`.

    `_doctor.py` cannot import these modules either, for the same reason as the host
    tools, but it does have the files on the card and a MicroPython `open`.
    """
    names = []
    for name in tuple(THEME_FILES) + tuple(REGISTRY_FILES):
        if name not in names:
            names.append(name)
    return "%s = ''.join(open('%s/' + f).read() for f in %r)\n" % (
        var, gui_path, names)
