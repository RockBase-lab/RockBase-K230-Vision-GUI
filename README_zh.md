# K230 视觉识别模块

简体中文 | [English](README.md)

RockBase IoT K230-Vision 视觉识别模块基于嘉楠科技新一代 K230 AI芯片设计的高性能AIoT开发板，面向开发者、教育场景与 AIoT 产品集成。模块将高性能视觉推理、多模态 AI 能力、灵活的主控通信方式和实用安装扩展组合在紧凑的带外壳系统中。可选3.5英寸电容触摸屏（800*480）。K230搭载双核RISC-V处理器（大核1.6GHz + 小核0.8GHz）和约6TOPS算力的KPU，内置1GB LPDDR4内存（可选2GB LPDDR4版本内存）。

## K230 视觉识别模块特点

- **6TOPS 等效 AI 算力**，采用 RISC-V 端侧 AIoT 平台，内置新一代KPU（Knowledge Processing Unit）智能计算单元，性能最高可达上一代 K210 芯片的 **13.7 倍**。
- **第三代 KPU**，具备高精度推理、快速启动与超低功耗特性。
- 标配 **1GB LPDDR4** 运行内存，并可选配 **2GB LPDDR4** 大内存，以应对内存需求更高的 AI 视觉应用。
- 同时支持 Linux 系统和 RT-Smart 实时操作系统。
- 面向 **MicroPython 开发**，从课堂原型到嵌入式应用均可快速迭代。

## 产品版本

| 版本 | 适用方向 | 显示与交互能力 |
| --- | --- | --- |
| 标准版 | 嵌入式 AI 视觉、串口集成与紧凑型部署 | 无集成触控显示屏要求 |
| 豪华版 | 交互演示、教学与独立 AI 终端 | 配备 3.5 寸高清电容触摸屏 |

## 核心硬件规格

 - **处理器**：内置双核64bit RISC-V 核心（主频高达 1.6GHz 和 800MHz），6TOPS 等效算力
 - **多媒体接口**：支持3路摄像头（MIPI CSI-2）输入，最高支持 1080P@60fps 图像处理
 - **外设接口**：USB 2.0、UART、I2C、SPI、PWM、GPIO、SD/eMMC 等丰富接口，便于与主控通信
 - **音频接口**：音频功放
 - **存储**：1GB LPDDR4（可选 2GB LPDDR4）
 - **无线网络**：支持 Wi-Fi，IEEE 802.11 b/g/n

## 平台亮点

| 类别 | 能力 |
| --- | --- |
| AI 算力 | 基于 K230 RISC-V 端侧 AIoT 芯片，提供 6TOPS 等效算力 |
| 视觉 | 高清高帧率摄像头，适合实时视觉应用 |
| 内存 | 1GB LPDDR4；可选 2GB LPDDR4 配置 |
| 操作系统 | Linux 与 RT-Smart |
| 外壳 | 耐用 ABS 外壳 |
| 交互 | 豪华版配备 3.5 寸高清电容触摸显示屏 |
| 机械扩展 | 支持多种安装支架与二维电动云台 |

## AI 能力

K230 视觉识别模块适合需要低延迟、隐私保护和自主运行的端侧 AI 应用。可用于以下方案方向：

- AI 视觉识别与端侧计算机视觉应用
- AIoT 感知、分析与设备控制
- 模型训练流程与本地部署
- 多模态 AI 应用，包括文字理解、语音识别、语音合成、语音对话、图片理解以及绘画/图像分析

## 集成与开发

模块通过串口通信，可便捷地与多种主控搭配使用。面向 MicroPython 的开发方式可帮助团队快速构建和迭代，同时保留 Linux 与实时应用的扩展路径。

常见的集成主控或平台包括：

- TI MSPM0、STM32 与 ESP32
- Arduino 与 micro:bit
- 树莓派
- NVIDIA Jetson 平台
- RDK 平台

## 典型应用

- AI 与嵌入式系统课堂教学
- AI 视觉演示与触摸交互展项
- 智能设备视觉感知与 AIoT 终端
- 本地识别、分析与自动化系统
- 云台视觉跟踪与多视角观测项目
- 语音与视觉结合的多模态交互终端

## 快速开始

1. 紧凑型视觉集成可选择标准版；需要触摸交互时选择豪华版。
2. 将模块安装在外壳内，或接入兼容的支架/云台组件。
3. 通过串口将模块连接至支持的主控。
4. 配置所选的 Linux、RT-Smart 或 MicroPython 开发流程。
5. 部署 AI 视觉或多模态模型，并将推理结果接入主控应用。

### K230 SDK编译

K230 SDK需要在Linux环境下编译，推荐使用Ubuntu 22.04（Windows用户可使用WSL2 + Docker）。

#### 下载源码：
```shell
git clone https://github.com/kendryte/k230_sdk
cd k230_sdk
make prepare_sourcecode
```

#### 使用Docker编译：
```shell
# 拉取 Docker 镜像
docker pull ghcr.io/kendryte/k230_sdk

# 进入 Docker 环境
docker run -u root -it -v $(pwd):$(pwd) -v $(pwd)/toolchain:/opt/toolchain -w $(pwd) ghcr.io/kendryte/k230_sdk /bin/bash

# 编译（以 EVB 板为例）
make CONF=k230_evb_defconfig
```
#### 编译产物：
```shell
k230_evb_defconfig/images/
├── sysimage-sdcard.img       # SD 卡/eMMC 非安全启动镜像
├── sysimage-sdcard.img.gz    # 压缩版本
├── sysimage-spinor32m.img    # NOR Flash 镜像
└── ...
```

