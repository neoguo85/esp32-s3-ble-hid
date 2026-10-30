# ESP32-S3 BLE HID Keyboard

在 Mac 或 Windows 上复制文本，通过**蓝牙**发到一块 ESP32-S3 板子；板子把文本存进 Flash，插到目标机后按一下按键，用**标准 USB 键盘**逐字符敲出来。

目标机（例如不能装任何软件和脚本的 Windows VPC）**零安装**：它只看到一个普通外接键盘。

```
控制机（Mac / Windows，可装东西）      ESP32-S3            目标机（零安装）
复制 → App ──── BLE ────→ 存 LittleFS ──── USB HID ────→ 普通键盘
                              按 BOOT 开始输出
```

## 为什么用 BLE + USB HID

- ESP32-S3 只有 BLE（无经典蓝牙），所以蓝牙只做「控制机 → 板子」的数据通道，不参与对目标机的输入。
- 对目标机的输出始终走 USB HID，因此不需要在目标机装驱动、脚本或任何客户端。

## 硬件

| 项 | 值 |
|---|---|
| 模组 | 乐鑫 ESP32-S3-WROOM-1-**N16R8**（16MB Flash + 8MB **OPI** PSRAM） |
| 开发板 | 任意兼容官方 ESP32-S3-DevKitC-1 的板子 |
| 建议 | 已焊接排针版，方便反复插拔与按 BOOT / EN |
| 工具链 | arduino-cli 1.5.1 + 板包 `esp32:esp32@3.3.10-cn` |

别用 N8R2 / N4R2：Flash 只有 8MB/4MB，分区表和 PSRAM 参数都得跟着改。

两个 Type-C 口：右侧 **UART**（下载 + 串口调试），左侧 **USB**（原生 USB，HID 输出）。

## 目录结构

```
.
├── esp32_hid_key_ble/
│   └── esp32_hid_key_ble.ino    # 固件：BLE 收文本 → LittleFS → USB HID
├── sender/                      # 发送端（macOS / Windows 共用同一份代码）
│   ├── app.py                   # 图形界面
│   ├── send_clipboard.py        # 等价命令行版
│   ├── requirements.txt         # bleak
│   ├── build.sh  + Info.plist   # macOS：生成双击即用的 .app
│   ├── build.bat + run.bat      # Windows：打包 exe / 直接运行
│   └── assets/                  # 图标：icns / ico / png + 生成脚本
└── LICENSE
```

## 装环境（一次性）

```bash
brew install arduino-cli
arduino-cli config init
arduino-cli config add board_manager.additional_urls \
  https://jihulab.com/esp-mirror/espressif/arduino-esp32/-/raw/gh-pages/package_esp32_index_cn.json
arduino-cli config save
arduino-cli core update-index
arduino-cli core install esp32:esp32@3.3.10-cn
```

BLE 用的是板包自带的 BLE 库，不需要额外装库。

## 编译烧录

> **端口号不是固定的。** 下面所有命令里的 `-p <PORT>` 都是占位符，必须换成你在第 1 步里查到的那个值。
> 本文出现的 `/dev/cu.usbserial-1420`、`COM3` 只是举例，**照抄一定失败**。

### 1. 查端口

板子用数据线插**右侧 UART 口**（左边那个原生 USB 口是输出键盘用的，不负责烧录），然后：

```bash
arduino-cli board list
```

输出长这样，`Port` 那列就是要用的端口：

```
Port                            Protocol Type        Board Name  FQBN Core
/dev/cu.usbserial-1420          serial   Serial Port Unknown
/dev/cu.Bluetooth-Incoming-Port serial   Serial Port Unknown
/dev/cu.debug-console           serial   Serial Port Unknown
```

一台机器上通常挂着好几个 `cu.Bluetooth-*`、`cu.debug-console`，认准名字里带下面这些词的（**下表是举例，实际值以你屏幕上的为准**）：

