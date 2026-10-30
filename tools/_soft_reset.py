"""Soft reset the board and confirm the GUI launcher starts.

Ctrl-C and Ctrl-D make the USB CDC interface re-enumerate, so a read fails with
ClearCommError or FileNotFoundError for a moment. That is the reset in progress, not a
fault: the port is reopened and reading continues until the deadline.
"""

import sys
import time

import serial

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM9"
BAUD = int(sys.argv[2]) if len(sys.argv) > 2 else 921600
WAIT_S = float(sys.argv[3]) if len(sys.argv) > 3 else 12.0


def open_port():
    for _ in range(20):
        try:
            return serial.Serial(PORT, BAUD, timeout=0.2)
        except BaseException:
            time.sleep(0.25)
    return None


port = open_port()
if port is None:
    raise SystemExit("[FAIL] port %s never opened" % PORT)

buf = bytearray()
try:
    try:
        port.write(b"\x03")          # Ctrl-C: break any running loop
        time.sleep(0.4)
        port.write(b"\r\n")
        time.sleep(0.2)
        port.write(b"\x04")          # Ctrl-D: soft reset -> main.py
    except BaseException as error:
        print("[RESET] write crossed the re-enumeration: %s" % error)

    deadline = time.time() + WAIT_S
    while time.time() < deadline:
        try:
            data = port.read(4096)
        except BaseException:
            try:
                port.close()
            except BaseException:
                pass
            port = open_port()
            if port is None:
                break
            continue
        if data:
            buf.extend(data)
    text = buf.decode("utf-8", "replace")
finally:
    try:
        port.close()
    except BaseException:
        pass

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
