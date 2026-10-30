# ESP32-S3 BLE HID Keyboard

Mac 上复制文本，通过**蓝牙**发到一块 ESP32-S3 板子；板子把文本存进 Flash，插到目标机后按一下按键，用**标准 USB 键盘**逐字符敲出来。

目标机（例如不能装任何软件和脚本的 Windows VPC）**零安装**：它只看到一个普通外接键盘。

```
Mac（可装东西）              ESP32-S3               目标机（零安装）
复制 → App ──BLE──→ 存 LittleFS ──USB HID──→ 普通键盘
                              按 BOOT 开始输出
```

## 为什么用 BLE + USB HID

- ESP32-S3 只有 BLE（无经典蓝牙），所以蓝牙只做「Mac → 板子」的数据通道，不参与对目标机的输入。
- 对目标机的输出始终走 USB HID，因此不需要在目标机装驱动、脚本或任何客户端。
- 砍掉 WiFi 协议栈后固件只占 **54%** Flash，比带网页的版本还宽松。

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
├── macapp/
│   ├── app.py                   # macOS 图形界面
│   ├── Info.plist
│   ├── build.sh                 # 生成双击即用的 .app
│   └── send_clipboard.py        # 同功能的命令行版本
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

arduino-cli board list
arduino-cli upload -p /dev/cu.wchusbserial1420 --fqbn esp32:esp32:esp32s3 --input-dir=build .
```

实测输出：

```
Sketch uses 708491 bytes (54%) of program storage space.
Global variables use 60772 bytes (18%) of dynamic memory.
```

参数一项都不能省：`psram_type=opi` 写错会 boot loop；`usb_cdc_on_boot=0` 不关，原生 USB 会枚举成串口，目标机认不到键盘。

**进下载模式**：数据线插右侧 UART 口 → 按住 **BOOT** → 点一下 **EN** → 松开 EN → 保持 BOOT 约 1 秒再松开。

## macOS 图形界面

不需要命令行，双击即用。

```bash
cd macapp
./build.sh          # 生成 build/ESP32 HID Sender.app
```

把 `ESP32 HID Sender.app` 拖到「应用程序」或桌面，双击打开。

| 功能 | 说明 |
|---|---|
| 扫描板子 | 自动找 `ESP32_HID_KEY` 并连接 |
| 文本区 | 可编辑，实时显示字符数 / 字节数 / 非 ASCII 字符数 |
| **剪贴板历史** | 保存最近 **10 条**，双击载入文本框；持久化到 `~/Library/Application Support/ESP32HIDSender/history.json`，重启不丢 |
| 从剪贴板填入 | 手动拉一次剪贴板 |
| 监听剪贴板 | 勾上后「复制即发」，每 0.8s 检查一次 |
| 发送到板子 / 清空板子 | 写入或清空板子上的文本 |
| 日志区 | 连接状态、字节数、速率、板子回执 |

首次发送时系统会询问蓝牙权限，选「好」。

依赖本机 Python 3（需 `tkinter` + `bleak`）：

```bash
pip3 install bleak
```

启动脚本会依次探测 `/opt/homebrew/bin/python3`、`~/.workbuddy/binaries/python/*/bin/python3`、`/usr/local/bin/python3`、`/usr/bin/python3`，选中第一个能 `import tkinter, bleak` 的解释器。

不想用图形界面时，`macapp/send_clipboard.py` 是等价命令行版：

```bash
python3 macapp/send_clipboard.py            # 发一次剪贴板
python3 macapp/send_clipboard.py --watch    # 复制即发
python3 macapp/send_clipboard.py --clear    # 清空板子
python3 macapp/send_clipboard.py --scan     # 只扫设备
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

1. Mac 上复制文本 → App 里点「发送到板子」（或勾上「监听剪贴板」自动发）
2. 板子左口插目标机（远程桌面场景做 USB 重定向）
3. 点好目标输入框，按一下 **BOOT** 开始逐字符输出
4. 输出中再按 BOOT 暂停 / 继续

板子插在目标机上时，BLE 仍可被 Mac 连着 —— 可以边发新内容边输出，不用拔插。

## 已知限制

- **只支持 ASCII**：中文、日文、emoji、Tab、方向键、Ctrl 组合键都不支持，固件逐字符映射 US 键盘 scancode。App 会提前提示非 ASCII 字符数。
- **目标机必须处于英文输入法**：停在拼音输入法上，敲 ASCII 也会进候选框变成中文。
- **单条文本上限 64KB**（约 6 万字），再大容易 malloc 失败。
- **漏字符**：远程桌面 / VPC 里把固件里的 `CHAR_DELAY` 从 80 调到 120~200。
- **文本明文存在板子 Flash**，用完记得清空。
- macOS 首次插入 HID 会弹键盘类型识别向导并抢焦点，按 `Esc` 关掉即可。

## 合规

仅在你被授权的设备与账号上使用。客户环境使用前，确认 HID 外设接入符合该环境的安全策略。

## License

MIT
