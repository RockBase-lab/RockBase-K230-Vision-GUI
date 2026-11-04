"""Bake the factory GUI into the RockBase image.

The RT-Smart image ships the operating environment only: /sdcard holds the
reference examples, libs and fonts, and there is no main.py, so a freshly flashed
board boots into the REPL with a dark panel. This tool copies factory/gui into
the staged SDCARD tree, writes the autostart stub, and re-packs the image.

It does not compile anything. Run `make -j$(nproc) log` in the SDK first: that
produces output/<defconfig>/images/, which is what gets packed. A later `make`
starts with `rm -rf images/`, so this step has to run after the last build.

Usage (from the repository root, on Windows or inside WSL):

    python tools/bake_factory_image.py
    python tools/bake_factory_image.py --sdk ~/rockbase-rtos
    python tools/bake_factory_image.py --check          # verify only, change nothing

The SDK lives inside WSL because the cross toolchain and the build host are
there. On Windows this tool routes through `wsl`; inside WSL it runs directly.
"""

import argparse
import os
import shutil
import subprocess
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
GUI_SRC = os.path.join(ROOT, "factory", "gui")

DEFAULT_SDK = "~/rockbase-rtos"
DEFAULT_DEFCONFIG = "k230_canmv_rockbase_defconfig"

# The /sdcard partition size from boards/<board>/genimage-sdcard.cfg. genimage
# fails once the staged tree exceeds it, so check first and say why.
SDCARD_PARTITION_MB = 500

# Files the board never needs. Mirrors tools/deploy_board.py so the image and an
# incremental deploy contain the same Python set.
EXCLUDES = ("__pycache__", "*.pyc", "*.pyo", "*.bak", "README.md")

AUTOSTART_MAIN = (
    "# Factory-baked autostart entry: run the RockBase GUI launcher.\n"
    "import os\n"
    "exec(open('/sdcard/factory/gui/launcher.py').read())\n"
)


def to_wsl_path(path):
    """Translate a Windows path to its /mnt/<drive> form; leave POSIX paths alone."""
    path = os.path.abspath(path).replace("\\", "/")
    if len(path) > 2 and path[1] == ":":
        return "/mnt/" + path[0].lower() + path[2:]
    return path


def in_wsl():
    """True when the interpreter already runs on Linux inside WSL."""
    if os.name != "posix":
        return False
    try:
        with open("/proc/version") as fh:
            return "microsoft" in fh.read().lower()
    except OSError:
        return False


def wsl_available():
    return shutil.which("wsl") is not None


# WSL and the SDK emit UTF-8. Windows would decode with the ANSI codepage (GBK on
# this machine) and die on the first non-ASCII byte, so every child process below
# states the encoding instead of inheriting it.
CHILD_ENCODING = "utf-8"


def run(cmd, cwd=None, timeout=3600):
    return subprocess.run(cmd, cwd=cwd, capture_output=True, text=True,
                          encoding=CHILD_ENCODING, errors="replace",
                          timeout=timeout)


