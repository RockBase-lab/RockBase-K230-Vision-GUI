# Run a local script on the board and collect its output over the raw REPL. The script
# exits once it finishes, so nothing keeps the serial port open afterwards.
# Usage: python tools/_run_remote.py [--reset] [--lvgl] [PORT|auto] [baud] <local_script.py>
# Defaults: auto picks the USB serial port / 921600 (falls back to 115200 on failure)
#
# --reset : soft reset (Ctrl-D) before running the script.
#
# --lvgl  : inject display init/teardown around the script (the 800x480 ST7701 path).
#           Hard rule: any probe that creates an lv object or calls task_handler must
#           pass --lvgl. Using lv widgets without Display.init + lv.init + flush_cb
#           wedges the board immediately (serial stream stops, recovery needs a
#           guard-driven soft reset). The script may still import lvgl as usual.
#
# Important: audio/PDM probes must close()/terminate() in a finally block. A leaked
# PDM stream makes every later open fail with "kd_mpi_ai_set_pdm_clk failed:-1", and
# then:
#   - a soft reset (Ctrl-D) does not help, the native handle cannot be reclaimed
#   - mpp.ai.kd_mpi_ai_disable(dev) recovers a single leak, but repeated calls lock the
#     PDM channel for good (open raises), leaving a power cycle / reset button as the
#     only way back
# So the recovery path is a power cycle; do not rely on code to undo a leak.
#
# Unlike an interactive runner that keeps the port held after the script ends, this
# uses the raw REPL and exits once the output is drained, which suits one-off probes
# and verification runs.
#
# Implementation notes: the board root is not writable (EINVAL) and one very long line
# trips the REPL limit, so the source is written to a temp file under /sdcard first and
# then exec'd. The temp file is removed at the end; if removal fails (EBUSY) the next
# run retries at startup.

import sys
import os
import time

from ampy.pyboard import Pyboard

# ---------------- argument parsing ----------------
# Two accepted forms:
#   _run_remote.py <script.py>
#   _run_remote.py COM56 921600 <script.py>
args = [a for a in sys.argv[1:] if not a.startswith("-")]
FLAGS = {a for a in sys.argv[1:] if a.startswith("--")}
RESET = "--reset" in FLAGS
PORT = "auto"
BAUD = 921600
LOCAL = None
if args and args[-1].lower().endswith(".py"):
    LOCAL = args.pop()
if args:
    PORT = args[0]
if len(args) > 1:
    BAUD = int(args[1])
BAUD_FALLBACK = 115200

# Script run timeout in seconds. Default 300, override with --timeout=NNN.
RUN_TIMEOUT = 300
# Idle timeout in seconds: this much silence is treated as a hang, so stop instead of
# waiting out RUN_TIMEOUT when the script really loops forever. Default 45, --idle=N.
IDLE_TIMEOUT = 45

# Serial read timeout in seconds. It must be set once, after connecting and entering the
# raw REPL, then left alone: on Windows assigning serial.timeout triggers
# _reconfigure_port(), which is slow, drops data already buffered, and repeated
# switches can raise PermissionError(13).
SERIAL_TIMEOUT = 0.05
for _f in FLAGS:
    if _f.startswith("--timeout="):
        RUN_TIMEOUT = int(_f.split("=", 1)[1])
    elif _f.startswith("--idle="):
        IDLE_TIMEOUT = int(_f.split("=", 1)[1])

LVGL_BOOT = "--lvgl" in FLAGS

if not LOCAL:
    print("usage: python tools/_run_remote.py [--reset] [--lvgl] [COMx|auto] "
          "[baud] <local_script.py>")
    sys.exit(2)


def resolve_port(name):
    """Resolve auto / an explicit name to a real COM port.

    Bluetooth virtual ports are excluded: they never carry the board.
    """
    from serial.tools import list_ports
    ports = list(list_ports.comports())
    if not ports:
        raise SystemExit("[FAIL] no serial port found; check the USB cable")
    if name and name.lower() != "auto":
        return name
    # The Chinese literal is a match pattern, not user-facing text: a
    # Chinese-locale Windows names the virtual Bluetooth port in Chinese. Both
    # spellings are needed, so this is the one deliberate non-ASCII string.
    cand = [p for p in ports
            if "蓝牙" not in (p.description or "")
            and "Bluetooth" not in (p.description or "")]
    if not cand:
        cand = ports
    print("[INFO] auto-selected port: %s (%s)" % (cand[0].device, cand[0].description))
    return cand[0].device


PORT = resolve_port(PORT)

