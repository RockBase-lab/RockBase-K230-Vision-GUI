# K230 Vision Module

English | [简体中文](README_zh.md)

RockBase IoT K230-Vision is an edge-AI vision module built on Canaan's next-generation K230 AI computing platform. Designed for developers, education, and AIoT product integration, it combines high-performance visual inference, multimodal AI capabilities, flexible controller connectivity, and practical mounting options in a compact, enclosed system. An optional 3.5-inch capacitive touchscreen (800×480) is available. The K230 integrates a dual-core RISC-V processor (big core at 1.6 GHz + little core at 0.8 GHz) and a KPU delivering approximately 6 TOPS of compute, with 1 GB LPDDR4 memory (a 2 GB LPDDR4 version is optional).

## K230 Vision Module Features

- **6 TOPS equivalent AI compute** on a RISC-V edge-AIoT platform with a built-in next-generation KPU (Knowledge Processing Unit), delivering up to **13.7×** the performance of the previous-generation K210 chip.
- **Third-generation KPU** for high-accuracy inference, fast startup, and ultra-low-power operation.
- **1 GB LPDDR4 memory** as standard, with an optional **2 GB LPDDR4** configuration for memory-intensive AI vision workloads.
- **Dual-system support** for Linux and the RT-Smart real-time operating system.
- **MicroPython-oriented development** for an approachable path from classroom prototypes to embedded applications.

## Editions

| Edition | Suitable for | Included display capability |
| --- | --- | --- |
| Standard Edition | Embedded AI vision, serial integration, and compact deployments | No integrated touch display requirement |
| Deluxe Edition | Interactive demos, teaching, and standalone AI terminals | 3.5-inch high-definition capacitive touchscreen |

## Core Hardware Specifications

- **Processor**: Dual-core 64-bit RISC-V cores (up to 1.6 GHz and 800 MHz), 6 TOPS equivalent AI compute
- **Multimedia interfaces**: Three camera (MIPI CSI-2) inputs, up to 1080P@60fps image processing
- **Peripherals**: USB 2.0, UART, I2C, SPI, PWM, GPIO, SD/eMMC, and other rich interfaces for host communication
- **Audio**: Audio power amplifier
- **Memory**: 1 GB LPDDR4 (2 GB LPDDR4 optional)
- **Wireless**: Wi-Fi, IEEE 802.11 b/g/n

## Platform Highlights

| Area | Capability |
| --- | --- |
| AI compute | K230 RISC-V edge-AIoT chip with 6 TOPS equivalent compute |
| Vision | High-definition, high-frame-rate camera for real-time visual applications |
| Memory | 1 GB LPDDR4; 2 GB LPDDR4 configuration available |
| Operating systems | Linux and RT-Smart |
| Enclosure | Durable ABS housing |
| Interaction | 3.5-inch high-definition capacitive touchscreen on the Deluxe Edition |
| Mechanics | Multiple mounting brackets and a two-axis motorized pan-tilt unit |

## AI Capabilities

K230 Vision is intended for local, edge-side AI applications where response time, privacy, and autonomous operation matter. Supported solution directions include:

- AI visual recognition and edge-side computer-vision applications
- AIoT sensing, analysis, and device control
- Model training workflows followed by local deployment
- Multimodal AI applications, including text understanding, speech recognition, speech synthesis, voice conversation, image understanding, and image/drawing analysis

## Integration and Development

The module communicates through a serial interface, making it practical to pair with a wide range of host controllers. Its MicroPython development approach helps teams build and iterate quickly while retaining a path to richer Linux and real-time applications.

Common integration targets include:

- TI MSPM0, STM32, and ESP32
- Arduino and micro:bit
- Raspberry Pi
- NVIDIA Jetson platforms
- RDK platforms

## Typical Applications

- Classroom AI and embedded-systems instruction
- AI vision demonstrations and interactive touchscreen exhibits
- Smart-device visual perception and AIoT endpoints
- Local recognition, analysis, and automation systems
- Pan-tilt visual tracking and multi-angle observation projects
- Multimodal voice-and-vision interaction terminals

## Getting Started

1. Select the Standard Edition for compact vision integration, or the Deluxe Edition when touchscreen interaction is needed.
2. Mount the module in its enclosure or on a compatible bracket/pan-tilt assembly.
3. Connect the serial interface to a supported host controller.
4. Set up the selected Linux, RT-Smart, or MicroPython development workflow.
5. Deploy an AI vision or multimodal model and integrate its results into the host application.

### Building the K230 SDK

The K230 SDK must be built in a Linux environment. Ubuntu 22.04 is recommended (Windows users can use WSL2 + Docker).

#### Download the source code:
```shell
git clone https://github.com/kendryte/k230_sdk
cd k230_sdk
make prepare_sourcecode
```

#### Build with Docker:
```shell
# Pull the Docker image
docker pull ghcr.io/kendryte/k230_sdk

# Enter the Docker environment
docker run -u root -it -v $(pwd):$(pwd) -v $(pwd)/toolchain:/opt/toolchain -w $(pwd) ghcr.io/kendryte/k230_sdk /bin/bash

# Build (using the EVB board as an example)
make CONF=k230_evb_defconfig
```