def build_script(repo_gui, sdk, defconfig, check_only):
    """The shell script executed inside WSL. Kept as text so the logic is readable."""
    return r"""
set -u
SDK="__SDK__"
# A quoted ~ is not expanded by the shell, and --sdk defaults to ~/rockbase-rtos.
case "$SDK" in
  "~"|"~/"*) SDK="$HOME${SDK#\~}" ;;
esac
DEFCONFIG="__DEFCONFIG__"
GUI_SRC="__GUI_SRC__"
PART_MB=__PART_MB__
CHECK_ONLY=__CHECK_ONLY__

die() { echo "[FAIL] $*" >&2; exit 1; }

[ -d "$SDK" ] || die "SDK not found at $SDK (pass --sdk)"
OUT="$SDK/output/$DEFCONFIG"
[ -d "$OUT" ] || die "no build output at $OUT; run 'make -j\$(nproc) log' in $SDK first"
[ -d "$OUT/images/sdcard" ] || die "$OUT/images/sdcard missing; run make first"
[ -d "$GUI_SRC" ] || die "GUI source not found at $GUI_SRC"

STAGE="$OUT/images/sdcard"

echo "[INFO] SDK        : $SDK"
echo "[INFO] defconfig   : $DEFCONFIG"
echo "[INFO] staged tree : $STAGE"

before=$(du -sm "$STAGE" | cut -f1)
echo "[INFO] staged size  : ${before} MB of ${PART_MB} MB"

if [ "$CHECK_ONLY" = "1" ]; then
    echo "[CHECK] factory/gui present : $([ -d "$STAGE/factory/gui" ] && echo yes || echo no)"
    echo "[CHECK] main.py present     : $([ -f "$STAGE/main.py" ] && echo yes || echo no)"
    if [ -f "$STAGE/main.py" ]; then
        echo "[CHECK] main.py content     :"
        sed 's/^/         /' "$STAGE/main.py"
    fi
    exit 0
fi

# ---- 1. stage factory/gui ------------------------------------------------
# --delete so a module removed from the repository also leaves the image; the
# plain additive copy would keep a stale layout next to the current one.
mkdir -p "$STAGE/factory"
rsync -a --delete __EXCLUDES__ "$GUI_SRC/" "$STAGE/factory/gui/" \
    || die "rsync of factory/gui failed"
gui_files=$(find "$STAGE/factory/gui" -type f | wc -l)
gui_mb=$(du -sm "$STAGE/factory/gui" | cut -f1)
echo "[OK]   factory/gui  : $gui_files files, ${gui_mb} MB"

# ---- 2. autostart stub ---------------------------------------------------
printf '%b' "__AUTOSTART__" > "$STAGE/main.py" || die "cannot write main.py"
echo "[OK]   main.py     : $(wc -c < "$STAGE/main.py") bytes"

# ---- 3. the partition must still fit ------------------------------------
after=$(du -sm "$STAGE" | cut -f1)
echo "[INFO] staged size  : ${after} MB of ${PART_MB} MB"
[ "$after" -lt "$PART_MB" ] || die "staged /sdcard is ${after} MB, over the ${PART_MB} MB partition"

# ---- 4. the RT app must be the real one ---------------------------------
# gen_image_rtapp.py reads CONFIG_FAST_BOOT_FILE_PATH, which points at the staged
# micropython binary. romfs.toml moves that binary into ROMFS while building, so
# re-running the generator AFTER a successful build finds nothing and silently
# substitutes a 4 KB all-zero fake, which yields an image that boots to nothing.
# This tool therefore never runs the generator unless the artefact is already a
# fake, and then it restores the binary from the build tree first.
RTAPP="$OUT/images/rtapp/rtapp.elf.gz"
rtapp_bytes=$(stat -c%s "$RTAPP" 2>/dev/null || echo 0)
[ "$rtapp_bytes" -gt 0 ] || die "$RTAPP missing; run 'make -j\$(nproc) log' first"
if [ "$rtapp_bytes" -lt 1048576 ]; then
    echo "[WARN] rtapp.elf.gz is only ${rtapp_bytes} B (the fake); rebuilding it"
    [ -f "$OUT/canmv/micropython" ] || \
        die "cannot rebuild rtapp: $OUT/canmv/micropython is gone; run a full make"
    cp "$OUT/canmv/micropython" "$STAGE/micropython"
    ( cd "$SDK" && SDK_SRC_ROOT_DIR="$SDK" SDK_BUILD_IMAGES_DIR="$OUT/images" \
        python3 tools/gen_image_rtapp.py ) || die "rtapp rebuild failed"
    rtapp_bytes=$(stat -c%s "$RTAPP")
    [ "$rtapp_bytes" -gt 1048576 ] || die "rtapp is still a fake (${rtapp_bytes} B)"
fi
echo "[OK]   rtapp       : ${rtapp_bytes} bytes"

# ---- 5. re-pack ---------------------------------------------------------
# gen_image.sh sources .config itself; export the variables mkenv.mk would
# normally supply. MK_IMAGE_NAME comes from CONFIG_BOARD_NAME and is what keeps
# the artefact named CanMV_K230_RockBase_... instead of "_micropython_...".
cd "$SDK" || die "cannot cd to $SDK"

# Export the paths first: .config itself references ${SDK_BUILD_IMAGES_DIR}, and
# `set -u` would abort on that before the value existed.
export SDK_SRC_ROOT_DIR="$SDK"
export SDK_BUILD_DIR="$OUT"
export SDK_BUILD_IMAGES_DIR="$OUT/images"
export SDK_TOOLS_DIR="$SDK/tools"
export SDK_RTSMART_SRC_DIR="$SDK/src/rtsmart"
export SDK_CANMV_SRC_DIR="$SDK/src/canmv"
export SDK_UBOOT_SRC_DIR="$SDK/src/uboot"
export SDK_OPENSBI_SRC_DIR="$SDK/src/opensbi"
export SDK_APPS_SRC_DIR="$SDK/src/applications"

source ./.config

export SDK_BOARD_DIR="$SDK/boards/$CONFIG_BOARD"
export MK_IMAGE_NAME="$CONFIG_BOARD_NAME"

"$SDK/tools/gen_image.sh" || die "image generation failed"
"""


