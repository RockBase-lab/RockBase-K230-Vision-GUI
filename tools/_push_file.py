# Push a single file to the board.
#
#   python tools/_push_file.py COM56 921600 <local> <remote>
#
# Serial access goes through _serial_guard: the stock ampy enter_raw_repl() relies on
# pyserial 2.x's interCharTimeout, which 3.x ignores, so serial.timeout stays None and
# read(1) blocks forever once the board is busy in the GUI loop. Every step here carries
# a finite timeout and a busy board is soft-reset automatically.
#
# Payload is sent as chunked base64: %r escapes binary to \xNN, up to 4x larger, and a
# long transfer drops bytes on this board.

import base64
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

import _serial_guard as guard  # noqa: E402

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM56"
BAUD = int(sys.argv[2]) if len(sys.argv) > 2 else 115200
LOCAL = sys.argv[3] if len(sys.argv) > 3 else "tools/fontsrc/Nunito_Bold.ttf"
REMOTE = sys.argv[4] if len(sys.argv) > 4 else "/sdcard/factory/gui/assets/fonts/Nunito-Bold-700.ttf"

CHUNK = 4096

with open(LOCAL, "rb") as f:
    data = f.read()
print("[INFO] {} bytes -> {}".format(len(data), REMOTE))

board = guard.open_board(PORT, BAUD)
board.serial.timeout = guard.SERIAL_TIMEOUT


def run(code):
    """Execute and return the decoded output."""
    return board.exec_(code.encode()).decode("utf-8", "replace")


try:
    run("import ubinascii")
    # A previous interrupted run may have left the handle open, so close and remove the
    # target first: open('wb') on an already-open file fails, and a stale partial file
    # would otherwise be appended to.
    run("try:\n    f.close()\nexcept:\n    pass\n"
        "import os\n"
        "try:\n    os.remove('{}')\nexcept:\n    pass".format(REMOTE))
    # EPERM on open('wb') is a transient FAT error on this board, not a lock: the same
    # call succeeds after a short pause. Retrying beats failing the whole push.
    last = None
    for attempt in range(4):
        try:
            run("f = open('{}', 'wb')".format(REMOTE))
            last = None
            break
        except BaseException as error:
            last = error
            print("[WARN] open failed ({}), retry {}/4".format(error, attempt + 1))
            time.sleep(0.6)
    if last is not None:
        raise last
    done = 0
    for off in range(0, len(data), CHUNK):
        b64 = base64.b64encode(data[off:off + CHUNK]).decode("ascii")
        run("f.write(ubinascii.a2b_base64('{}'))".format(b64))
        done = min(off + CHUNK, len(data))
        print("\r[PUSH] {}/{} ({:.0f}%)".format(
            done, len(data), done * 100.0 / len(data)), end="")
        sys.stdout.flush()
    print()
    run("f.close()")
    size = run("import os\nprint(os.stat('{}')[6])".format(REMOTE)).strip()
    print("[DONE] board size: {}".format(size))
    if size.isdigit() and int(size) != len(data):
        print("[FAIL] board size mismatch: local {} vs board {}".format(len(data), size))
        sys.exit(1)
finally:
    try:
        board.exit_raw_repl()
    except BaseException:
        pass
    try:
        board.close()
    except BaseException:
        pass
