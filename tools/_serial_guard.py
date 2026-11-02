"""Serial-port exclusivity guard: make sure *this* process gets the port, and when it
cannot, clear the holder and retry.

Why it exists: while the GUI main loop runs on the board, any Ctrl-C/Ctrl-D makes the
USB CDC interface re-enumerate, and Windows then reports one of two failures:
  - at open time: `failed to access COMx` (the port is still held by its previous owner)
  - at write time: `ClearCommError failed (PermissionError(13, ..., 22))`
Both mean "the same port is held concurrently or left behind", not broken hardware.
Disconnecting the CanMV terminal's serial port by hand used to turn each occurrence
into several extra rounds of waiting, so the handling is automated here.

Handling order, lightest first:
  1. Try to open; return on success.
  2. Find same-machine processes whose command line carries one of the toolchain
     keywords in SELF_KEYS, skip this process and terminate them.
  3. Wait a short backoff, try to open again, for at most attempts rounds.
Every step is printed so it is clear which one did the job.

Note: the VS Code extension host (node) is never touched. When the extension's own
serial session holds the port, it cannot be killed safely from here, so the module
states that an extension session is suspected and leaves the decision (click
Disconnect in the CanMV terminal) to the caller. It never retries silently forever.
"""

import os
import subprocess
import sys
import time

# A command line carrying these keywords marks a leftover process of this project's own
# toolchain, which is safe to clear.
SELF_KEYS = ("canmv_driver.py", "_run_remote.py", "deploy_board.py",
             "_push_file.py", "_soft_reset.py", "ampy")

_PS_QUERY = (
    "Get-CimInstance Win32_Process | "
    "Where-Object {{ $_.CommandLine -ne $null -and "
    "({matchers}) }} | "
    "ForEach-Object {{ \"{{0}}`t{{1}}\" -f $_.ProcessId, $_.Name }}"
)


def _ps(*args):
    """Run PowerShell and return its stdout text; an empty string on failure."""
    try:
        out = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command"] + list(args),
            capture_output=True, text=True, timeout=20)
        return out.stdout or ""
    except Exception:
        return ""


def _hog_processes():
    """Return [(pid, name)] for processes whose command line hits a toolchain keyword.

    This process itself is excluded.
    """
    matchers = " -or ".join(
        "$_.CommandLine -like '*{0}*'".format(k) for k in SELF_KEYS)
    text = _ps(_PS_QUERY.format(matchers=matchers))
    me = os.getpid()
    found = []
    for line in text.splitlines():
        parts = line.strip().split("\t")
        if len(parts) != 2:
            continue
        try:
            pid = int(parts[0])
        except ValueError:
            continue
        if pid == me:
            continue
        found.append((pid, parts[1].strip()))
    return found


def _try_open(port, baud):
    """Try the port once: close it immediately when it opens and return True."""
    try:
        import serial
    except ImportError:
        return False
    try:
        s = serial.Serial()
        s.port = port
        s.baudrate = baud
        s.timeout = 0.5
        s.open()
        s.close()
        return True
    except Exception:
        return False


def kill_hogs(verbose=True):
    """Kill leftover toolchain processes. Returns the cleared [(pid, name)]."""
    hogs = _hog_processes()
    if not hogs:
        return []
    killed = []
    for pid, name in hogs:
        if verbose:
            print("[GUARD] killing stale process holding the port pid={} {}".format(pid, name))
        try:
            subprocess.run(["taskkill", "/PID", str(pid), "/F"],
                           capture_output=True, timeout=15)
            killed.append((pid, name))
        except Exception:
            pass
    return killed


def ensure_port(port, baud=115200, attempts=4, settle_ms=400,
                verbose=True):
    """Make the port usable. Returns True when usable, False when still held.

    Processes are only cleared when the open fails; a port that opens is left alone so a
    running deploy is never killed by mistake.
    """
    if _try_open(port, baud):
        return True
    for i in range(attempts):
        if verbose:
            print("[GUARD] {} will not open, attempt {}/{}".format(
                port, i + 1, attempts))
        killed = kill_hogs(verbose=verbose)
        time.sleep(settle_ms / 1000.0)
        if _try_open(port, baud):
            if verbose:
                print("[GUARD] port released ({} process(es) cleared)".format(len(killed)))
            return True
        # Still unopenable after clearing: most likely the VS Code extension's own
        # serial session holds the port, which cannot be killed.
        if not killed:
            if verbose:
                print("[GUARD] no stale process to clear; the port is probably held "
                      "by an IDE serial session: {}".format(port))
                print("[GUARD] disconnect the serial port in the CanMV terminal, "
                      "then retry")
            return False
    return False