# Serial guard: ensure_port() / is_write_disconnect(). Imported from this directory so
# the tools need no package install.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _serial_guard as guard

src = open(LOCAL, "rb").read()
print("[RUN] {} bytes -> board".format(len(src)))

# The board root is not writable (EINVAL), so the file must live on /sdcard. Each run
# uses a unique name: a crashed run keeps the open() file object alive in its traceback
# and leaves the fixed-name temp file locked, after which every open(TMP, 'wb') quietly
# writes into that corrupted file and f.read() always raises UnicodeError. A unique name
# sidesteps the locked leftover entirely.
import time as _time
TMP = "/sdcard/_probe_%d.py" % (_time.time_ns() % 100000000)

# Remove the on-board temp file: collect first so leftover file objects are released,
# then delete, retrying while the file is busy.
CLEAN_CODE = (
    "import os, gc\n"
    "for _ in range(3):\n"
    "    gc.collect()\n"
    "    try:\n"
    "        os.remove('%s')\n"
    "        print('CLEAN_OK')\n"
    "        break\n"
    "    except OSError as e:\n"
    "        if e.args[0] == 2:\n"       # ENOENT: nothing to delete
    "            print('CLEAN_OK')\n"
    "            break\n"
    "    except Exception:\n"
    "        break\n" % TMP
)


def connect(baud):
    # The guard owns both the exclusive open and the raw REPL entry: it carries
    # timeouts and soft-resets a board that is busy running the GUI. The stock
    # enter_raw_repl has no working timeout and hangs forever on a silent board.
    b = guard.open_board(PORT, baud)
    b.serial.timeout = SERIAL_TIMEOUT
    return b


def soft_reset(b):
    """Ctrl-D soft reset.

    The board prints boot text afterwards, so the raw REPL must be re-entered.
    """
    print("[INFO] soft reset (Ctrl-D) ...")
    try:
        b.exit_raw_repl()
    except BaseException:
        pass
    b.serial.write(b"\x03")
    time.sleep(0.2)
    b.serial.write(b"\x04")
    time.sleep(1.5)
    # Drain the boot log
    try:
        b.serial.read(b.serial.in_waiting or 1)
    except BaseException:
        pass
    for _ in range(3):
        b.serial.write(b"\x03")
        time.sleep(0.1)
    time.sleep(0.4)
    b.enter_raw_repl()
    # Set it only after entering the raw REPL, otherwise the short timeout breaks
    # ampy's own read_until / read(2) calls.
    b.serial.timeout = SERIAL_TIMEOUT


def _port_busy_hint():
    """Last-resort hint when the port cannot be taken at all.

    The normal path is already handled automatically by guard.ensure_port().
    """
    print("[FAIL] cannot open port %s (already tried clearing stale processes)." % PORT)
    print("       Two possibilities remain:")
    print("       1) The VS Code CanMV extension holds the port -- disconnect it "
          "in that terminal, then retry;")
    print("       2) The board USB just re-enumerated -- wait 2 s and rerun this command.")
    sys.exit(3)


try:
    board = connect(BAUD)
except BaseException as exc:
    if "failed to access" in str(exc) or "PermissionError" in str(exc):
        _port_busy_hint()
    if BAUD == BAUD_FALLBACK:
        raise
    print("[WARN] %d baud failed (%s), falling back to %d" % (BAUD, exc, BAUD_FALLBACK))
    board = connect(BAUD_FALLBACK)

# ---------------- raw REPL execution (self-managed receive buffer) ----------------
# MicroPython answers one exec over the raw REPL with four fixed parts:
#     OK                        compile succeeded
#     <stdout> \x04             end of stdout
#     <stderr> \x04             end of stderr
#     >                         prompt for the next command
#
# Three things must be handled here instead of being left to ampy:
#   1) Pyboard opens the port without a timeout, so pyserial defaults to timeout=None
#      and read(n) returns only after n bytes arrive. The tail of the output is usually
#      shorter than n, which used to block forever: the script finished but the console
#      sat on ">" and never returned. Every read therefore uses a short timeout.
#   2) One read often brings back the stdout terminator, stderr and the prompt together.
#      Dropping the surplus after a marker makes the next segment wait for a marker that
#      will never come again, so the surplus must be kept.
#   3) The response has no separate status byte; success can only be inferred from an
#      empty stderr.
RX = bytearray()


def _serial_read():
    """Read with the short timeout already set in connect(); never change it here."""
    try:
        return board.serial.read(4096)
    except BaseException as exc:
        text = repr(exc)
        if "ClearCommError" in text or "PermissionError" in text:
            time.sleep(0.02)
            return b""
        raise


