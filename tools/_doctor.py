"""One-shot health check for the board's deployed GUI.

Run this before blaming the code: every trap that has silently hidden a change on
this card is checked here, in the order that pinpoints it.

    python tools/_doctor.py                 # auto-detect the port
    python tools/_doctor.py COM56           # explicit port

Exit code 0 when every check passes, 1 otherwise, so it can gate a deploy script.
"""

import io
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, HERE)

import _serial_guard as guard  # noqa: E402

GUI = "/sdcard/factory/gui"
LOCAL_GUI = os.path.join(ROOT, "factory", "gui")

# The autostart stub is three lines; anything near launcher.py's size is a stale copy.
STUB_MAX_BYTES = 400

# Package directories created by the module split. An empty one means the card still
# has the flat pre-split layout and the current launcher cannot import them.
PACKAGES = ("ui", "net", "audio", "core", "hardware")


def local_state():
    """Parse the local launcher.py: bake version and the module/icon it references."""
    txt = io.open(os.path.join(LOCAL_GUI, "launcher.py"), encoding="utf-8").read()
    ver = int(re.search(r"BAKE_VER\s*=\s*(\d+)", txt).group(1))
    mods, icons = [], []
    for block in (r"APPS\s*=\s*\[(.*?)\n\]", r"FOLDER_MEMBERS\s*=\s*\{(.*?)\n\}"):
        m = re.search(block, txt, re.S)
        if not m:
            continue
        body = m.group(1)
        for entry in re.findall(r"\{[^{}]*\}", body, re.S):
            mm = re.search(r'"module"\s*:\s*"([^"]+)"', entry)
            mi = re.search(r'"icon"\s*:\s*"([^"]+)"', entry)
            if mm:
                mods.append(mm.group(1))
            if mi:
                icons.append(mi.group(1))
    return ver, sorted(set(mods)), sorted(set(icons))


def pick_port():
    """First non-Bluetooth serial port. The Bluetooth virtual COMs open but never
    answer the raw REPL, so picking one looks like a permanent hang."""
    try:
        from serial.tools import list_ports
    except ImportError:
        return "COM56"
    for p in list_ports.comports():
        desc = (p.description or "").lower()
        if "bluetooth" in desc or "蓝牙" in (p.description or ""):
            continue
        return p.device
    return "COM56"


