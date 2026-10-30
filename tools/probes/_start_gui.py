# Starts the on-board GUI through a single exec, avoiding the unicode
# errors seen when pushing gui.py as a script.

exec(open("/sdcard/factory/gui/gui.py").read())
