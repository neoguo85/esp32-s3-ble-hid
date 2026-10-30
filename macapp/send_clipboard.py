#!/usr/bin/env python3
"""
把 macOS 剪贴板内容通过 BLE 发到 ESP32-S3 HID 板子。

板子收到后写入 Flash（断电不丢），插到 Windows 上按 BOOT 即用标准 USB 键盘输出，
目标机零安装。

依赖:
    pip3 install bleak

用法:
    python3 send_clipboard.py                # 发送当前剪贴板
    python3 send_clipboard.py --text "abc"   # 发送指定字符串
    python3 send_clipboard.py --file a.txt   # 发送文件内容
    python3 send_clipboard.py --watch        # 监听剪贴板，一变就发（Ctrl-C 退出）
    python3 send_clipboard.py --clear        # 清空板子上的文本
    python3 send_clipboard.py --scan         # 只扫描附近的板子
"""

import argparse
import asyncio
import hashlib
import subprocess
import sys

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    sys.exit("缺少依赖，请先执行:  pip3 install bleak")

DEVICE_NAME = "ESP32_HID_KEY"
RX_CHAR = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
TX_CHAR = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

CHUNK = 180          # 单片字节数（macOS 通常协商到 MTU 185）
CHUNK_GAP = 0.004    # 片间间隔，太快的个别适配器会丢包
ACK_TIMEOUT = 30.0


def read_clipboard() -> bytes:
    """用 pbpaste 读剪贴板，避免额外依赖。"""
    r = subprocess.run(["pbpaste"], capture_output=True)
    if r.returncode != 0:
        raise SystemExit("读取剪贴板失败：pbpaste 返回非零")
    return r.stdout


def ascii_report(data: bytes) -> str:
    """板子只能打 ASCII，提前把会被跳过的字符数告诉用户。"""
    bad = sum(1 for b in data if b > 127)
    if bad == 0:
        return ""
    return f"  ⚠ 含 {bad} 个非 ASCII 字节（中文/emoji 等），板子会跳过这些字符"


async def find_device(timeout: float = 10.0):
    dev = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=timeout)
    if dev is None:
        raise SystemExit(
            f"找不到 {DEVICE_NAME}。检查：板子是否上电、是否已被别的设备连走、"
            f"Mac 蓝牙是否打开。"
        )
    return dev


async def send_bytes(data: bytes) -> None:
    print(f"· 寻找 {DEVICE_NAME} …")
    dev = await find_device()
    print(f"· 已发现 {dev.name} ({dev.address})，连接中 …")

    ack: asyncio.Future = asyncio.get_event_loop().create_future()

    def on_notify(_char, payload: bytearray):
        if not ack.done():
            ack.set_result(payload.decode("utf-8", "replace"))

    async with BleakClient(dev) as cli:
        print("· 已连接，发送中 …")
        await cli.start_notify(TX_CHAR, on_notify)

        await cli.write_gatt_char(RX_CHAR, f"LEN:{len(data)}\n".encode(), response=True)

        total = len(data)
        sent = 0
        t0 = asyncio.get_event_loop().time()
        for i in range(0, total, CHUNK):
            await cli.write_gatt_char(RX_CHAR, data[i:i + CHUNK], response=True)
            sent = min(i + CHUNK, total)
            if total >= 2048 and (i // CHUNK) % 20 == 0:
                pct = sent * 100 // total
                print(f"\r  {sent}/{total} bytes ({pct}%)", end="", flush=True)
            await asyncio.sleep(CHUNK_GAP)

        if total >= 2048:
            print(f"\r  {total}/{total} bytes (100%)")

        elapsed = asyncio.get_event_loop().time() - t0
        rate = total / elapsed / 1024 if elapsed > 0 else 0

        try:
            result = await asyncio.wait_for(ack, timeout=ACK_TIMEOUT)
        except asyncio.TimeoutError:
            result = "(未收到确认)"

        print(f"· 板子回执: {result}")
        print(f"· 耗时 {elapsed:.1f}s，约 {rate:.1f} KB/s")

        if result.startswith("OK"):
            print("✅ 已存入板子。拔下插到目标机，点好输入框，按 BOOT 开始输出。")


async def scan():
    print("扫描 BLE 设备 8 秒 …")
    devices = await BleakScanner.discover(timeout=8.0)
    for d in devices:
        mark = "  ← 目标板子" if d.name == DEVICE_NAME else ""
        print(f"  {d.name or '(无名)'}  {d.address}{mark}")
    if not any(d.name == DEVICE_NAME for d in devices):
        print(f"\n没扫到 {DEVICE_NAME}。确认板子已上电且没被别的设备连着。")


async def watch(interval: float):
    print(f"监听剪贴板（每 {interval}s 检查一次），Ctrl-C 结束。")
    last = hashlib.md5(read_clipboard()).hexdigest()
    print(f"· 当前剪贴板基线 {last[:8]}，复制新内容后会自动发送。")
    while True:
        await asyncio.sleep(interval)
        data = read_clipboard()
        digest = hashlib.md5(data).hexdigest()
        if digest == last:
            continue
        last = digest
        if not data:
            print("· 剪贴板为空，跳过")
            continue
        print(f"\n· 剪贴板变化：{len(data)} 字节{ascii_report(data)}")
        try:
            await send_bytes(data)
        except Exception as e:
            print(f"✗ 发送失败：{e}")


def main():
    ap = argparse.ArgumentParser(description="把剪贴板通过 BLE 发到 ESP32-S3 HID 板子")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--text", help="发送指定字符串")
    src.add_argument("--file", help="发送文件内容")
    src.add_argument("--clear", action="store_true", help="清空板子上的文本")
    src.add_argument("--watch", action="store_true", help="监听剪贴板变化并自动发送")
    src.add_argument("--scan", action="store_true", help="只扫描附近的 BLE 设备")
    ap.add_argument("--interval", type=float, default=0.8, help="watch 模式的轮询间隔秒数")
    args = ap.parse_args()

    if args.scan:
        asyncio.run(scan())
        return

    if args.watch:
        try:
            asyncio.run(watch(args.interval))
        except KeyboardInterrupt:
            print("\n已退出监听。")
        return

    if args.clear:
        data = b""
    elif args.text is not None:
        data = args.text.encode("utf-8")
        data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    elif args.file:
        with open(args.file, "rb") as f:
            data = f.read()
        data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
    else:
        data = read_clipboard()
        data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")

    if not data and not args.clear:
        raise SystemExit("剪贴板是空的。")

    if args.clear:
        print("· 清空板子上的文本")
    else:
        print(f"· 待发送 {len(data)} 字节{ascii_report(data)}")

    asyncio.run(send_bytes(data))


if __name__ == "__main__":
    main()