### 烧录镜像

```shell
sudo dd if=sysimage-sdcard.img of=/dev/sdx bs=1M oflag=sync
```

## 文档规划

本仓库将逐步汇集构建 K230 视觉识别项目所需的板级资料。计划覆盖以下内容：

- 硬件连接与串口通信参考
- 固件、操作系统与 MicroPython 环境配置指南
- AI 视觉与多模态模型部署示例
- 主控集成示例
- 云台、显示与安装说明

## 仓库结构

```
.
├── README.md              # 英文版产品说明
├── README_zh.md           # 简体中文版产品说明
├── LICENSE                # Apache License 2.0
├── AGENTS.md              # 贡献者与 AI 代理的协作约定
├── factory/gui/           # 板端 MicroPython GUI 启动器与应用
├── stubs/                 # 板端 API 类型存根
├── tools/                 # 资源烘焙与部署脚本
│   └── probes/            # 在板上跑的一次性测量脚本
├── models/                # 推理资源的参考副本
│   ├── kmodel/            # 应用加载的 kmodel（含来源说明）
│   └── utils/             # 人脸检测锚点数据
└── docs/                  # 板上实测的客观事实
    ├── board-ai-runtime.md      # Ai2d 与 KPU 契约
    ├── board-hardware.md        # IMU、音频、摄像头、视频解码
    ├── lvgl-binding.md          # 本 LVGL 绑定暴露了哪些能力
    ├── performance-budget.md    # 每帧开销
    ├── pitfalls.md              # 跨模块坑位清单
    └── reset-matrix.md          # 哪种复位能清哪种故障

```

## 开发资源

- 硬件连接、固件烧录与 MicroPython 入门指南将陆续补充到 `docs/` 目录。
- 底层开发建议结合嘉楠科技[K230 官方文档](https://github.com/kendryte/k230_docs) 进行。
- [K230 SDK](https://github.com/kendryte/k230_sdk)
- [K230 CanMV](https://github.com/kendryte/k230_canmv/)

## 适用用户

K230 视觉识别模块适用于需要端侧 AI 加速的嵌入式开发者、需要易于教学的 AI 视觉平台的教育工作者，以及评估 AIoT 视觉模块的产品团队。

## 参与贡献

欢迎通过 Issue 或 Pull Request 补充资料、修正错误。若修改中英文 README，请尽量保持两侧内容同步，并确保硬件参数与产品说明一致。

## 技术支持

如在使用过程中遇到问题，可先查阅 `docs/` 下的文档。若问题仍未解决，欢迎提交 Issue，描述现象、复现步骤与所用版本。

## 运行环境

本仓库**只包含应用层 Python 代码**，不含运行环境，也不分发运行环境的任何组成部分。

GUI 运行在 K230 的 **CanMV MicroPython 固件**上，应用 import 的板端模块
（`lvgl`、`image`、`media`、`nncase_runtime`、`aidemo`、`ulab`）全部由该固件提供。
固件需自行从嘉楠获取安装，本仓库不提供替代品。

| 组件 | 维护方 | 授权 |
| --- | --- | --- |
| CanMV 固件（`canmv_k230`） | 嘉楠 | 上游截至 2026-10 未发布 LICENSE 文件，请参考上游仓库 |
| K230 SDK | 嘉楠 | BSD-2-Clause，Copyright (c) 2023 Canaan Bright Sight Co., Ltd |
| LVGL | LVGL 项目 | MIT |

`stubs/*.pyi` 描述该固件的 API 面，用于让 PC 端类型检查不把板端 import 判为错误。
它们是本项目编写的类型描述，不是固件源码。

### 推理资源

`models/kmodel/` 与 `models/utils/` 是官方 CanMV 固件镜像中两个目录的**参考副本**，内容未做改动：

| 本仓库 | 板上路径（由固件镜像提供） | 来源 |
| --- | --- | --- |
| `models/kmodel/*.kmodel` | `/sdcard/examples/kmodel/` | K230 SDK `src/rtsmart/libs/kmodel/ai_poc/kmodel/` |
| `models/utils/prior_data_320.bin` | `/sdcard/examples/utils/` | 同上，人脸检测锚点数据 |

应用从板上路径加载，这些文件由 CanMV 固件镜像提供，`tools/deploy_board.py` **不**
推送它们。放在仓库里是为了让依赖清单、文件名与字节数无需解包固件镜像就能查阅，
更换模型时能留下可审查的差异。

这些文件属于嘉楠，适用于其原始授权，不属于本项目的 Apache-2.0；
何况视觉类应用本身也只能与该固件配合使用。

## 许可证

本项目以 **Apache License 2.0** 授权，完整条款见 [LICENSE](LICENSE)。

仓库内附带的第三方资源沿用各自原有授权：Lucide 图标（ISC）、
Nunito 与 Font Awesome 字体（SIL OFL / CC BY 4.0）、DejaVu Sans Mono
（Bitstream Vera）。完整署名清单见 `factory/gui/assets/LICENSE.txt`。
