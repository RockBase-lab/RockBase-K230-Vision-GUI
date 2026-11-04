# Verify the freshly deployed GUI boots: exec launcher.py with --lvgl, build the
# Launcher, and report the preloaded model pool, fonts, and home-screen state.
# Writes nothing; returns to the REPL so the pool size can be compared later.

import gc
import sys

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("core", "launcher", "core.model_pool", "core.font_pool", "apps", "ui.beep",
              "ui.paging", "shell.splash", "hardware.backlight", "net.service", "hardware.battery"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv  # noqa: F401 -- the display must be initialised before use

src = open("/sdcard/factory/gui/launcher.py").read()
G = {"__name__": "gui_probe"}
exec(compile(src, "launcher.py", "exec"), G)
# The tables and classes live in shell/ after the layer split; importing them
# here also keeps sys.path bootstrapped by the exec above.
from shell.theme import BAKE_VER
from shell.home import Launcher
from shell import tile_cache
from shell.boot import boot_load
from shell import splash as splash_ui


tile_cache.ensure_cache_dir()
print("BAKE_VER", BAKE_VER)

from core import model_pool

# boot_load() is what main() runs before building the desktop: it preloads the
# models and fonts. Calling Launcher() alone skips it and reports resident=0.
splash = splash_ui.Splash()
boot_load(splash)
print("POOL resident={}".format(model_pool.loaded_count()))

launcher = Launcher()
print("HOME built | mem_free={}".format(gc.mem_free()))
launcher.splash = splash.scr

print("DONE mem_free={}".format(gc.mem_free()))