def _take(term, total_timeout=10, idle_timeout=None, sink=None):
    """Take the data before term out of RX and consume term.

    With a sink, bytes that cannot belong to term are streamed out as they arrive,
    which gives live output; without one everything comes back at once once term is
    found. Returns (data, found). On timeout it returns (data so far, False) and lets
    the caller decide the next step.
    """
    if idle_timeout is None:
        idle_timeout = IDLE_TIMEOUT
    acc = bytearray()
    keep = len(term) - 1     # the tail may hold half a marker, keep it in RX
    deadline = time.time() + total_timeout
    last = time.time()
    while True:
        i = RX.find(term)
        if i >= 0:
            data = bytes(RX[:i])
            del RX[:i + len(term)]
            acc += data
            if sink is not None and data:
                sink(data)
            return bytes(acc), True
        if len(RX) > keep:
            n = len(RX) - keep
            data = bytes(RX[:n])
            del RX[:n]
            acc += data
            if sink is not None and data:
                sink(data)
        now = time.time()
        if now > deadline or now - last > idle_timeout:
            data = bytes(RX)
            RX.clear()
            acc += data
            if sink is not None and data:
                sink(data)
            return bytes(acc), False
        chunk = _serial_read()
        if chunk:
            RX.extend(chunk)
            last = time.time()


def _take_any(terms, total_timeout=10, idle_timeout=None, sink=None):
    """Like _take, but returns whichever of several markers appears first.

    Compile confirmation needs this: a successful compile answers "OK" first, while a
    failure goes straight to the error output terminated by \\x04. Waiting only for
    "OK" would drag every failure out to the timeout.
    """
    if idle_timeout is None:
        idle_timeout = IDLE_TIMEOUT
    acc = bytearray()
    keep = max(len(t) for t in terms) - 1
    deadline = time.time() + total_timeout
    last = time.time()
    while True:
        best = -1
        hit = None
        for t in terms:
            i = RX.find(t)
            if i >= 0 and (best < 0 or i < best):
                best, hit = i, t
        if hit is not None:
            data = bytes(RX[:best])
            del RX[:best + len(hit)]
            acc += data
            if sink is not None and data:
                sink(data)
            return bytes(acc), hit
        if len(RX) > keep:
            n = len(RX) - keep
            data = bytes(RX[:n])
            del RX[:n]
            acc += data
            if sink is not None and data:
                sink(data)
        now = time.time()
        if now > deadline or now - last > idle_timeout:
            data = bytes(RX)
            RX.clear()
            acc += data
            if sink is not None and data:
                sink(data)
            return bytes(acc), None
        chunk = _serial_read()
        if chunk:
            RX.extend(chunk)
            last = time.time()


def _raw_exec(code, stream=False):
    """Run a snippet under the raw REPL and return (stdout, stderr, ok).

    The leftover prompt is informational and needs no draining before writing: after
    every command the raw REPL returns to the input state, so writing the code plus
    \\x04 is enough.

    A write that hits a disconnect (Windows errno 22 / ClearCommError) reopens the port
    and retries once: those errors are almost always USB CDC re-enumeration (board
    reset, or the port briefly taken over), and a reopen recovers.
    """
    code_b = code.encode() if isinstance(code, str) else code
    try:
        return _raw_exec_once(code_b, stream)
    except BaseException as exc:
        if not guard.is_write_disconnect(exc):
            raise
        print("\n[GUARD] serial write dropped; reopened the port and retrying once ...")
        reconnect()
        return _raw_exec_once(code_b, stream)


def _raw_exec_once(code_b, stream):
    _serial_read()           # discard the prompt left by the previous command
    RX.clear()
    # Chunked writes are mandatory: the on-board REPL input buffer is small, so pushing
    # several KB at once drops bytes. Dropped bytes raise nothing at write time; they
    # leave the on-board file truncated or scrambled, and a later read() raises an error
    # unrelated to the real cause, such as UnicodeError (seen with large files).
    for i in range(0, len(code_b), 256):
        board.serial.write(code_b[i:i + 256])
        time.sleep(0.005)
    board.serial.write(b"\x04")

    def _sink(data):
        sys.stdout.write(data.decode("utf-8", "replace"))
        sys.stdout.flush()

    sink = _sink if stream else None

    ack, term = _take_any((b"OK", b"\x04"), 15, 8)
    if term != b"OK":
        # Compile failed: the error text came back with ack, one \x04 is still pending
        err, _ = _take(b"\x04", 5, 5)
        return ack, err, False

    out, ok_out = _take(b"\x04", RUN_TIMEOUT, IDLE_TIMEOUT, sink)
    err, ok_err = _take(b"\x04", 5, 5)
    return out, err, bool(ok_out and ok_err)


