# Network subsystem: the resident WiFi/NTP service and the password wrapper it
# needs for its on-disk config.
#
# One module, one consumer: the wrapper's only caller is load()/save() in
# service.py, so it lives there rather than in a file of its own.
