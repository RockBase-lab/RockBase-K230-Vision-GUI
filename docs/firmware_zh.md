# 获取或编译 RockBase 镜像

[English](firmware.md) | 简体中文

本仓库只提供**应用层 Python 代码**。提供 `lvgl`、`image`、`media`、`nncase_runtime`、
`aidemo` 与 `ulab` 的 MicroPython 固件是另一个产物：K230 RT-Smart SDK 的 CanMV 构建版。
本文说明这个镜像从哪里来、以及如何为 RockBase 编译出来。

以下步骤产出的是**运行环境**，不是本仓库。刷好固件后，仍然要用
`python tools/deploy_board.py` 部署本项目（见 README）。

## 使用哪个板级配置

RockBase 在上游 RT-Smart SDK 中已有独立配置：

| 项目 | 值 |
| --- | --- |
| defconfig | `k230_canmv_rockbase_defconfig` |
| 板级目录 | `boards/k230_canmv_rockbase/` |
| `os.uname()` 报告的 `machine` | `k230_canmv_rockbase` |

板子已正确刷机时，`os.uname()` 是最快的确认方式：

```
sysname='rt-smart'  nodename='rt-smart'  release='v1.8-70-gd6fe10e'
version='v1.8-70-gd6fe10e on 2026-10-28'  machine='k230_canmv_rockbase'
```

上面的版本号与日期是本仓库的验证环境；更新的构建会报告自己的值，
但 `machine='k230_canmv_rockbase'` 应保持不变。

该 defconfig 选中的内容（读自配置文件本身，不是凭记忆）：

| 配置项 | 值 | 含义 |
| --- | --- | --- |
| `CONFIG_SDK_ENABLE_CANMV` | `y` | 构建 MicroPython / CanMV 应用集，而非裸 RT-Smart 系统 |
| `CONFIG_MPP_ENABLE_SENSOR_GC2093` | `y` | 本项目使用的摄像头 |
| `CONFIG_MPP_ENABLE_SENSOR_OV5647`、`..._IMX335` | `y` | 备选摄像头模组 |
| `CONFIG_MPP_ENABLE_CSI_DEV_0/1/2` | `y` | 三路 CSI，复位脚分别为 29、31、13 |
| `CONFIG_MPP_ENABLE_DSI_LCD` | `y` | DSI 屏输出 |
| `CONFIG_MPP_DSI_ENABLE_LCD_ST7701` | `y` | 本项目驱动的 800×480 屏 |
| `CONFIG_MPP_DSI_ENABLE_LCD_ILI9881` | `y` | 备选屏 |
| `CONFIG_MPP_DSI_ENABLE_HDMI_LT9611` | `y` | DSI 转 HDMI 桥 |
| `CONFIG_MPP_ENABLE_SENSOR_AF_DRV_DW9714P` | `y` | 自动对焦驱动 |
| `CONFIG_CANMV_OPENMV_CFG_FB_ALLOC_SIZE` | `0x1400000` | 20 MB 帧缓冲池 |
| `CONFIG_BOARD_CONFIG_NAME` | `k230_canmv_rockbase_defconfig` | 写入镜像的板级标识 |

## 方案一：下载预编译镜像

两个上游来源，均由嘉楠维护：

| 来源 | 地址 | 说明 |
| --- | --- | --- |
| 每日构建 | `https://kendryte-download.canaan-creative.com/developer/releases/canmv_k230_micropython/daily_build/` | 开发分支的最新快照；只保留最新一版 |
| 稳定版本 | `https://www.kendryte.com/resource` 的 `K230/Images` 分类 | 找文件名含 `RTSmart` 的镜像；下载包是 gzip 压缩 |

预编译镜像是按板型命名的，刷写前先确认文件名提到 RockBase。每日构建在下载当天下没下
RockBase 配置并不确定；如果 `os.uname()` 报告的 `machine` 不是 `k230_canmv_rockbase`，
就退回方案二的源码编译。

## 方案二：从源码编译

环境要求：