#### Build artifacts:
```shell
k230_evb_defconfig/images/
├── sysimage-sdcard.img       # SD card/eMMC non-secure boot image
├── sysimage-sdcard.img.gz    # Compressed version
├── sysimage-spinor32m.img    # NOR Flash image
└── ...
```

### Flashing the Image

```shell
sudo dd if=sysimage-sdcard.img of=/dev/sdx bs=1M oflag=sync
```

## Documentation Roadmap

This repository will collect the board-specific material needed to build K230 Vision projects. Planned documentation areas include:

- Hardware connections and serial communication reference
- Firmware, operating-system, and MicroPython setup guides
- AI vision and multimodal model deployment examples
- Host-controller integration examples
- Pan-tilt, display, and mounting instructions

## Repository Structure

```
.
├── README.md              # English product overview
├── README_zh.md           # Simplified Chinese product overview
├── LICENSE                # Apache License 2.0
├── AGENTS.md              # Working agreement for contributors and agents
├── factory/gui/           # GUI: launcher, apps, audio, net, assets
├── stubs/                 # Type stubs for the on-board API
├── tools/                 # Asset baking and deployment scripts
│   └── probes/            # One-shot measurement scripts run on the board
├── models/                # Reference copies of the inference artefacts
│   ├── kmodel/            # kmodels the apps load (+ provenance notes)
│   └── utils/             # Face-detection anchor data
└── docs/                  # Measured platform facts
    ├── board-ai-runtime.md      # Ai2d and KPU contracts
    ├── board-hardware.md        # IMU, audio, camera, video decoding
    ├── lvgl-binding.md          # What this LVGL binding exposes
    ├── performance-budget.md    # Per-frame costs
    ├── pitfalls.md              # Cross-cutting trap index
    └── reset-matrix.md          # Which reset clears which failure

```

## Development Resources

- Hardware connections, firmware flashing, and MicroPython getting-started guides will be added to `docs/`.
- For low-level development, refer to Canaan's [K230 official documentation](https://github.com/kendryte/k230_docs).
- [K230 SDK](https://github.com/kendryte/k230_sdk)
- [K230 CanMV](https://github.com/kendryte/k230_canmv/)

## Intended Users

K230 Vision serves embedded developers who need edge AI acceleration, educators who need a teachable AI-vision platform, and product teams evaluating a flexible vision module for AIoT solutions.

## Contributing

Contributions via Issues and Pull Requests are welcome. When modifying either README, please keep the bilingual content in sync and ensure hardware parameters match the product brief.

## Technical Support

If you encounter problems, check the documentation under `docs/` first. If the issue persists, open an Issue describing the symptom, reproduction steps, and the version in use.

## Runtime Environment

This repository contains **application-layer Python code only**. It does
not include the operating environment and does not redistribute any part
of it.

The GUI runs on the **CanMV MicroPython firmware** for K230, which
provides the board-only modules the apps import: `lvgl`, `image`,
`media`, `nncase_runtime`, `aidemo` and `ulab`. Install that firmware
from Canaan separately; nothing here replaces it.

| Component | Maintained by | Terms |
| --- | --- | --- |
| CanMV firmware (`canmv_k230`) | Canaan | No licence file published upstream as of 2026-10; consult the upstream repository |
| K230 SDK | Canaan | BSD-2-Clause, Copyright (c) 2023 Canaan Bright Sight Co., Ltd |
| LVGL | LVGL project | MIT |

`stubs/*.pyi` describe this firmware's API surface so a PC-side type
checker does not flag board-only imports. They are type descriptions
written for this project, not firmware source.

### Inference artefacts

`models/kmodel/` and `models/utils/` are **reference copies** of two
directories in the official CanMV firmware image, unmodified:

| Here | Board path (from the firmware image) | Origin |
| --- | --- | --- |
| `models/kmodel/*.kmodel` | `/sdcard/examples/kmodel/` | K230 SDK `src/rtsmart/libs/kmodel/ai_poc/kmodel/` |
| `models/utils/prior_data_320.bin` | `/sdcard/examples/utils/` | Same, face-detection anchors |

The apps load them from the board path, which the CanMV firmware image
already provides; `tools/deploy_board.py` does not push these files. They
are kept here so the dependency list, the file names and the byte counts
are visible and reviewable without unpacking a firmware image, and so a
model swap is a diff rather than a shrug.

These files belong to Canaan and remain under their origin terms, not
this project's Apache-2.0 licence; the vision apps are only usable
together with that firmware anyway.

## License

Released under the **Apache License 2.0**. See [LICENSE](LICENSE) for the full text.

Third-party components bundled with this repository keep their own terms:
Lucide icons (ISC), Nunito and Font Awesome fonts (SIL OFL / CC BY 4.0),
DejaVu Sans Mono (Bitstream Vera). The full attribution list is
`factory/gui/assets/LICENSE.txt`.