def reconnect():
    """Close the old handle, take the exclusive open again and re-enter raw REPL.

    Any half-received bytes in RX are dropped along with it.
    """
    global board
    RX.clear()
    try:
        board.serial.close()
    except BaseException:
        pass
    time.sleep(0.4)
    board = connect(BAUD)


out = b""
failed = None
try:
    # Remove a temp file a previous run may have left behind (EBUSY leftover)
    _raw_exec(CLEAN_CODE, stream=False)

    # Write the source to an on-board temp file before exec'ing it, so one very long
    # line never trips the REPL limit.
    _raw_exec("f = open('{}', 'wb')".format(TMP), stream=False)
    CHUNK = 2048
    total = len(src)
    for off in range(0, total, CHUNK):
        _raw_exec("f.write({!r})".format(src[off:off + CHUNK]), stream=False)
        done = min(off + CHUNK, total)
        sys.stdout.write("\r[RUN] upload {}/{} ({:.0f}%)".format(
            done, total, 100.0 * done / total))
        sys.stdout.flush()
    sys.stdout.write("\n")
    _raw_exec("f.close()", stream=False)

    # Read and close explicitly, then exec the string. A bare
    # exec(open(TMP).read()) keeps the file object alive until GC runs, after which
    # os.remove fails with OSError 16 (EBUSY) and the temp file stays on /sdcard for
    # good. Output is streamed chunk by chunk so a long script never leaves the terminal
    # silent.
    if LVGL_BOOT:
        # Same display bring-up as the GUI main loop (800x480 ST7701). The script is
        # wrapped in try/finally so a failure still deinits the display.
        code = (
            "f = open('{}')\nsrc = f.read()\nf.close()\ndel f\n"
            "import image, uctypes\n"
            "import lvgl as _lv\n"
            "from media.display import Display\n"
            "_imgs = [image.Image(800, 480, image.BGRA8888) for _ in range(2)]\n"
            "Display.init(Display.ST7701, width=800, height=480, "
            "flag=Display.FLAG_ROTATION_270, to_ide=False)\n"
            "_lv.init()\n"
            "_drv = _lv.disp_create(800, 480)\n"
            "_drv.set_color_format(_lv.COLOR_FORMAT.ARGB8888)\n"
            "_drv.set_draw_buffers(_imgs[0].bytearray(), _imgs[1].bytearray(), "
            "_imgs[0].size(), _lv.DISP_RENDER_MODE.FULL)\n"
            "def _fcb(d, a, c):\n"
            "    if d.flush_is_last():\n"
            "        p = uctypes.addressof(c.__dereference__())\n"
            "        Display.show_image(_imgs[0] if _imgs[0].virtaddr() == p "
            "else _imgs[1], layer=Display.LAYER_OSD0)\n"
            "    d.flush_ready()\n"
            "_drv.set_flush_cb(_fcb)\n"
            "try:\n"
            "    exec(compile(src, '{}', 'exec'))\n"
            "finally:\n"
            "    _lv.deinit()\n"
            "    Display.deinit()\n"
        ).format(TMP, TMP)
    else:
        code = (
            "f = open('{}')\n"
            "src = f.read()\n"
            "f.close()\n"
            "del f\n"
            "exec(compile(src, '{}', 'exec'))".format(TMP, TMP)
        )
    print("[RUN] executing (output streams live) ...")
    out, err, ok = _raw_exec(code, stream=True)
    if not ok:
        print("\n[RUN] timeout/idle: board output stalled (see what already streamed above)")
    if err:
        print("\n[RUN] board stderr:\n" + err.decode("utf-8", "replace"))
    failed = (None if (ok and not err) else "board error")
except BaseException as error:
    failed = error
    print("\n[RUN] failed:", error)
finally:
    # Remove the temp file; a failure only warns and does not change this run's result
    try:
        cleaned, _cerr, _cok = _raw_exec(CLEAN_CODE, stream=False)
        if b"CLEAN_OK" not in cleaned:
            print("[WARN] could not delete {}; the next run retries automatically".format(TMP))
    except BaseException as exc:
        print("[WARN] cleanup skipped: {}".format(exc))
    try:
        board.exit_raw_repl()
    except BaseException:
        pass
    board.close()

if failed is None:
    print("[RUN] done")
else:
    print("[RUN] done (with errors)")
    sys.exit(1)
