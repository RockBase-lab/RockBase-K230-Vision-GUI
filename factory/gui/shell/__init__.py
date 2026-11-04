# Home-screen framework (L5). Only launcher.py imports this package.
#
# Deliberately empty: importing this package must not build a screen, start the
# boot sequence or touch the display. Import the submodule you need:
#
#   from shell import registry
#   from shell.home import Launcher
#
# Never import launcher.py from here. The launcher runs through
# exec(open(...).read()), so a reverse import executes the whole file a second
# time and builds a second Launcher.
#
# Contents: theme, registry, tile cache, display bring-up, touch, buttons,
# context, status bar, boot, and class Launcher. All of it still lives in
# launcher.py; this package is the stated destination.