def main():
    port = sys.argv[1] if len(sys.argv) > 1 else pick_port()

    ver, mods, icons = local_state()
    print("[local] BAKE_VER={} | {} modules, {} icons referenced".format(
        ver, len(mods), len(icons)))

    board = guard.open_board(port, 115200)

    failures = []

    def check(name, ok, detail):
        print("[{}] {:34s} {}".format(" OK " if ok else "FAIL", name, detail))
        if not ok:
            failures.append(name)

    try:
        def run(code):
            return board.exec_(code.encode()).decode("utf-8", "replace")

        # 1) Autostart must be the stub, not a copy of launcher.py. A stale copy runs
        #    an old GUI that every probe of launcher.py still reports as current.
        try:
            size = int(run("import os;print(os.stat('/sdcard/main.py')[6])").strip())
        except BaseException:
            size = -1
        check("main.py is the boot stub", 0 < size <= STUB_MAX_BYTES,
              "{} B (limit {})".format(size, STUB_MAX_BYTES))

        # 2) Package directories must be populated. Empty ones make `from ui import ...`
        #    fail, and a stale main.py hides it because that copy uses the flat layout.
        #    A package the card does not have at all is the same failure, not a crash:
        #    listdir raises ENOENT, so the listing is fetched inside the snippet.
        for pkg in PACKAGES:
            listing = run("import os\n"
                          "p = '%s/%s'\n"
                          "try:\n    print(sorted(os.listdir(p)))\n"
                          "except OSError as e:\n    print('MISSING', e)\n"
                          % (GUI, pkg)).strip()
            empty = (listing.startswith("MISSING")
                     or len(listing) < 4 or listing in ("[]", "''"))
            check("package {} populated".format(pkg), not empty, listing[:70])

        # 3) Every module the registry names must exist on the card.
        missing = []
        for mod in mods:
            path = "{}/{}.py".format(GUI, mod.replace(".", "/"))
            if "-1" in run("import os\n"
                           "try:\n    os.stat('%s')\n    print(0)\n"
                           "except OSError:\n    print(-1)" % path).strip():
                missing.append(mod)
        check("all APPS modules present", not missing,
              "missing: {}".format(missing) if missing else "{} checked".format(len(mods)))

        # 4) Icons the registry names must exist too.
        missing_icons = []
        for icon in icons:
            path = "{}/{}".format(GUI, icon)
            if "-1" in run("import os\n"
                           "try:\n    os.stat('%s')\n    print(0)\n"
                           "except OSError:\n    print(-1)" % path).strip():
                missing_icons.append(icon)
        check("all APPS icons present", not missing_icons,
              "missing: {}".format(missing_icons) if missing_icons else
              "{} checked".format(len(icons)))

        # 5) The deployed launcher must be the same bake version, otherwise the board
        #    loads stale bitmaps and ignores freshly written caches.
        line = run("import re\n"
                   "print(re.search(r'BAKE_VER\\s*=\\s*(\\d+)',"
                   "open('%s/launcher.py').read()).group(1))" % GUI).strip()
        check("board launcher BAKE_VER", line.isdigit() and int(line) == ver,
              "board={} local={}".format(line, ver))

        # 6) Tile caches must carry that same version byte, or every tile re-bakes at
        #    boot and the disk copy is pointless. Caches whose app no longer exists are
        #    only a warning: nothing loads them, they just waste card space.
        # No re on board: this MicroPython's re module has no re.S alias, and a line
        # scan is clearer anyway.
        out = run("import os\n"
                  "live = set()\n"
                  "lines = open('%s/launcher.py').read().split(chr(10))\n"
                  "inside = False\n"
                  "for ln in lines:\n"
                  "    if ln.startswith('APPS = ['):\n"
                  "        inside = True\n"
                  "    elif inside and ln.startswith(']'):\n"
                  "        break\n"
                  "    elif inside and '\"id\":' in ln:\n"
                  "        parts = ln.split('\"id\":', 1)[1].split('\"')\n"
                  "        if len(parts) > 1:\n"
                  "            live.add(parts[1])\n"
                  "bad, orphan = [], []\n"
                  "d = '%s/assets/cache'\n"
                  "for n in sorted(os.listdir(d)):\n"
                  "    if not n.startswith('tile_') or not n.endswith('.bin'):\n"
                  "        continue\n"
                  "    app = n[5:-4]\n"
                  "    f = open(d + '/' + n, 'rb')\n"
                  "    h = f.read(6)\n"
                  "    f.close()\n"
                  "    if len(h) == 6 and h[:5] == b'TILE2' and h[5] == %d:\n"
                  "        continue\n"
                  "    if app in live:\n"
                  "        bad.append(app)\n"
                  "    else:\n"
                  "        orphan.append(app)\n"
                  "print(sorted(bad))\n"
                  "print(sorted(orphan))" % (GUI, GUI, ver))
        lines = [ln for ln in out.splitlines() if ln.strip()]
        bad_tiles = lines[0] if lines else "[]"
        orphans = lines[1] if len(lines) > 1 else "[]"
        check("live tile caches match BAKE_VER", bad_tiles in ("[]", "''"),
              bad_tiles if bad_tiles not in ("[]", "''") else "all match")
        if orphans not in ("[]", "''"):
            print("[WARN] {:34s} {}".format(
                "orphan tile caches", "{} (app gone, safe to delete)".format(orphans)))

        # 7) Import the packages the launcher needs, without starting a UI.
        out = run("import sys\n"
                  "sys.path.insert(0, '%s')\n"
                  "for m in ['ui.paging', 'ui.beep', 'net.service', 'hardware.battery',\n"
                  "          'core.paths', 'core.config', 'hardware.camera_fov']:\n"
                  "    try:\n"
                  "        __import__(m)\n"
                  "        print('OK', m)\n"
                  "    except Exception as e:\n"
                  "        print('NO', m, e)" % GUI)
        bad_imports = [ln for ln in out.splitlines() if ln.startswith("NO")]
        check("launcher imports resolve", not bad_imports,
              bad_imports or "ui/net/batt ok")
    finally:
        try:
            board.exit_raw_repl()
        except BaseException:
            pass
        try:
            board.close()
        except BaseException:
            pass

    if failures:
        print("\n[DOCTOR] {} check(s) failed.".format(len(failures)))
        return 1
    print("\n[DOCTOR] all checks passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
