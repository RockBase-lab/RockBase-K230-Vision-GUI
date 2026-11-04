# Getting or building the RockBase firmware image

English | [简体中文](firmware_zh.md)

This project ships **application-layer Python only**. The MicroPython firmware that
provides `lvgl`, `image`, `media`, `nncase_runtime`, `aidemo` and `ulab` is a separate
artifact: the CanMV build of the K230 RT-Smart SDK. This page covers where that image
comes from and how to build it for RockBase.

Everything below produces the **operating environment**. Two ways to get this
repository onto the board afterwards:

| | How | Use when |
| --- | --- | --- |
| Deploy after flashing | `python tools/deploy_board.py --gui-only --autostart` | Iterating: 14 seconds over USB, incremental |
| Bake into the image | `python tools/bake_factory_image.py` before flashing | Shipping: the card boots straight into the GUI |

## Which board configuration

The RockBase board has its own defconfig in the upstream RT-Smart SDK:

| Item | Value |
| --- | --- |
| Defconfig | `k230_canmv_rockbase_defconfig` |
| Board directory | `boards/k230_canmv_rockbase/` |
| `machine` reported by `os.uname()` | `k230_canmv_rockbase` |

On a board already flashed correctly, `os.uname()` is the quickest confirmation:

```
sysname='rt-smart'  nodename='rt-smart'  release='v1.8-70-gd6fe10e'
version='v1.8-70-gd6fe10e on 2026-10-28'  machine='k230_canmv_rockbase'
```

The release and date above are what this repository was verified against; a newer
build reports its own values while keeping `machine='k230_canmv_rockbase'`.

What that defconfig selects (read from the file itself, not from memory):

| Setting | Value | Meaning |
| --- | --- | --- |
| `CONFIG_SDK_ENABLE_CANMV` | `y` | Builds the MicroPython/CanMV application set rather than a bare RT-Smart system |
| `CONFIG_MPP_ENABLE_SENSOR_GC2093` | `y` | The camera this project uses |
| `CONFIG_MPP_ENABLE_SENSOR_OV5647`, `..._IMX335` | `y` | Alternate camera modules |
| `CONFIG_MPP_ENABLE_CSI_DEV_0/1/2` | `y` | All three CSI ports, with resets on 29, 31 and 13 |
| `CONFIG_MPP_ENABLE_DSI_LCD` | `y` | DSI panel output |
| `CONFIG_MPP_DSI_ENABLE_LCD_ST7701` | `y` | The 800x480 panel this project drives |
| `CONFIG_MPP_DSI_ENABLE_LCD_ILI9881` | `y` | Alternate panel |
| `CONFIG_MPP_DSI_ENABLE_HDMI_LT9611` | `y` | DSI-to-HDMI bridge |
| `CONFIG_MPP_ENABLE_SENSOR_AF_DRV_DW9714P` | `y` | Autofocus driver |
| `CONFIG_CANMV_OPENMV_CFG_FB_ALLOC_SIZE` | `0x1400000` | 20 MB framebuffer pool |
| `CONFIG_BOARD_CONFIG_NAME` | `k230_canmv_rockbase_defconfig` | Board identity baked into the image |

## Option 1: download a prebuilt image

Two upstream sources, both maintained by Canaan:

| Source | Where | Notes |
| --- | --- | --- |
| Daily build | `https://kendryte-download.canaan-creative.com/developer/releases/canmv_k230_micropython/daily_build/` | Newest development snapshot; only the latest build is kept |
| Stable release | `https://www.kendryte.com/resource` under the `K230/Images` category | Look for a file whose name contains `RTSmart`; the download is gzip-compressed |

Prebuilt images are named by board, so confirm the file mentions RockBase before
flashing. Daily builds may or may not have been rebuilt with this board configuration
on the day you download; if `os.uname()` reports a different `machine` value, fall back
to a source build (option 2).

## Option 2: build from source

Requirements:

