"""Soft reset the board and confirm the GUI launcher starts.

Sends Ctrl-D on the friendly REPL and reads the boot output for a few seconds,
so the caller sees whether main.py brought the launcher up instead of assuming.
"""

import sys
import time

import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM9"
BAUD = int(sys.argv[2]) if len(sys.argv) > 2 else 921600
WAIT_S = float(sys.argv[3]) if len(sys.argv) > 3 else 12.0

port = serial.Serial(PORT, BAUD, timeout=0.2)
try:
    port.write(b"\x03")          # Ctrl-C: break any running loop
    time.sleep(0.4)
    port.write(b"\r\n")
    time.sleep(0.2)
    port.write(b"\x04")          # Ctrl-D: soft reset -> main.py
    deadline = time.time() + WAIT_S
    buf = bytearray()
    while time.time() < deadline:
        data = port.read(4096)
        if data:
            buf.extend(data)
    text = buf.decode("utf-8", "replace")
finally:
    port.close()

for line in text.splitlines():
    line = line.rstrip()
    if not line:
        continue
    low = line.lower()
    if ("gui" in low or "launcher" in low or "rockbase" in low
            or "traceback" in low or "error" in low or "exception" in low):
        print("BOOT " + line[:160])

print("BOOT chars={} gui_started={}".format(
    len(text), ("[GUI]" in text) or ("RockBase" in text)))
