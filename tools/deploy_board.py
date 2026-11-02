# RockBase one-shot board deploy: push every dependency the project needs on board to the
# SD card and confirm it with a closed-loop verification.
#
# Usage:
#   python tools/deploy_board.py                              # auto USB port, 921600 incremental
#   python tools/deploy_board.py COM56                        # explicit port
#   python tools/deploy_board.py COM56 115200                 # explicit port + baud
#   python tools/deploy_board.py --gui-only                   # push factory/gui only
#   python tools/deploy_board.py --autostart                  # also write /sdcard/main.py to autostart
#   python tools/deploy_board.py --with-test-media            # also push the big factory media (~53MB, slow)
#   python tools/deploy_board.py --verify-only                # verify only, write nothing
#   python tools/deploy_board.py --force                      # ignore "already identical", re-push all
#   python tools/deploy_board.py --dry-run                    # print the plan only
#   python tools/deploy_board.py --smoke                      # extra on-board import smoke test
#
# Deployed content (MANIFEST):
#   factory/gui  -> /sdcard/factory/gui   launcher.py / ui / audio / net / apps / assets
#   factory/test -> /sdcard/factory/test  production test scripts (**without** media >1MB
#                                         unless --with-test-media). This directory is
#                                         not part of the public tree; the entry is a
#                                         no-op when it is absent.
#
# Runtime directories created on board: /data/photos /data/cache/thumbs /data/cache/full
#   /data/audio (CanMV's os module has no path/makedirs, so every level needs os.mkdir)
#
# Transfer:
#   chunked base64 (ubinascii.a2b_base64). Sending binary through %r escapes it to \xNN
#   and inflates it up to 4x; base64 inflates only 1.33x, stays printable, and decodes in
#   about 1ms on board.
#
# Incremental strategy:
#   read every remote size with os.stat first; only files whose size matches and that are
#   <=256KB get a sha256 comparison (hashlib ships with the firmware, one exec finishes
#   the batch, far faster than pulling each file back).
#
# Closed-loop verification:
#   1) compare byte counts per file with os.stat   2) compile() every .py on board
#   (catches truncation/corruption)   3) report free space on /sdcard and /data
#   4) sweep leftover _probe* temp files from the /sdcard root
#
# Note: lvgl / image / media / ulab / nncase_runtime / touch come from the firmware and
# are outside the deploy scope.

import base64
import hashlib
import os
import sys
import time

from ampy.pyboard import Pyboard

# Serial guard (same directory): exclusivity handling + raw REPL entry with timeouts.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import _serial_guard as guard

# ---------------- argument parsing ----------------
raw = sys.argv[1:]
FLAGS = {a for a in raw if a.startswith("--")}
args = [a for a in raw if not a.startswith("--")]
PORT = args[0] if len(args) > 0 else "auto"
BAUD = int(args[1]) if len(args) > 1 else 921600
BAUD_FALLBACK = 115200


def resolve_port(name):
    """Resolve auto / an explicit name to a real COM port.

    Bluetooth virtual ports are excluded: they never carry the board.
    """
    from serial.tools import list_ports
    ports = list(list_ports.comports())
    if not ports:
        raise SystemExit("[FAIL] no serial port found; check the USB cable")
    if name and name.lower() != "auto":
        for p in ports:
            if p.device.lower() == name.lower():
                return p.device
        raise SystemExit("[FAIL] port %s does not exist; available: %s"
                         % (name, ", ".join(p.device for p in ports)))
    # Auto pick: the description mentions USB / serial and is not Bluetooth
    # The Chinese literal is a match pattern, not user-facing text: a
    # Chinese-locale Windows names the virtual Bluetooth port in Chinese. Both
    # spellings are needed, so this is the one deliberate non-ASCII string.
    cand = [p for p in ports
            if "蓝牙" not in (p.description or "")
            and "Bluetooth" not in (p.description or "")]
    if not cand:
        # Never fall back to the full list: a Bluetooth virtual port accepts the
        # open but never answers the raw-REPL handshake, so the deploy hangs
        # instead of failing. Make the user pick a port explicitly.
        raise SystemExit(
            "[FAIL] no non-Bluetooth serial port found. Available: %s\n"
            "       Pass the board port explicitly, e.g. "
            "python tools/deploy_board.py --gui-only COM9"
            % ", ".join("%s (%s)" % (p.device, p.description) for p in ports))
    print("[INFO] auto-selected port: %s (%s)" % (cand[0].device, cand[0].description))
    return cand[0].device


PORT = resolve_port(PORT)

