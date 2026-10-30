# Push a whole local directory to the board (with a progress display).
# Usage: python tools/push_factory.py [COM port] [baud] [local dir] [board dir]
# Defaults: auto 921600 factory/gui /sdcard/factory/gui
#
# This is the transfer path; use deploy_board.py --verify-only for the closed-loop check.
# Unlike deploy_board.py it does not soft reset the board, because a reset restarts main.py,
# the GUI reopens launcher.py, and every later write to a running module fails with EPERM.
# Interrupting the loop with Ctrl-C leaves the files closed.

import base64
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _serial_guard as guard

PORT = sys.argv[1] if len(sys.argv) > 1 else "auto"
BAUD = int(sys.argv[2]) if len(sys.argv) > 2 else 921600
LOCAL_DIR = sys.argv[3] if len(sys.argv) > 3 else "factory/gui"
BOARD_DIR = sys.argv[4] if len(sys.argv) > 4 else "/sdcard/factory/gui"

SKIP_NAMES = {"__pycache__"}
SKIP_EXT = {".pyc"}

from ampy.pyboard import PyboardError


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


def leave_raw_repl(port, baud):
    """Return the board to the friendly REPL before the guard looks at it.

    A cancelled run leaves the board in the raw REPL, where Ctrl-C produces no echo. The
    guard reads that silence as "the GUI main loop owns the board" and soft resets, which
    restarts main.py, and the GUI then holds the modules that are about to be overwritten.
    Ctrl-B is the documented way out of the raw REPL.
    """
    try:
        import serial
        s = serial.Serial(port, baud, timeout=0.2)
    except BaseException as error:
        print("[PREP] port not opened for the reset: %s" % error)
        return
    try:
        for _ in range(3):
            s.write(b"\x02")            # Ctrl-B: leave raw REPL
            time.sleep(0.2)
            s.write(b"\r\x03")          # Ctrl-C: stop any running loop
            time.sleep(0.2)
        s.write(b"\r")
        time.sleep(0.3)
        text = s.read(65536) or b""
        if text:
            print("[PREP] board answered, %d bytes" % len(text))
    finally:
        s.close()


def main():
    items = collect_files()
    total = len(items)
    total_bytes = sum(os.path.getsize(p) for p, _ in items)
    print("[INFO] %d files, %.1f KB total" % (total, total_bytes / 1024))

    leave_raw_repl(PORT, BAUD)

    # The guard opens the port with exclusivity handling and enters the raw REPL with
    # timeouts. Stock enter_raw_repl() relies on pyserial 2.x's interCharTimeout, which
    # 3.x ignores, so a silent board hangs it forever instead of failing.
    board = guard.open_board(PORT, BAUD)

    def run(code, retries=5):
        """Execute one snippet, retrying transient FAT and CDC failures.

        EPERM on open/write and "could not exec command" both come and go on this board
        within a second; a retry succeeds where a bare call aborts the whole transfer.
        """
        last = None
        for attempt in range(retries):
            try:
                return board.exec_(code.encode())
            except PyboardError as error:
                last = error
                time.sleep(0.25 + 0.25 * attempt)
                try:
                    guard.enter_raw_repl(board, timeout=6.0, verbose=False)
                except BaseException:
                    pass
        raise last

    # A file still open in the VM locks its FAT entry, and the write then fails with
    # EPERM. Collect first so the launcher's read handle is gone before anything is
    # overwritten.
    run("import gc\ngc.collect()")

    # Skip files the board already holds at the same size. launcher.py is the usual case:
    # the GUI keeps it open, so it can be neither removed nor reopened while the board
    # lives, and rewriting an unchanged file only produces EPERM.
    force = "--force" in sys.argv
    remote_size = {}
    listing = run("import os\n"
                  "for p in %r:\n"
                  "    try:\n"
                  "        print(p, os.stat(p)[6])\n"
                  "    except OSError:\n"
                  "        print(p, -1)\n" % [r for _, r in items])
    for line in listing.decode().strip().splitlines():
        parts = line.rsplit(" ", 1)
        if len(parts) == 2:
            try:
                remote_size[parts[0]] = int(parts[1])
            except ValueError:
                pass
    pending = [(local, remote) for local, remote in items
               if force or remote_size.get(remote) != os.path.getsize(local)]
    print("[INFO] %d file(s) already at the right size, %d to push"
          % (total - len(pending), len(pending)))
    if not pending:
        print("[DONE] nothing to push")
        board.exit_raw_repl()
        board.close()
        return
    items = pending
    total = len(items)
    total_bytes = sum(os.path.getsize(p) for p, _ in items) or 1

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
    run("import ubinascii")
    for i, (local_path, remote_path) in enumerate(items, 1):
        data = open(local_path, "rb").read()
        # 'wb' truncates an existing file, so no removal is needed, and removal is worse:
        # it fails while the file is held and only adds a failure path.
        run("f = open('%s', 'wb')" % remote_path)
        chunk = 4096
        for off in range(0, len(data), chunk):
            # base64 keeps the payload printable and inflates it 1.33x; the %r form
            # escaped non-ASCII source up to 4x and long transfers dropped the link.
            b64 = base64.b64encode(data[off:off + chunk]).decode("ascii")
            run("f.write(ubinascii.a2b_base64('%s'))" % b64)
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
