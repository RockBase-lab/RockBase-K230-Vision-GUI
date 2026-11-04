# Cold-start the GUI with every factory module cache dropped, which is what a
# power-on gives you. Used to measure the boot sequence honestly: without this the
# persistent REPL keeps font_pool and ocr_dict populated from an earlier run, and
# their load cost silently disappears from the measurement.

import sys

for _name in list(sys.modules.keys()):
    if (_name == "launcher" or _name.startswith("apps") or _name.startswith("ui")
            or _name.startswith("net") or _name.startswith("core") or _name == "hardware.camera_fov"
            or _name in ("core", "core.model_pool", "core.font_pool", "core.ocr_dict", "hardware.battery",
                         "paging")):
        del sys.modules[_name]

print("[COLD] purged factory modules")
exec(open("/sdcard/factory/gui/launcher.py").read())