GUI_ONLY = "--gui-only" in FLAGS
VERIFY_ONLY = "--verify-only" in FLAGS
AUTOSTART = "--autostart" in FLAGS
WITH_TEST_MEDIA = "--with-test-media" in FLAGS
FORCE = "--force" in FLAGS
DRY_RUN = "--dry-run" in FLAGS
SMOKE = "--smoke" in FLAGS

# ---------------- deploy manifest ----------------
# (local dir, board dir, push big media?)
MANIFEST = [
    ("factory/gui", "/sdcard/factory/gui", True),
    ("factory/test", "/sdcard/factory/test", WITH_TEST_MEDIA),
]
if GUI_ONLY:
    MANIFEST = MANIFEST[:1]

# Runtime directories that must exist on board (camera captures / gallery cache /
# audio / the face feature database). Paths follow the same /data/<app>/<topic>
# layout: the first level is the app name, the second what the app stores.
BOARD_MKDIRS = [
    "/data",
    "/data/photos",
    "/data/cache",
    "/data/cache/thumbs",
    "/data/cache/full",
    "/data/audio",
    "/data/face/recognize",
]

SKIP_DIRS = {"__pycache__", ".git"}
SKIP_EXT = {".pyc", ".pyo", ".bak"}   # .bak = local rollback backup, never pushed
BIG_MEDIA_EXT = {".wav", ".mp3", ".mp4", ".avi", ".mkv", ".flac", ".ogg"}
BIG_MEDIA_BYTES = 1 << 20  # 1MB
HASH_LIMIT = 256 << 10     # only files <=256KB get a sha256 comparison

AUTOSTART_MAIN = (
    "# auto-generated by tools/deploy_board.py --autostart\n"
    "import os\n"
    "exec(open('/sdcard/factory/gui/launcher.py').read())\n"
)


# ---------------- local file collection ----------------
def collect_files():
    items = []  # (local_path | None, remote_path)
    for local_dir, remote_dir, allow_media in MANIFEST:
        for root, dirs, fs in os.walk(local_dir):
            dirs[:] = [d for d in dirs if d not in SKIP_DIRS]
            rel = os.path.relpath(root, local_dir).replace("\\", "/")
            rdir = remote_dir if rel == "." else remote_dir + "/" + rel
            for name in sorted(fs):
                ext = os.path.splitext(name)[1].lower()
                if ext in SKIP_EXT:
                    continue
                full = os.path.join(root, name)
                if not allow_media and (
                    ext in BIG_MEDIA_EXT or os.path.getsize(full) > BIG_MEDIA_BYTES
                ):
                    continue
                items.append((full, rdir + "/" + name))
    if AUTOSTART:
        items.append((None, "/sdcard/main.py"))
    return items


def local_bytes(item):
    local_path, _ = item
    return AUTOSTART_MAIN.encode() if local_path is None else open(local_path, "rb").read()


def sha_bytes(data):
    return hashlib.sha256(data).hexdigest()


# ---------------- remote script snippets ----------------
MKDIR_SNIPPET = (
    "import os\n"
    "for d in %r:\n"
    "    try:\n"
    "        os.mkdir(d)\n"
    "    except OSError:\n"
    "        pass\n"
)