| 板载 USB 转串口芯片 | macOS 端口名样子 | Windows 端口名样子 |
|---|---|---|
| CH340 / CH343 | `/dev/cu.usbserial-*`、`/dev/cu.wchusbserial*` | `COM3` 之类 |
| CP2102 | `/dev/cu.SLAB_USBtoUART`、`/dev/cu.usbserial-*` | `COM3` 之类 |
| 原生 USB（S3 自带） | `/dev/cu.usbmodem*` | `COM3` 之类 |

不想装 arduino-cli 也能查：

| 系统 | 方法 |
|---|---|
| macOS | `ls /dev/cu.*` |
| Windows | 设备管理器 → **端口 (COM 和 LPT)** → 看到 `USB-SERIAL CH340 (COM3)` 就是它 |
| Windows | PowerShell：`[System.IO.Ports.SerialPort]::GetPortNames()` |

查到之后记下来，第 4、5 步用的必须是同一个值。

### 2. 编译

macOS / Linux：

```bash
cd esp32_hid_key_ble
arduino-cli compile --output-dir=build \
  --fqbn esp32:esp32:esp32s3 \
  --build-property build.partitions=default_16MB \
  --build-property build.flash_size=16MB \
  --build-property build.psram_type=opi \
  --build-property build.usb_cdc_on_boot=0 \
  --build-property build.flashmode=qio \
  --build-property build.flashfreq=80m .
```