- Ubuntu 20.04 或 22.04 LTS（不想动本机环境的话，上游
  [`BUILD.md`](https://github.com/kendryte/k230_rtos_sdk/blob/main/BUILD.md) 里有 Docker 方式）
- `repo sync` 期间能访问 GitHub 或 Gitee 镜像
- 一份 checkout 加一次构建输出约需 10 GB 磁盘
  （实测：源码 4.9 GB，`output/` 2.9 GB）

主机依赖：

```bash
sudo apt-get install -y --no-install-recommends \
  make autoconf automake bison flex gcc g++ gawk libncurses5-dev pkg-config \
  libconfuse-dev libssl-dev python3 python3-pip python-is-python3 \
  cmake libyaml-dev scons mtools bzip2 curl git openssh-client \
  rsync dosfstools ca-certificates

pip3 install pycryptodome gmssl scons==3.1.2
```

`python` 命令不是可选项。U-Boot 加固件头的步骤直接调用 `python`，只有 `python3`
的主机会停在这里：

```
/bin/sh: 1: python: not found
Make uboot.img
dd: failed to open 'k230-u-boot-spl.bin': No such file or directory
make[7]: *** [arch/riscv/cpu/k230/config.mk:18: add_firmware_head] Error 1
```

装 `python-is-python3` 即可。在 `PATH` 靠前的位置放一个 `python -> python3` 软链，
不需要 root 也能达到同样效果。

`repo` 工具：

```bash
mkdir -p ~/.bin
export PATH="${HOME}/.bin:${PATH}"
curl https://storage.googleapis.com/git-repo-downloads/repo > ~/.bin/repo
chmod a+rx ~/.bin/repo
```

拉取源码：

```bash
# over https
repo init -u https://github.com/canmv-k230/manifest -b master \
  --repo-url=https://github.com/canmv-k230/git-repo.git
# or over ssh, with your key already configured
# repo init -u git@gitee.com:canmv-k230/manifest.git -b master \
#   --repo-url=git@gitee.com:canmv-k230/git-repo.git

repo sync
```

然后编译 RockBase 配置：

```bash
make dl_toolchain                       # first build only
make k230_canmv_rockbase_defconfig      # selects the board
time make log                           # builds, writes output/<defconfig>/log.txt
```

也可以用 `make list-def` 打开交互式选择器（左右键切换 Arduino / CanMV / RT-Smart，
Tab 切换 K230 / K230D，上下键选板型）。没有 TTY 时它只打印列表，适合用来确认自己的
checkout 里有没有这个 defconfig。

产物直接落在按配置划分的输出目录里，**不在** `images/` 下：

```
output/k230_canmv_rockbase_defconfig/
├── CanMV_K230_RockBase_micropython_local_nncase_v2.11.0.img        640 MiB  flash this
├── CanMV_K230_RockBase_micropython_local_nncase_v2.11.0.kdimg      540 MiB  Windows tool
├── CanMV_K230_RockBase_micropython_local_nncase_v2.11.0.img.gz              gzip of the .img
└── CanMV_K230_RockBase_micropython_local_nncase_v2.11.0_ota.kdimg  9.7 MiB  rtt + rtapp only
```

文件名拼装规则是
`<CONFIG_BOARD_NAME>_micropython_<canmv_revision>_nncase_v<nncase_version>`，所以
普通本地构建始终是 `..._micropython_local_...`。`images/` 里只有打包器要读的中间
暂存树（`uboot/`、`rtsmart/`、`sdcard/` 等），最终镜像从不写在那里。

镜像由 `boards/k230_canmv_rockbase/genimage-sdcard.cfg` 生成，先放启动链，再放两个
FAT16 分区：`BIN`（30 MiB）与 `SDCARD`（500 MiB，挂载为 `/sdcard`）。带上参考示例、
`libs/` 与 `res/` 之后，`/sdcard` 约占用 403 MiB，剩余约 76 MiB。

`_ota.kdimg` 只打包 `rtt` 与 `rtapp` 两个分区，**不含** `SDCARD` 分区，因此 OTA
升级永远不会改动 `/sdcard`。

## `/sdcard` 的内容从哪来

`images/sdcard/` 是暂存树：里面的每个文件都会进入 `SDCARD` 分区。
`src/canmv/Makefile` 按下面的顺序把它填满：

| 步骤 | 来源 |
| --- | --- |
| `copy_micropython` | strip 过的 `micropython` 二进制 |
| `copy_libs` | `src/canmv/resources/libs/` |
| `copy_freetype_fonts` | `src/canmv/resources/font/` |
| `copy_examples` | `src/canmv/resources/examples/`，以及示例加载的 kmodel |
| `copy_sdcard` | `src/canmv/resources/sdcard/`，仅当该目录存在时 |

`copy_sdcard` 跑的是顶层 `rsync --delete`，其 filter 只保护 `.git`、`micropython`、
`__pycache__` 与 `*.pyc`。因此往 `src/canmv/resources/sdcard/` 里放东西，会**删除**
暂存树中的 `examples/`、`libs/`、`res/` 与 `revision.txt`；用这个钩子之前必须先把
这些路径加进保护 filter。

完整 `make` 的第一步是 `rm -rf output/<defconfig>/images`，所以手工拷进
`images/sdcard/` 的内容只能活到下一次构建。想不重编只重打包，就把文件拷进
`images/sdcard/`，再用 `output/<defconfig>/.config` 的值作环境变量运行
`tools/gen_image.sh`。该脚本用 `MK_IMAGE_NAME`（来自 `CONFIG_BOARD_NAME`）给产物
命名，缺了它镜像会被写成 `_micropython_local_...`。

## 烧录

Linux：

```bash
IMG=CanMV_K230_RockBase_micropython_local_nncase_v2.11.0.img
gunzip -k "$IMG.gz"       # a source build already leaves the .img uncompressed
sudo dd if="$IMG" of=/dev/sdX bs=1M status=progress oflag=sync
```

确认 `/dev/sdX` 是那张卡、不是你启动用的硬盘：插卡前后各跑一次 `lsblk` 比对最稳妥。

Windows：用嘉楠的烧录工具，不要直接写裸字节；它吃 `.kdimg` 及其 `.gz`。上游指南：
<https://www.kendryte.com/k230_rtos/zh/main/userguide/how_to_flash.html>。

## 刷完之后

镜像带来运行环境与参考素材：

- `/sdcard/examples/kmodel/` —— 56 个 kmodel，约 337 MiB；本项目所有视觉类应用都从
  这里加载模型
- `/sdcard/examples/utils/` —— 示例使用的锚点与辅助数据
- `/sdcard/examples/` —— 上游示例集
- `/sdcard/libs/`、`/sdcard/res/font/` —— 示例 import 的 Python 库与字体

它**不包含**本项目的 GUI，也**不包含** `/sdcard/main.py`：纯净镜像启动后进入
MicroPython REPL，屏幕是黑的。先部署本项目，再复位：

```bash
python tools/_doctor.py COM9
python tools/deploy_board.py COM9 --gui-only --autostart
```

写入自启动后的第一次启动，软复位即可；只有硬复位才能释放 GUI 占着的 sensor，
这时用 `machine.reset()`（或 `python tools/probes/_reset_hard.py`）。

## 排障

| 现象 | 原因 | 处理 |
| --- | --- | --- |
| `make <defconfig>` 报目标不存在 | checkout 早于 RockBase 板级支持（上游 PR #19） | 重新 `repo sync`，或在 SDK 树里查 `git log --oneline -1 -- configs/k230_canmv_rockbase_defconfig` |
| 编译在 `Make uboot.img` 之后报 `/bin/sh: 1: python: not found`，紧接着 `dd: failed to open 'k230-u-boot-spl.bin'` | 缺 `python-is-python3`；U-Boot 加固件头的步骤调用 `python` | 装 `python-is-python3`，或在 `PATH` 靠前处放 `python -> python3` 软链 |
| 编译在 Python 包处立刻失败 | `scons` 版本不匹配 | `pip3 install scons==3.1.2` |
| `repo sync` 因网络中断 | 镜像源或代理问题 | 重跑 `repo sync`，它会续传；GitHub manifest 不通时改用 Gitee 那份 |
| 编译“成功”但找不到 `images/sysimage-sdcard.img` | `images/` 只有中间暂存树，镜像写在上一级目录 | 刷 `output/<defconfig>/CanMV_K230_RockBase_micropython_local_nncase_v<ver>.img` |
| 刷完能启动，但 `os.uname()` 报告别的板型 | 编错了 defconfig，或刷错了镜像 | 用 `k230_canmv_rockbase_defconfig` 重编 |
| 刷完能启动，但屏幕不亮 | 背光是本 GUI 自己驱动的独立 GPIO；不含本项目代码的裸 RT-Smart 镜像不会点亮面板 | 部署本项目，见 README |
| `/sdcard/examples/kmodel/` 缺失 | 用了别的 defconfig，或 `copy_sdcard` 删掉了暂存树 | 重刷完整镜像，并在构建日志里确认有 `Copy examples` |
| `/sdcard` 报空间不足 | 该分区固定 500 MiB FAT16，参考示例已占约 403 MiB | 删掉不用的示例目录，或调大 `genimage-sdcard.cfg` 里的 `app.vfat` |

## 上游参考

- [K230 RTOS SDK](https://github.com/kendryte/k230_rtos_sdk) —— 本文用到的构建系统、
  板级配置与 `BUILD.md`
- [CanMV K230](https://github.com/kendryte/canmv_k230) —— MicroPython 移植本体
- [RTOS 文档](https://www.kendryte.com/k230_rtos/zh/main/index.html) —— 编译、烧录与使用指南
- Manifest 与工具链：`canmv-k230/manifest`、`canmv-k230/git-repo`