def main():
    global BAUD
    items = collect_files()
    size_map, hash_map = {}, {}
    for it in items:
        data = local_bytes(it)
        size_map[it[1]] = len(data)
        if len(data) <= HASH_LIMIT:
            hash_map[it[1]] = sha_bytes(data)
    total_bytes = sum(size_map.values())

    print("[INFO] %s @ %d baud | %d files, %.1f KB"
          % (PORT, BAUD, len(items), total_bytes / 1024.0))
    if not VERIFY_ONLY and not FORCE:
        print("[INFO] incremental mode: files with matching size+sha256 are skipped "
              "(--force re-pushes everything)")

    def connect(baud):
        # The guard owns the exclusive open and the raw REPL entry, both with timeouts.
        # The stock enter_raw_repl relies on pyserial 2.x's interCharTimeout, which
        # pyserial 3.x ignores, so serial.timeout stays None and a silent board (Ctrl-C
        # ignored inside the GUI main loop) hangs it forever. The guard tries Ctrl-C
        # first and falls back to a Ctrl-D soft reset.
        return guard.open_board(PORT, baud)

    try:
        board = connect(BAUD)
    except BaseException as exc:
        if BAUD == BAUD_FALLBACK:
            raise
        print("[WARN] %d baud failed (%s), falling back to %d" % (BAUD, exc, BAUD_FALLBACK))
        board = connect(BAUD_FALLBACK)
        BAUD = BAUD_FALLBACK

    def run(code):
        return board.exec_(code.encode()).decode("utf-8", "replace")

    bad = []
    comp_ok = False
    try:
        # ---------- 1) snapshot the remote state ----------
        remote_size = {}
        listed = run(
            "import os\n"
            "for p in %r:\n"
            "    try:\n"
            "        print(p, os.stat(p)[6])\n"
            "    except OSError:\n"
            "        print(p, -1)\n" % [r for _, r in items]
        )
        for ln in listed.strip().splitlines():
            parts = ln.rsplit(" ", 1)
            if len(parts) == 2:
                try:
                    remote_size[parts[0]] = int(parts[1])
                except ValueError:
                    pass

        remote_hash = {}
        need_hash = [p for p in hash_map
                     if remote_size.get(p) == size_map[p] and not FORCE]
        if need_hash:
            hashed = run(
                "import hashlib\n"
                "import binascii\n"
                "for p in %r:\n"
                "    try:\n"
                "        f = open(p, 'rb')\n"
                "        h = hashlib.sha256()\n"
                "        while True:\n"
                "            b = f.read(8192)\n"
                "            if not b:\n"
                "                break\n"
                "            h.update(b)\n"
                "        f.close()\n"
                "        print(p, binascii.hexlify(h.digest()).decode())\n"
                "    except Exception:\n"
                "        print(p, '-')\n" % need_hash
            )
            for ln in hashed.strip().splitlines():
                parts = ln.rsplit(" ", 1)
                if len(parts) == 2:
                    remote_hash[parts[0]] = parts[1]

        # ---------- 2) build the incremental plan ----------
        todo = []
        skipped = 0
        for it in items:
            remote = it[1]
            if not FORCE and remote_size.get(remote) == size_map[remote]:
                if remote not in hash_map or remote_hash.get(remote) == hash_map[remote]:
                    skipped += 1
                    continue
            todo.append(it)

        print("[PLAN] %d to push, %d skipped (already identical)" % (len(todo), skipped))
        if DRY_RUN:
            for _, remote in todo:
                print("  + %-58s %8d B" % (remote, size_map[remote]))
            return 0

        if VERIFY_ONLY:
            todo = []

        # ---------- 3) create directories (runtime dirs are topped up even when there
        # is nothing to push) ----------
        dirs = list(BOARD_MKDIRS)
        for _, remote in todo:
            d = remote.rsplit("/", 1)[0]
            if d not in dirs:
                dirs.append(d)
        # os.mkdir creates one level only and fails with ENOENT when the parent is
        # missing (guaranteed on a fresh card). Every ancestor of every directory is
        # added, then the list is sorted so prefixes naturally come first.
        all_dirs = set(dirs)
        for d in dirs:
            parts = d.split("/")
            for i in range(2, len(parts)):
                anc = "/".join(parts[:i])
                if anc:
                    all_dirs.add(anc)
        run(MKDIR_SNIPPET % sorted(all_dirs))

        # ---------- 4) push (chunked base64) ----------
        if todo:
            run("import ubinascii")
            start = time.time()
            done = 0
            todo_bytes = sum(size_map[r] for _, r in todo) or 1
            CHUNK = 4096
            for i, it in enumerate(todo, 1):
                remote = it[1]
                data = local_bytes(it)
                # An interrupted run may leave an unclosed handle; close + remove before
                # creating the file again
                run("try:\n    f.close()\nexcept:\n    pass\n"
                    "import os\n"
                    "try:\n    os.remove('%s')\nexcept:\n    pass" % remote)
                run("f = open('%s', 'wb')" % remote)
                for off in range(0, len(data), CHUNK):
                    b64 = base64.b64encode(data[off:off + CHUNK]).decode("ascii")
                    run("f.write(ubinascii.a2b_base64('%s'))" % b64)
                    pct = (done + min(off + CHUNK, len(data))) * 100.0 / todo_bytes
                    sys.stdout.write("\r[%2d/%2d] %-46s %6.1f%%"
                                     % (i, len(todo), remote[-46:], pct))
                    sys.stdout.flush()
                run("f.close()")
                done += len(data)
            print("\r[%2d/%2d] %-46s 100.0%% (%.1fs)          "
                  % (len(todo), len(todo), "ALL PUSHED", time.time() - start))

        # ---------- 5) verification: byte counts ----------
        print("[VERIFY] checking byte counts of %d files ..." % len(items))
        remote_size = {}
        listed = run(
            "import os\n"
            "for p in %r:\n"
            "    try:\n"
            "        print(p, os.stat(p)[6])\n"
            "    except OSError:\n"
            "        print(p, -1)\n" % [r for _, r in items]
        )
        for ln in listed.strip().splitlines():
            parts = ln.rsplit(" ", 1)
            if len(parts) == 2:
                try:
                    remote_size[parts[0]] = int(parts[1])
                except ValueError:
                    pass
        bad = [(r, size_map[r], remote_size.get(r, -1))
               for _, r in items if remote_size.get(r, -1) != size_map[r]]
        if bad:
            print("[VERIFY] %d file(s) with a size mismatch:" % len(bad))
            for p, want, got in bad:
                print("  %-58s expect %8d got %8d" % (p, want, got))
        else:
            print("[VERIFY] OK: all %d file sizes match" % len(items))

        # ---------- 6) verification: on-board syntax compile ----------
        pys = [r for _, r in items if r.endswith(".py")]
        out = run(
            "ok = 0\n"
            "bad = []\n"
            "for p in %r:\n"
            "    try:\n"
            "        compile(open(p).read(), p, 'exec')\n"
            "        ok += 1\n"
            "    except Exception as e:\n"
            "        bad.append('%%s: %%s' %% (p, e))\n"
            "print('COMPILE_OK', ok)\n"
            "for b in bad:\n"
            "    print('COMPILE_FAIL', b)\n" % pys
        )
        comp_ok = "COMPILE_OK" in out
        for ln in out.strip().splitlines():
            if ln.startswith("COMPILE_OK"):
                print("[VERIFY] %s .py file(s) compiled on board" % ln.split()[1])
            elif ln.startswith("COMPILE_FAIL"):
                print("[VERIFY] " + ln)

        # ---------- 7) optional: on-board import smoke (no UI main loop) ----------
        if SMOKE:
            out = run(
                "import sys\n"
                "sys.path.insert(0, '/sdcard/factory/gui')\n"
                "mods = ['ui.paging', 'apps.app_touch', 'apps.app_camera',\n"
                "        'apps.app_gallery', 'apps.app_audio',\n"
                "        'apps.app_placeholder']\n"
                "for m in mods:\n"
                "    try:\n"
                "        __import__(m)\n"
                "        print('IMPORT_OK', m)\n"
                "    except Exception as e:\n"
                "        print('IMPORT_FAIL', m, e)\n"
            )
            for ln in out.strip().splitlines():
                if ln.startswith("IMPORT_OK"):
                    print("[SMOKE] " + ln)
                elif ln.startswith("IMPORT_FAIL"):
                    print("[SMOKE] " + ln)
                    comp_ok = False
            print("[SMOKE] import smoke done (launcher.py starts the main loop, "
                  "so it is not imported here)")

        # ---------- 8) space report ----------
        print(run(
            "import os\n"
            "for mp in ('/sdcard', '/data'):\n"
            "    try:\n"
            "        s = os.statvfs(mp)\n"
            "        print('  %s free %.1fMB' % (mp, s[2] * s[0] / 1048576.0))\n"
            "    except Exception as e:\n"
            "        print('  %s statvfs failed: %s' % (mp, e))\n"
        ).rstrip())

        # ---------- 9) sweep leftover probe temp files from the /sdcard root ----------
        # A probe run drops a _probe*.py under /sdcard and leaves it behind when
        # interrupted.
        out = run(
            "import os, gc\n"
            "gc.collect()\n"
            "gone = []\n"
            "for n in os.listdir('/sdcard'):\n"
            "    if not n.startswith('_probe'):\n"
            "        continue\n"
            "    try:\n"
            "        os.remove('/sdcard/' + n)\n"
            "        gone.append(n)\n"
            "    except Exception:\n"
            "        pass\n"
            "print('CLEANED', gone)\n"
        )
        for ln in out.strip().splitlines():
            if ln.startswith("CLEANED"):
                gone = ln[8:].strip("[] '\"")
                print("[CLEAN] leftover probe temp files on /sdcard: %s" % (gone or "none"))
    finally:
        try:
            board.exit_raw_repl()
        except BaseException:
            pass
        board.close()

    if bad or not comp_ok:
        print("[FAIL] deploy incomplete -- rerun once")
        return 1
    print("[DONE] board ready. Run: exec(open('/sdcard/factory/gui/launcher.py').read())")
    return 0


if __name__ == "__main__":
    sys.exit(main())
