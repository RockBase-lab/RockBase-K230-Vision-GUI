# Pull a file from the board to the local machine (base64 chunks over the raw REPL).
#
# Use case: bring a frame captured on board back to the PC and run the same model
# there as ground truth, to decide whether the board-side pipeline is wrong or the
# model genuinely detected nothing.
#
# Usage: python tools/_pull_file.py [COM port] [baud] <remote path> <local path>
#
# Note: the board must print(b64.decode()); printing bytes directly emits a b'...'
# repr that corrupts the data. The PC side then strips whitespace and anything
# outside the base64 alphabet. Large files cost one round trip per 3KB and are slow,
# so down-sample on board first (e.g. a[::5, ::5]) before saving a capture.
import base64
import re
import sys
import time

from ampy.pyboard import Pyboard

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM56"
BAUD = int(sys.argv[2]) if len(sys.argv) > 2 else 115200
REMOTE = sys.argv[3] if len(sys.argv) > 3 else "/sdcard/_cap.npy"
LOCAL = sys.argv[4] if len(sys.argv) > 4 else "tmp/_cap.npy"

board = Pyboard(PORT, baudrate=BAUD)
for _ in range(5):
    board.serial.write(b"\x03")
    time.sleep(0.1)
time.sleep(0.5)
board.enter_raw_repl()

size = int(board.exec_("import os\nprint(os.stat('{}')[6])".format(REMOTE).encode()).decode().strip())
print("[INFO] remote size:", size)

# The board base64-encodes the file and prints it in chunks; the PC reassembles
board.exec_("import ubinascii,os\nf=open('{}','rb')".format(REMOTE).encode())
CHUNK = 3072  # raw bytes (~4096 once base64 encoded)
parts = []
off = 0
while off < size:
    n = min(CHUNK, size - off)
    out = board.exec_(
        "print(ubinascii.b2a_base64(f.read({})).decode())".format(n).encode()
    ).decode().strip()
    parts.append(out)
    off += n
board.exec_("f.close()".encode())

b64 = "".join(parts)
# The REPL may inject newlines/whitespace; strip it all before decoding
b64 = "".join(b64.split())
# Drop anything outside the base64 alphabet (stray bytes the REPL injects now and then)
import re
b64 = re.sub(r"[^A-Za-z0-9+/=]", "", b64)
data = base64.b64decode(b64)
with open(LOCAL, "wb") as f:
    f.write(data)
print("[DONE] local size:", len(data))
board.exit_raw_repl()
board.close()
