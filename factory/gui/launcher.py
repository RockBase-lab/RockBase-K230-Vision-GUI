# RockBase K230 factory GUI entry point.
#
# Everything below this file lives in a layer (see README.md): the home screen and
# the launcher state machine are shell/, apps/ are the apps, and assets/ is data.
# This file only bootstraps the import path and brings the system up in the order
# the hardware needs.
#
# Run: exec(open('/sdcard/factory/gui/launcher.py').read())
#
# Under exec() there is no __file__ tied to this source, so the GUI root is
# resolved here once and put on sys.path. Every module below imports normally
# after that point and never hard-codes the deploy path.

import gc
import os
import sys

import lvgl as lv
from media.display import Display

# CanMV's os module has no os.path. The deploy path is fixed by the layout, and
# this is the one place allowed to name it: a package import above this line would
# fail with "no module named 'shell'".
# A probe may exec this source in a scope that carries a __file__, so that path is
# accepted only when it actually holds the package.
_gui_dir = "/sdcard/factory/gui"
try:
    _here = __file__.rsplit("/", 1)[0]
except NameError:
    _here = None
if _here:
    try:
        os.stat(_here + "/shell/__init__.py")
        _gui_dir = _here
    except OSError:
        pass
if _gui_dir not in sys.path:
    sys.path.insert(0, _gui_dir)

from core import config
from core import i18n
from core import model_pool
from hardware import backlight
from hardware import panel
from net import service as net_mgr
from shell import display_init
from shell import splash as splash_ui
from shell.boot import boot_load
from shell.home import Launcher
from shell.touchscreen import TouchScreen
from ui import beep


def main():
    # Preferences first: brightness and click tone are applied while the panel
    # comes up, so the first boot frame and the first tap already honour them.
    config.load()
    i18n.init()
    # Display rotation is applied at the Display layer.
    Display.init(panel.TYPE, width=panel.WIDTH, height=panel.HEIGHT,
                 flag=panel.FLAG, to_ide=True)
    # Light the panel as early as possible: everything below can take a second
    # (tile bake) and the user must see that the board booted.
    display_init.backlight_on(
        config.get_int("display.brightness", backlight.MIN_PERCENT))
    try:
        display_init.lvgl_setup()
        # Buzzer before the UI so the very first tap after boot has sound
        beep.init()
        beep.set_enabled(config.get_bool("ui.click_sound", True))
        # When a worker (connect/scan/NTP) holds the GIL inside a wlan C call, the
        # click sound cannot be muted by its shutdown thread and inevitably becomes
        # a long tone; let beep stay silent for that window instead.
        beep.set_busy_check(net_mgr.busy)
        splash = splash_ui.Splash()
        try:
            boot_load(splash)
        except BaseException as error:
            # The desktop must appear even if a preload step misbehaves; apps fall
            # back to loading their own resources.
            print("[BOOT] preload error: {}".format(error))
            sys.print_exception(error)
        launcher = Launcher()
        launcher.splash = splash.scr   # deleted safely in run() once home is loaded
        TouchScreen()
        launcher.run()
    except KeyboardInterrupt as e:
        print("[GUI] user stop:", e)
    except BaseException as e:
        sys.print_exception(e)
        raise
    finally:
        beep.shutdown()
        model_pool.release_all()
        lv.deinit()
        Display.deinit()
        gc.collect()


if __name__ == "__main__":
    main()
