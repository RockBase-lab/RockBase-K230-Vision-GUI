# Small LVGL helpers shared by the shell modules.
#
# They exist because the same three-line rituals (strip the theme's border, keep a
# decorative object out of hit testing, pick the first built-in font that exists)
# were repeated in every screen builder. A copy that drifts is a visible defect:
# the focus ring reappears, an icon swallows the click meant for its parent.

import gc

import lvgl as lv


def font(*names):
    """Return the first built-in font that exists among the candidate names.

    Built-in font names differ between firmware builds, so every call site lists
    the sizes it can live with, largest first.
    """
    for name in names:
        f = getattr(lv, name, None)
        if f:
            return f
    return None


def no_hit(obj):
    """Keep a decorative object out of hit testing.

    lv.obj is CLICKABLE by default and would steal the click from its parent button.
    """
    try:
        obj.clear_flag(lv.obj.FLAG.CLICKABLE)
    except Exception:
        pass


def clean(obj):
    """Strip the default theme's border, shadow and focus outline, FOCUSED included.

    Without FOCUSED the first button of a screen carries a visible outline ring.
    """
    focused = getattr(lv.STATE, "FOCUSED", 0x100)
    states = (0, lv.STATE.PRESSED, lv.STATE.CHECKED, lv.STATE.CHECKED | lv.STATE.PRESSED,
              focused, focused | lv.STATE.PRESSED)
    for st in states:
        try:
            obj.set_style_border_width(0, st)
            obj.set_style_shadow_width(0, st)
            obj.set_style_outline_width(0, st)
        except Exception:
            pass


def mem_str():
    """Current MicroPython heap usage in KB, for stage diagnostics."""
    try:
        free = gc.mem_free()
        alloc = gc.mem_alloc()
        return "alloc={}KB free={}KB".format(alloc // 1024, free // 1024)
    except Exception as error:
        return "mem n/a ({})".format(error)
