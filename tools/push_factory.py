# Push a whole local directory to the board (with a progress display)
# Usage: python tools/push_factory.py [COM port] [baud] [local dir] [board dir]
# Defaults: COM56 115200 factory/gui /sdcard/factory/gui

import os
import sys
import time

PORT = sys.argv[1] if len(sys.argv) > 1 else "COM56"
BAUD = int(sys.argv[2]) if len(sys.argv) > 2 else 115200
LOCAL_DIR = sys.argv[3] if len(sys.argv) > 3 else "factory/gui"
BOARD_DIR = sys.argv[4] if len(sys.argv) > 4 else "/sdcard/factory/gui"

SKIP_NAMES = {"__pycache__"}
SKIP_EXT = {".pyc"}

from ampy.pyboard import Pyboard


def collect_files():
    items = []
    for root, dirs, fs in os.walk(LOCAL_DIR):
        dirs[:] = [d for d in dirs if d not in SKIP_NAMES]
        rel = os.path.relpath(root, LOCAL_DIR).replace("\\", "/")
        remote_dir = BOARD_DIR if rel == "." else BOARD_DIR + "/" + rel
        for name in sorted(fs):
            if os.path.splitext(name)[1].lower() in SKIP_EXT:
                continue
            items.append((os.path.join(root, name), remote_dir + "/" + name))
    return items


def main():
    items = collect_files()
    total = len(items)
    total_bytes = sum(os.path.getsize(p) for p, _ in items)
    print("[INFO] %d files, %.1f KB total" % (total, total_bytes / 1024))

    board = Pyboard(PORT, baudrate=BAUD)
    # The board may be running the launcher main loop: interrupt it with several
    # Ctrl-C first, then enter the raw REPL
    for _ in range(5):
        board.serial.write(b"\x03")
        time.sleep(0.1)
    time.sleep(0.5)
    board.enter_raw_repl()

    def run(code):
        return board.exec_(code.encode())

    def mkdir(path):
        run("import os\n"
            "try:\n    os.mkdir('%s')\nexcept OSError:\n    pass\n" % path)

    def mkdir_chain(rel):
        path = ""
        for part in BOARD_DIR.strip("/").split("/"):
            path += "/" + part
            mkdir(path)
        if rel:
            for part in rel.split("/"):
                path += "/" + part
                mkdir(path)

    # Pre-create every directory
    dirs_done = set()
    for _, remote_path in items:
        d = remote_path.rsplit("/", 1)[0]
        if d not in dirs_done:
            mkdir_chain(d[len(BOARD_DIR):].strip("/"))
            dirs_done.add(d)

    done_bytes = 0
    start = time.time()
    for i, (local_path, remote_path) in enumerate(items, 1):
        data = open(local_path, "rb").read()
        # An interrupted earlier push can leave the file locked (open 'wb' -> EPERM).
        # Close any leftover handle and remove the old file before creating it again.
        run("try:\n    f.close()\nexcept:\n    pass\n"
            "import os\n"
            "try:\n    os.remove('%s')\nexcept:\n    pass" % remote_path)
        run("f = open('%s', 'wb')" % remote_path)
        chunk = 4096
        for off in range(0, len(data), chunk):
            run("f.write(%r)" % data[off:off + chunk])
            pct = (done_bytes + min(off + chunk, len(data))) * 100.0 / total_bytes
            sys.stdout.write("\r[%d/%d] %-24s %6.1f%%" % (i, total, os.path.basename(local_path), pct))
            sys.stdout.flush()
        run("f.close()")
        done_bytes += len(data)
    elapsed = time.time() - start
    print("\r[%d/%d] %-24s 100.0%% (%.1fs)        " % (total, total, "ALL DONE", elapsed))
    board.exit_raw_repl()
    board.close()
    print("[DONE] %d files -> %s" % (total, BOARD_DIR))


if __name__ == "__main__":
    main()