def is_write_disconnect(exc):
    """Tell whether an exception is a mid-write disconnect (USB re-enumeration / takeover).

    On Windows it shows up as PermissionError(13) with winerror/errno 22, or as a message
    containing ClearCommError. Reopening the port recovers from it, so the whole run does
    not have to fail.
    """
    text = "{}".format(exc)
    if "ClearCommError" in text:
        return True
    errno = getattr(exc, "errno", None)
    winerror = getattr(exc, "winerror", None)
    return errno == 22 or winerror == 22


# ---------------- raw REPL (timeouts, GUI occupancy triggers a soft reset) ----------------

RAW_PROMPT = b"raw REPL; CTRL-B to exit\r\n"
SERIAL_TIMEOUT = 0.1


def _drain(s, seconds):
    """Read every byte that arrives within seconds (serial.timeout must be short already)."""
    buf = b""
    end = time.time() + seconds
    while time.time() < end:
        chunk = s.read(1)
        if chunk:
            buf += chunk
    return buf


def _read_until(s, marker, timeout):
    """Read until marker appears or the timeout expires. Returns (data, found).

    Unlike the read_until that ships with ampy, serial.timeout must already be a finite
    value, otherwise read(1) blocks forever. That helper sets pyserial 2.x's
    interCharTimeout, which pyserial 3.x ignores, so timeout stays None - the root cause
    of the old hangs.
    """
    buf = b""
    end = time.time() + timeout
    while time.time() < end:
        chunk = s.read(1)
        if chunk:
            buf += chunk
            if buf.endswith(marker):
                return buf, True
    return buf, False


def enter_raw_repl(board, timeout=10.0, verbose=True):
    """Enter the raw REPL.

    While the board runs the GUI main loop Ctrl-C does nothing (measured: 4 in a row
    produced zero echo), so only a Ctrl-D soft reset brings the REPL back. That is done
    here automatically, and every step carries a timeout.

    board is an ampy.pyboard.Pyboard instance; returns True on success and raises
    RuntimeError on failure.
    """
    s = board.serial
    s.timeout = SERIAL_TIMEOUT
    _drain(s, 0.3)                       # flush output left by the previous command

    # 1) Try Ctrl-C first: it interrupts a board in the REPL or in a plain script
    s.write(b"\r\x03\x03")
    probe = _drain(s, 0.9)
    responsive = bool(probe)

    # 2) No response means the board sits in the GUI main loop and the interrupt cannot
    # reach it -> soft reset
    if not responsive:
        if verbose:
            print("[GUARD] Ctrl-C ignored; the board is in the GUI main loop -> "
                  "Ctrl-D soft reset")
        s.write(b"\x04")
        rebooted, found = _read_until(s, b"soft reboot", 6.0)
        if verbose:
            print("[GUARD] soft reset {}".format("done" if found else "unconfirmed"))
        _drain(s, 0.5)
        # Interrupt right after the reset, otherwise main.py starts the GUI again and owns
        # stdin
        s.write(b"\x03\x03")
        _drain(s, 0.5)

    # 3) Ctrl-A enters the raw REPL; read the prompt with a timeout
    s.write(b"\r\x01")
    data, found = _read_until(s, RAW_PROMPT, timeout)
    if not found:
        tail = data[-160:].decode("utf-8", "replace") if data else "(no echo)"
        raise RuntimeError(
            "failed to enter the raw REPL (no prompt within {:.0f}s): {}".format(
                timeout, tail))
    if verbose:
        print("[GUARD] raw REPL ready")
    return True


def open_board(port, baud, verbose=True):
    """Open a Pyboard connection and make sure the raw REPL is reached.

    Port exclusivity handling is included.
    """
    from ampy.pyboard import Pyboard
    ensure_port(port, baud, verbose=verbose)
    board = Pyboard(port, baudrate=baud)
    try:
        enter_raw_repl(board, verbose=verbose)
    except BaseException:
        try:
            board.serial.close()
        except BaseException:
            pass
        raise
    return board


if __name__ == "__main__":
    # Self-check: python tools/_serial_guard.py COM9 115200
    p = sys.argv[1] if len(sys.argv) > 1 else "COM9"
    b = int(sys.argv[2]) if len(sys.argv) > 2 else 115200
    ok = ensure_port(p, b)
    print("[GUARD] result:", "usable" if ok else "busy")
    sys.exit(0 if ok else 1)
