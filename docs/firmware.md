# Getting or building the RockBase firmware image

This project ships **application-layer Python only**. The MicroPython firmware that
provides `lvgl`, `image`, `media`, `nncase_runtime`, `aidemo` and `ulab` is a separate
artifact: the CanMV build of the K230 RT-Smart SDK. This page covers where that image
comes from and how to build it for RockBase.

Everything below produces the **operating environment**, not this repository. After
flashing, deploy this project with `python tools/deploy_board.py` (see the README).

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
| `CONFIG_MPP_DSI_ENABLE_LCD_LI9881` | `y` | Alternate panel |
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

Host packages:

```bash
sudo apt-get install -y --no-install-recommends \
  make autoconf automake bison flex gcc g++ gawk libncurses5-dev pkg-config \
  libconfuse-dev libssl-dev python3 python3-pip python-is-python3 \
  cmake libyaml-dev scons mtools bzip2 curl git openssh-client \
  rsync dosfstools ca-certificates

pip3 install pycryptodome gmssl scons==3.1.2
```

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

Artifacts land in the per-configuration output directory:

```
output/k230_canmv_rockbase_defconfig/images/
└── sysimage-sdcard.img          # flash this to the SD card or eMMC
```

The image is generated from `boards/k230_canmv_rockbase/genimage-sdcard.cfg`, which
places the boot chain and then two FAT partitions: `BIN` and `SDCARD`. The `SDCARD`
partition is what the running system mounts as `/sdcard`.

## Flashing

Linux:

```bash
gunzip -c sysimage-sdcard.img.gz > sysimage-sdcard.img   # only for a .gz download
sudo dd if=sysimage-sdcard.img of=/dev/sdX bs=1M status=progress oflag=sync
```

Confirm `/dev/sdX` is the card, not the disk you booted from: `lsblk` before and after
inserting the card is the safe check.

Windows: use Canaan's flashing tool rather than writing raw bytes. The upstream guide is
<https://www.kendryte.com/k230_rtos/zh/main/userguide/how_to_flash.html>.

## After flashing

The image brings the operating environment and the reference media:

- `/sdcard/examples/kmodel/`, `/sdcard/examples/utils/` — the kmodels and anchor data the
  vision applications load
- `/sdcard/examples/` — the upstream example set
- `/sdcard/main.py` — the boot entry

It does **not** contain this project's GUI. Deploy that separately, then reset:

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
| Build fails immediately on Python packages | `scons` version mismatch | `pip3 install scons==3.1.2` |
| `repo sync` stops on a network error | Mirror or proxy issue | Re-run `repo sync`; it resumes. The Gitee manifest is the alternative to the GitHub one |
| Flashed image boots but `os.uname()` reports another board | Wrong defconfig was built or the wrong image was flashed | Rebuild with `k230_canmv_rockbase_defconfig` |
| Image boots, but the panel stays dark | Backlight is a separate GPIO this GUI drives itself; a bare RT-Smart image without this project's code leaves the panel unlit | Deploy this project; see the README |
| `/sdcard/examples/kmodel/` missing | The image was built with a different defconfig, or only a partial image was written | Re-flash the full `sysimage-sdcard.img` |

## Upstream references

- [K230 RTOS SDK](https://github.com/kendryte/k230_rtos_sdk) — this build system, board
  configurations and `BUILD.md`
- [CanMV K230](https://github.com/kendryte/canmv_k230) — the MicroPython port itself
- [RTOS documentation](https://www.kendryte.com/k230_rtos/zh/main/index.html) — build,
  flash and usage guides
- Manifest and toolchain: `canmv-k230/manifest`, `canmv-k230/git-repo`
