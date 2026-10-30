"""Interrupt the running GUI so the deployer can overwrite files on /sdcard.

The launcher keeps its modules open, so a write fails with EPERM while it runs.
Ctrl-C returns to the REPL; the file handles are released with the frame.
"""

import sys
import time

import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM9"
BAUD = int(sys.argv[2]) if len(sys.argv) > 2 else 921600

port = serial.Serial(PORT, BAUD, timeout=0.3)
try:
    port.write(b"\x03")
    time.sleep(0.8)
    port.write(b"\x03")
    time.sleep(0.5)
    port.write(b"\r\n")
    time.sleep(0.5)
    text = b""
    deadline = time.time() + 3.0
    while time.time() < deadline:
        chunk = port.read(4096)
        if chunk:
            text += chunk
finally:
    port.close()

tail = text.decode("utf-8", "replace").strip().splitlines()[-6:]
for line in tail:
    print("INT " + line[:120])
print("INT repl_likely={}".format(">>>" in text.decode("utf-8", "replace")))
