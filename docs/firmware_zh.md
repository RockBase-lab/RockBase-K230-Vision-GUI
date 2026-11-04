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
| `CONFIG_MPP_DSI_ENABLE_LCD_LI9881` | `y` | 备选屏 |
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

主机依赖：

```bash
sudo apt-get install -y --no-install-recommends \
  make autoconf automake bison flex gcc g++ gawk libncurses5-dev pkg-config \
  libconfuse-dev libssl-dev python3 python3-pip python-is-python3 \
  cmake libyaml-dev scons mtools bzip2 curl git openssh-client \
  rsync dosfstools ca-certificates

pip3 install pycryptodome gmssl scons==3.1.2
```

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

产物落在按配置划分的输出目录：

```
output/k230_canmv_rockbase_defconfig/images/
└── sysimage-sdcard.img          # flash this to the SD card or eMMC
```

镜像由 `boards/k230_canmv_rockbase/genimage-sdcard.cfg` 生成，先放启动链，再放两个
FAT 分区：`BIN` 与 `SDCARD`。系统启动后把 `SDCARD` 分区挂载为 `/sdcard`。

## 烧录

Linux：

```bash
gunzip -c sysimage-sdcard.img.gz > sysimage-sdcard.img   # only for a .gz download
sudo dd if=sysimage-sdcard.img of=/dev/sdX bs=1M status=progress oflag=sync
```

确认 `/dev/sdX` 是那张卡、不是你启动用的硬盘：插卡前后各跑一次 `lsblk` 比对最稳妥。

Windows：用嘉楠的烧录工具，不要直接写裸字节。上游指南：
<https://www.kendryte.com/k230_rtos/zh/main/userguide/how_to_flash.html>。

## 刷完之后

镜像带来运行环境与参考素材：

- `/sdcard/examples/kmodel/`、`/sdcard/examples/utils/` —— 视觉类应用加载的 kmodel
  与锚点数据
- `/sdcard/examples/` —— 上游示例集
- `/sdcard/main.py` —— 启动入口

它**不包含**本项目的 GUI。那部分要单独部署，然后复位：

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
| 编译在 Python 包处立刻失败 | `scons` 版本不匹配 | `pip3 install scons==3.1.2` |
| `repo sync` 因网络中断 | 镜像源或代理问题 | 重跑 `repo sync`，它会续传；GitHub manifest 不通时改用 Gitee 那份 |
| 刷完能启动，但 `os.uname()` 报告别的板型 | 编错了 defconfig，或刷错了镜像 | 用 `k230_canmv_rockbase_defconfig` 重编 |
| 刷完能启动，但屏幕不亮 | 背光是本 GUI 自己驱动的独立 GPIO；不含本项目代码的裸 RT-Smart 镜像不会点亮面板 | 部署本项目，见 README |
| `/sdcard/examples/kmodel/` 缺失 | 用了别的 defconfig，或镜像只写了一部分 | 重新刷完整的 `sysimage-sdcard.img` |

## 上游参考

- [K230 RTOS SDK](https://github.com/kendryte/k230_rtos_sdk) —— 本文用到的构建系统、
  板级配置与 `BUILD.md`
- [CanMV K230](https://github.com/kendryte/canmv_k230) —— MicroPython 移植本体
- [RTOS 文档](https://www.kendryte.com/k230_rtos/zh/main/index.html) —— 编译、烧录与使用指南
- Manifest 与工具链：`canmv-k230/manifest`、`canmv-k230/git-repo`