- Ubuntu 20.04 or 22.04 LTS (a Docker route is documented in the upstream
  [`BUILD.md`](https://github.com/kendryte/k230_rtos_sdk/blob/main/BUILD.md) if you would
  rather not touch the host)
- Network access to GitHub or the Gitee mirror during `repo sync`
- About 10 GB of disk for one checkout plus its build output
  (measured: 4.9 GB of source, 2.9 GB under `output/`)

Host packages:

```bash
sudo apt-get install -y --no-install-recommends \
  make autoconf automake bison flex gcc g++ gawk libncurses5-dev pkg-config \
  libconfuse-dev libssl-dev python3 python3-pip python-is-python3 \
  cmake libyaml-dev scons mtools bzip2 curl git openssh-client \
  rsync dosfstools ca-certificates

pip3 install pycryptodome gmssl scons==3.1.2
```

The `python` command is not optional. U-Boot's firmware-header step calls `python`
directly, so a host that only provides `python3` stops there with:

```
/bin/sh: 1: python: not found
Make uboot.img
dd: failed to open 'k230-u-boot-spl.bin': No such file or directory
make[7]: *** [arch/riscv/cpu/k230/config.mk:18: add_firmware_head] Error 1
```

`python-is-python3` provides it. A `python -> python3` symlink earlier on `PATH`
achieves the same without root.

The `repo` tool:

```bash
mkdir -p ~/.bin
export PATH="${HOME}/.bin:${PATH}"
curl https://storage.googleapis.com/git-repo-downloads/repo > ~/.bin/repo
chmod a+rx ~/.bin/repo
```

Fetch the sources:

```bash
# over https
repo init -u https://github.com/canmv-k230/manifest -b master \
  --repo-url=https://github.com/canmv-k230/git-repo.git
# or over ssh, with your key already configured
# repo init -u git@gitee.com:canmv-k230/manifest.git -b master \
#   --repo-url=git@gitee.com:canmv-k230/git-repo.git

repo sync
```

Then build the RockBase configuration:

```bash
make dl_toolchain                       # first build only
make k230_canmv_rockbase_defconfig      # selects the board
time make log                           # builds, writes output/<defconfig>/log.txt
```

`make list-def` opens an interactive selector instead (left/right chooses Arduino,
CanMV or RT-Smart; tab chooses K230 or K230D; up/down chooses the board). With no TTY it
only prints the list, which is handy for confirming the defconfig name exists in your
checkout.

Artifacts land directly in the per-configuration output directory, **not** under
`images/`:

```
output/k230_canmv_rockbase_defconfig/
├── CanMV_K230_RockBase_micropython_local_nncase_v2.11.0.img        640 MiB  flash this
├── CanMV_K230_RockBase_micropython_local_nncase_v2.11.0.kdimg      540 MiB  Windows tool
├── CanMV_K230_RockBase_micropython_local_nncase_v2.11.0.img.gz              gzip of the .img
└── CanMV_K230_RockBase_micropython_local_nncase_v2.11.0_ota.kdimg  9.7 MiB  rtt + rtapp only
```

The name is assembled as
`<CONFIG_BOARD_NAME>_micropython_<canmv_revision>_nncase_v<nncase_version>`, so an
ordinary local build always reads `..._micropython_local_...`. `images/` holds only the
intermediate staging trees (`uboot/`, `rtsmart/`, `sdcard/`, ...) that the packer reads;
the finished image is never written there.

The image is generated from `boards/k230_canmv_rockbase/genimage-sdcard.cfg`, which
places the boot chain and then two FAT16 partitions: `BIN` (30 MiB) and `SDCARD`
(500 MiB, mounted as `/sdcard`). With the reference examples, `libs/` and `res/`
included, `/sdcard` occupies about 403 MiB and leaves roughly 76 MiB free.

`_ota.kdimg` carries only the `rtt` and `rtapp` partitions. It does **not** contain the
`SDCARD` partition, so an OTA upgrade never touches `/sdcard`.

## Where the `/sdcard` content comes from

`images/sdcard/` is the staging tree: every file in it ends up in the `SDCARD`
partition. `src/canmv/Makefile` fills it in this order:

| Step | Source |
| --- | --- |
| `copy_micropython` | the stripped `micropython` binary |
| `copy_libs` | `src/canmv/resources/libs/` |
| `copy_freetype_fonts` | `src/canmv/resources/font/` |
| `copy_examples` | `src/canmv/resources/examples/`, plus the kmodels the examples load |
| `copy_sdcard` | `src/canmv/resources/sdcard/`, only when that directory exists |

`copy_sdcard` runs a top-level `rsync --delete` whose filters protect only `.git`,
`micropython`, `__pycache__` and `*.pyc`. Anything placed in
`src/canmv/resources/sdcard/` therefore **deletes** `examples/`, `libs/`, `res/` and
`revision.txt` from the staging tree unless the same filters also protect them; guard
those paths before using that hook.

A full `make` starts with `rm -rf output/<defconfig>/images`, so content copied into
`images/sdcard/` by hand survives only until the next build. To re-pack without
recompiling, copy into `images/sdcard/` and run `tools/gen_image.sh` with the values
from `output/<defconfig>/.config` in its environment. That script names its output from
`MK_IMAGE_NAME`, which comes from `CONFIG_BOARD_NAME`; without it the image is written
as `_micropython_local_...`.

## Baking the GUI into the image

A pristine image has no `/sdcard/main.py`, so a freshly flashed board boots into
the REPL and leaves the panel dark. `tools/bake_factory_image.py` stages this
repository into the image and writes that entry point:

```bash
make -j$(nproc) log                     # produces output/<defconfig>/images/
python tools/bake_factory_image.py      # from the repository root
```

It runs inside WSL (the toolchain lives there) and does four things:

1. `rsync --delete factory/gui` into `output/<defconfig>/images/sdcard/factory/gui`,
   skipping `README.md`, `__pycache__` and `*.pyc`;
2. writes `images/sdcard/main.py`, the three-line autostart stub;
3. checks the staged tree still fits the 500 MiB `SDCARD` partition;
4. re-packs with the SDK's own `gen_image.sh`.

`--check` reports the staged state and changes nothing. `--publish DIR` also
copies the resulting `.img`, as `<image>-<suffix>.img`, and checks it against the
source MD5. The suffix defaults to `with-gui-<GUI_VERSION>` read from
`factory/gui/core/version.py`, so a published file names the GUI it carries:
`...with-gui-v0.1.01.img`. Only the `.img` is copied, because that is the file
`dd` writes; leaving the `.kdimg` out keeps a directory of candidates from
leading to a wrong flash.

Two build-system details make this safe. `images/` is wiped by the next `make`,
so the bake has to run after the last build. And `gen_image_rtapp.py` reads the
staged `micropython` binary, which `romfs.toml` moves into ROMFS while building;
running that generator again finds nothing and substitutes a 4 KB fake, which
produces an image that boots to nothing. The tool therefore never re-runs it
unless `rtapp.elf.gz` is already under 1 MB, and then it restores the binary from
`canmv/micropython` first.

After a bake, `images/sdcard` holds `factory/` and `main.py` alongside
`examples/`, `libs/` and `res/`. `/sdcard` ends up about 433 MiB used of 500 MiB.

## Flashing

Linux:

```bash
IMG=CanMV_K230_RockBase_micropython_local_nncase_v2.11.0.img
gunzip -k "$IMG.gz"       # a source build already leaves the .img uncompressed
sudo dd if="$IMG" of=/dev/sdX bs=1M status=progress oflag=sync
```

Confirm `/dev/sdX` is the card, not the disk you booted from: `lsblk` before and after
inserting the card is the safe check.

Windows: use Canaan's flashing tool rather than writing raw bytes; it takes the
`.kdimg` and its `.gz`. The upstream guide is
<https://www.kendryte.com/k230_rtos/zh/main/userguide/how_to_flash.html>.

## After flashing

The image brings the operating environment and the reference media:

- `/sdcard/examples/kmodel/` — 56 kmodels, about 337 MiB; every vision app in this
  project loads its models from here
- `/sdcard/examples/utils/` — anchor and helper data used by the examples
- `/sdcard/examples/` — the upstream example set
- `/sdcard/libs/`, `/sdcard/res/font/` — the Python libraries and fonts the examples
  import

It does **not** contain this project's GUI, and it does **not** contain
`/sdcard/main.py`: a pristine image boots into the MicroPython REPL with the panel
dark. Either bake the GUI in first (see above), or deploy it after flashing and
reset:

```bash
python tools/_doctor.py COM9
python tools/deploy_board.py COM9 --gui-only --autostart
```

A soft reset is enough for the first boot after autostart is written; use
`machine.reset()` (or `python tools/probes/_reset_hard.py`) when only a hard reset
releases a sensor the GUI still owns.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `make <defconfig>` says the target does not exist | The checkout predates the RockBase board support (upstream PR #19) | `repo sync` again, or check `git log --oneline -1 -- configs/k230_canmv_rockbase_defconfig` in the SDK tree |
| Build dies with `/bin/sh: 1: python: not found` right after `Make uboot.img`, then `dd: failed to open 'k230-u-boot-spl.bin'` | `python-is-python3` is missing; U-Boot's firmware-header step calls `python` | Install `python-is-python3`, or put a `python -> python3` symlink earlier on `PATH` |
| Build fails immediately on Python packages | `scons` version mismatch | `pip3 install scons==3.1.2` |
| `repo sync` stops on a network error | Mirror or proxy issue | Re-run `repo sync`; it resumes. The Gitee manifest is the alternative to the GitHub one |
| The build succeeded but `images/sysimage-sdcard.img` does not exist | `images/` only holds intermediate staging trees; the image is written one level up | Flash `output/<defconfig>/CanMV_K230_RockBase_micropython_local_nncase_v<ver>.img` |
| Flashed image boots but `os.uname()` reports another board | Wrong defconfig was built or the wrong image was flashed | Rebuild with `k230_canmv_rockbase_defconfig` |
| Image boots, but the panel stays dark | Backlight is a separate GPIO this GUI drives itself; a bare RT-Smart image without this project's code leaves the panel unlit | Deploy this project; see the README |
| `/sdcard/examples/kmodel/` missing | The image was built with a different defconfig, or `copy_sdcard` deleted the staging tree | Re-flash a complete image and check the build log for `Copy examples` |
| `/sdcard` reports no free space | The partition is a fixed 500 MiB FAT16 and the reference examples already take about 403 MiB | Remove unused example directories, or grow `app.vfat` in `genimage-sdcard.cfg` |

## Upstream references

- [K230 RTOS SDK](https://github.com/kendryte/k230_rtos_sdk) — this build system, board
  configurations and `BUILD.md`
- [CanMV K230](https://github.com/kendryte/canmv_k230) — the MicroPython port itself
- [RTOS documentation](https://www.kendryte.com/k230_rtos/zh/main/index.html) — build,
  flash and usage guides
- Manifest and toolchain: `canmv-k230/manifest`, `canmv-k230/git-repo`
