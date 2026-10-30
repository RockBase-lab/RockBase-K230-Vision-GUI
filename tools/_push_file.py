import sys
import time

from ampy.pyboard import Pyboard

# Push a single local file to the board: python tools/_push_file.py COM56 115200 <local> <remote>
PORT = sys.argv[1] if len(sys.argv) > 1 else "COM56"
BAUD = int(sys.argv[2]) if len(sys.argv) > 2 else 115200
LOCAL = sys.argv[3] if len(sys.argv) > 3 else "tools/fontsrc/Nunito_Bold.ttf"
REMOTE = sys.argv[4] if len(sys.argv) > 4 else "/sdcard/factory/gui/assets/Nunito_Bold.ttf"

data = open(LOCAL, "rb").read()
print("[INFO] {} bytes -> {}".format(len(data), REMOTE))

board = Pyboard(PORT, baudrate=BAUD)
for _ in range(5):
    board.serial.write(b"\x03")
    time.sleep(0.1)
time.sleep(0.5)
board.enter_raw_repl()

board.exec_("f = open('{}', 'wb')".format(REMOTE).encode())
CHUNK = 4096
for off in range(0, len(data), CHUNK):
    board.exec_("f.write({!r})".format(data[off:off + CHUNK]).encode())
out = board.exec_("f.close()\nimport os\nprint(os.stat('{}')[6])".format(REMOTE).encode())
print("[DONE] board size:", out.decode().strip())
board.exit_raw_repl()
board.close()
