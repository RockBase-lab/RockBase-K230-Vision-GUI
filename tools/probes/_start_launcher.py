# Starts the on-board GUI through a single exec, avoiding the unicode
# errors seen when pushing launcher.py as a script.

exec(open("/sdcard/factory/gui/launcher.py").read())