Windows（cmd 和 PowerShell 都贴这一整行。别用 `\` 续行 —— 那是 bash 的语法，cmd 用 `^`、PowerShell 用反引号，写成一行最省事）：

```bat
cd esp32_hid_key_ble
arduino-cli compile --output-dir=build --fqbn esp32:esp32:esp32s3 --build-property build.partitions=default_16MB --build-property build.flash_size=16MB --build-property build.psram_type=opi --build-property build.usb_cdc_on_boot=0 --build-property build.flashmode=qio --build-property build.flashfreq=80m .
```

实测输出：

```
Sketch uses 708491 bytes (54%) of program storage space.
Global variables use 60772 bytes (18%) of dynamic memory.
```

参数一项都不能省：`psram_type=opi` 写错会 boot loop；`usb_cdc_on_boot=0` 不关，原生 USB 会枚举成串口，目标机认不到键盘。

### 3. 进下载模式

数据线插右侧 UART 口 → 按住 **BOOT** → 点一下 **EN** → 松开 EN → 保持 BOOT 约 1 秒再松开。

### 4. 烧录

`-p` 后面填第 1 步查到的端口，把 `<PORT>` 整个替换掉（连尖括号一起删）：

```bash
# macOS / Linux —— 例如 -p /dev/cu.usbserial-1420
arduino-cli upload -p <PORT> --fqbn esp32:esp32:esp32s3 --input-dir=build .
```

```bat
:: Windows —— 例如 -p COM3
arduino-cli upload -p <PORT> --fqbn esp32:esp32:esp32s3 --input-dir=build .
```

看到 `Hash of data verified.` 和 `Hard resetting via RTS pin...` 就是烧好了。

### 5. 看串口日志（可选）

固件在 115200 上会打连接、存盘、输出进度，排查时很有用：

```bash
# macOS / Linux —— 例如 -p /dev/cu.usbserial-1420
arduino-cli monitor -p <PORT> -c baudrate=115200
```

```bat
:: Windows —— 例如 -p COM3
arduino-cli monitor -p <PORT> -c baudrate=115200
```

`Ctrl-C` 退出。注意固件把 USB CDC 关了（`usb_cdc_on_boot=0`），所以日志只从这个 UART 口出来 —— 插左边的原生 USB 口是看不到的，那边只有键盘。

正常启动会看到类似：

```
[fs] no saved text
[ready] BLE name: ESP32_HID_KEY, stored text: 0 bytes
[ble] connected
```

### 端口相关的坑

| 现象 | 原因 | 处理 |
|---|---|---|
| `board list` 里根本没有新端口 | 用了充电线（只有电源没数据线），或驱动没装 | 换一根确定的**数据线**；Windows 若看到黄色感叹号，装 CH340 驱动（看板子上 USB 口旁边那颗芯片的丝印） |
| 端口在，但 upload 报 `Failed to connect` / `No serial data received` | 没进下载模式 | 按第 3 步重来，BOOT 一直按到日志出现 `Connecting...` 再松 |
| 端口名是 `usbmodem`，且每次拔插都变 | 用了左侧原生 USB 口烧录 | 换右侧 UART 口，端口名就稳定了；非要用原生口，每次烧前重新 `board list` |
| Windows 上 upload 报端口被占用 | 串口助手或 `monitor` 还开着 | 关掉占用的程序再烧 |

## 图形界面（macOS / Windows）

同一个 `sender/app.py`，两个平台都能跑，全程不需要碰命令行。

### macOS

```bash
cd sender
./build.sh          # 生成 build/ESP32 HID Sender.app
```

把 `ESP32 HID Sender.app` 拖到「应用程序」或桌面，双击打开。

依赖本机 Python 3（需 `tkinter` + `bleak`）：先 `pip3 install bleak`。启动脚本会依次探测 `/opt/homebrew/bin/python3`、`~/.workbuddy/binaries/python/*/bin/python3`、`/usr/local/bin/python3`、`/usr/bin/python3`，选中第一个能 `import tkinter, bleak` 的解释器。

首次发送时系统会询问蓝牙权限，选「好」。

### Windows

两次点击，不需要 Visual Studio，也不需要管理员权限。

1. 装 [Python 3.10+](https://www.python.org/downloads/)，安装时勾上 **Add python.exe to PATH**
2. 双击 `sender\build.bat`，等它跑完（首次 1~3 分钟）

产物是单个文件 **`sender\dist\ESP32 HID Sender.exe`**（约 20 MB）。把这个 exe 拷给别人，对方**不用装 Python、不用联网**。

`build.bat` 会自动完成：建 `.venv` → 装 `bleak` + `pyinstaller` → 跑一次自检（确认 tkinter / bleak 能 import、控件尺寸正常）→ 打包 exe。

只想本机临时用、不打包：`build.bat` 跑过一次（`.venv` 已建好）之后，双击 `sender\run.bat` 直接起界面。若双击后没反应，在 cmd 里执行 `.venv\Scripts\python.exe app.py`，报错信息会直接打出来。

前置条件：Windows 10 版 1709 以上（BLE 支持），蓝牙适配器已打开。板子如果正被别的设备连着，会扫不到。

两个可能遇到的坑：

- **首次运行弹「Windows 已保护你的电脑」**：exe 没做代码签名，正常现象。点「更多信息」→「仍要运行」。
- **exe 双击后一闪就没了**：多半是 PyInstaller 没把 `winrt` 打进去（`bleak` 在 Windows 靠它访问蓝牙）。`build.bat` 检测到 `winrt` 时已经自动加了 `--collect-all winrt`；万一还不行，在 `sender` 目录手动补一次：

  ```bat
  .venv\Scripts\python.exe -m PyInstaller --noconfirm --clean --onefile --windowed ^
      --name "ESP32 HID Sender" --collect-all bleak --collect-all winrt app.py
  ```

  或者直接用 `run.bat` 从源码跑，绕开打包环节。

### 功能

| 功能 | 说明 |
|---|---|
| 扫描板子 | 自动找 `ESP32_HID_KEY` 并连接 |
| 文本区 | 可编辑，实时显示字符数 / 字节数；含板子打不出的字符时标红，点发送会被拦下 |
| **剪贴板历史** | 保存最近 **10 条**，双击载入文本框；落盘持久化，重启不丢 |
| 从剪贴板填入 | 手动拉一次剪贴板 |
| 监听剪贴板 | 勾上后「复制即发」，每 0.8s 检查一次 |
| 发送到板子 / 清空板子 | 写入或清空板子上的文本 |
| 日志区 | 连接状态、字节数、速率、板子回执 |

剪贴板历史存放位置：

- macOS `~/Library/Application Support/ESP32HIDSender/history.json`
- Windows `%APPDATA%\ESP32HIDSender\history.json`

图标在 `sender/assets/`：`AppIcon.icns`（macOS）、`app.ico`（Windows exe）、`appicon.png`（窗口图标）。想改图案就编辑 `assets/make_icon.py` 再跑 `python3 make_icon.py` 重新生成三个文件（需要 Pillow，平时用不到）。

不想用图形界面时，`sender/send_clipboard.py` 是等价命令行版（两个平台通用）：

```bash
python3 sender/send_clipboard.py            # 发一次剪贴板
python3 sender/send_clipboard.py --watch    # 复制即发
python3 sender/send_clipboard.py --clear    # 清空板子
python3 sender/send_clipboard.py --scan     # 只扫设备
python3 sender/send_clipboard.py --strip    # 剔除不支持字符后再发
python3 sender/send_clipboard.py --force    # 明知有缺字也照发
```

## BLE 协议

自定义 UART Service（Nordic UUID），方便用任何语言对接：

| 方向 | UUID | 说明 |
|---|---|---|
| 写 | `6E400002-B5A3-F393-E0A9-E50E24DCCA9E` | Mac → 板子 |
| 通知 | `6E400003-B5A3-F393-E0A9-E50E24DCCA9E` | 板子 → Mac |

数据格式：先发 `LEN:<字节数>\n`，随后跟原始字节流（可任意分片，板子按长度拼包）。

板子回执：`OK:<字节数>`（已存盘）、`DONE`（输出完毕）、`ERR:len`、`ERR:mem`。

清空板子：发 `LEN:0\n`。

设备名 `ESP32_HID_KEY`。

## 使用

1. 控制机上复制文本 → App 里点「发送到板子」（或勾上「监听剪贴板」自动发）
2. 板子左口插目标机（远程桌面场景做 USB 重定向）
3. 点好目标输入框，按一下 **BOOT** 开始逐字符输出
4. 输出中再按 BOOT 暂停 / 继续

板子插在目标机上时，BLE 仍可被控制机连着 —— 可以边发新内容边输出，不用拔插。

## 已知限制

- **只支持 ASCII**：板子按 US 键盘 scancode 逐字符输出，只认 `A-Z a-z 0-9`、常见半角标点（`!@#$%^&*()_+-=[]{};:'",./<>?` 以及反引号、竖线、反斜杠）、空格和换行，合计 95 个可见字符 + `\n`。中文、日文、emoji、全角标点、Tab、方向键、Ctrl 组合键一律不支持。
- **不支持的字符会被拦下**：判定表和固件里的 `isSupportedChar()` 一一对应（`sender/hid_chars.py`）。App 信息栏标红，点「发送到板子」时弹出首个出错位置（第几行第几列）和涉及字符，可以选择剔除后再发；勾了「监听剪贴板」时只在日志里记一条并跳过，不弹窗打断。命令行版默认拦下并以退出码 2 结束，需要时用 `--strip` 剔除或 `--force` 强发。
- **目标机必须处于英文输入法**：停在拼音输入法上，敲 ASCII 也会进候选框变成中文。
- **单条文本上限 64KB**（约 6 万字），再大容易 malloc 失败。
- **漏字符**：远程桌面 / VPC 里把固件里的 `CHAR_DELAY` 从 80 调到 120~200。
- **文本明文存在板子 Flash**，用完记得清空。
- macOS 首次插入 HID 会弹键盘类型识别向导并抢焦点，按 `Esc` 关掉即可。
- Windows 需要蓝牙 4.0+ 适配器与 Win10 1709+；若系统「蓝牙和其他设备」里已经手动配过这块板子，先删掉那条记录再让 App 扫描，否则可能连上却收不到通知。

## 合规

仅在你被授权的设备与账号上使用。客户环境使用前，确认 HID 外设接入符合该环境的安全策略。

## License

MIT