def render(script, gui_src, sdk, defconfig, check_only):
    return (script
            .replace("__SDK__", sdk)
            .replace("__DEFCONFIG__", defconfig)
            .replace("__GUI_SRC__", gui_src)
            .replace("__PART_MB__", str(SDCARD_PARTITION_MB))
            .replace("__CHECK_ONLY__", "1" if check_only else "0")
            .replace("__EXCLUDES__", " ".join(
                "--exclude='%s'" % e for e in EXCLUDES))
            .replace("__AUTOSTART__", AUTOSTART_MAIN.replace("\n", "\\n")))


def main():
    ap = argparse.ArgumentParser(
        description="Stage factory/gui into the SDCARD tree and re-pack the image.")
    ap.add_argument("--sdk", default=DEFAULT_SDK,
                    help="SDK checkout (default %s)" % DEFAULT_SDK)
    ap.add_argument("--defconfig", default=DEFAULT_DEFCONFIG,
                    help="build output to use (default %s)" % DEFAULT_DEFCONFIG)
    ap.add_argument("--check", action="store_true",
                    help="report the staged state and exit without changing it")
    args = ap.parse_args()

    if not os.path.isdir(GUI_SRC):
        sys.exit("[FAIL] %s not found; run from the repository root" % GUI_SRC)

    sdk = args.sdk
    gui = to_wsl_path(GUI_SRC)
    script = render(build_script(gui, sdk, args.defconfig, args.check),
                    gui, sdk, args.defconfig, args.check)

    if in_wsl():
        cmd = ["bash", "-s"]
    elif wsl_available():
        cmd = ["wsl", "-d", "Ubuntu-22.04", "--", "bash", "-s"]
    else:
        sys.exit("[FAIL] neither WSL nor a Linux host is available")

    print("[RUN] %s" % ("checking" if args.check else "staging and packing"))
    # The script goes in on stdin. Passing it as an argument to `bash -c` lets
    # wsl.exe re-split the command line, which silently breaks a multi-line script
    # (a line becomes its own command, so variables end up empty).
    #
    # Bytes, not text: on Windows `text=True` translates \n to \r\n on the way in,
    # and bash then reads the CR as part of the command ("$'\r': command not found").
    proc = subprocess.run(cmd, input=script.encode(CHILD_ENCODING),
                          capture_output=True)

    out = (proc.stdout or b"").decode(CHILD_ENCODING, "replace").strip()
    err = (proc.stderr or b"").decode(CHILD_ENCODING, "replace").strip()
    if out:
        print(out)
    if err:
        # genimage and the SDK write their progress to stderr, so only lines that
        # carry no INFO/WARN marker are reported as errors.
        for line in err.split("\n"):
            if not line.strip():
                continue
            if "INFO:" in line or line.startswith("WARN"):
                print("        " + line)
            else:
                print("[ERR ] " + line)

    if proc.returncode != 0:
        sys.exit(proc.returncode or 1)
    if args.check:
        return

    # Report the artefacts that were just written, newest first. A quoted ~ is not
    # expanded, so turn the SDK argument into an absolute path first.
    list_script = (
        'S=%s\n'
        'case "$S" in "~"|"~/"*) S="$HOME${S#\\~}" ;; esac\n'
        'ls -lt "$S/output/%s"/*.img "$S/output/%s"/*.kdimg 2>/dev/null | head -4\n'
        % (args.sdk, args.defconfig, args.defconfig))
    listing = subprocess.run(
        (["bash", "-s"] if in_wsl()
         else ["wsl", "-d", "Ubuntu-22.04", "--", "bash", "-s"]),
        input=list_script.encode(CHILD_ENCODING), capture_output=True)
    print()
    print("[DONE] flashable artefacts:")
    for line in (listing.stdout or b"").decode(CHILD_ENCODING, "replace").split("\n"):
        if line.strip():
            print("  " + line)


if __name__ == "__main__":
    main()
