# Audio subsystem: capture, playback, shutter effect, tone generator.
#
# Deliberately empty. Importing this package must not pull in the PDM/AO
# hardware layer, so each consumer imports the submodule it needs:
#
#   from audio import device     # recording, WAV finalising, playback
#   from audio import shutter    # camera shutter effect
#   from audio import tone       # single-frequency sine for the sweep page
#
# The files used to sit flat next to the launcher; grouping them keeps the
# top level readable without changing what each module does.
