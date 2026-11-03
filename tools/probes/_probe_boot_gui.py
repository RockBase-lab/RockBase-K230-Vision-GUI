# Verify the freshly deployed GUI boots: exec launcher.py with --lvgl, build the
# Launcher, and report the preloaded model pool, fonts, and home-screen state.
# Writes nothing; returns to the REPL so the pool size can be compared later.

import gc
import sys

sys.path.insert(0, "/sdcard/factory/gui")
for _name in ("launcher", "model_pool", "font_pool", "apps", "ui.beep",
              "ui.paging", "ui.splash", "ui.backlight", "net.service", "batt"):
    if _name in sys.modules:
        del sys.modules[_name]

import lvgl as lv  # noqa: F401 -- the display must be initialised before use

src = open("/sdcard/factory/gui/launcher.py").read()
G = {"__name__": "gui_probe"}
exec(compile(src, "launcher.py", "exec"), G)

G["_ensure_cache_dir"]()
print("BAKE_VER", G["BAKE_VER"])

import model_pool

# boot_load() is what main() runs before building the desktop: it preloads the
# models and fonts. Calling Launcher() alone skips it and reports resident=0.
splash = G["splash_ui"].Splash()
G["boot_load"](splash)
print("POOL resident={}".format(model_pool.loaded_count()))

launcher = G["Launcher"]()
print("HOME built | mem_free={}".format(gc.mem_free()))
launcher.splash = splash.scr

print("DONE mem_free={}".format(gc.mem_free()))
