#!/usr/bin/env python3
"""
把剪贴板内容通过 BLE 发到 ESP32-S3 HID 板子（macOS / Windows 通用）。

板子收到后写入 Flash（断电不丢），插到 Windows 上按 BOOT 即用标准 USB 键盘输出，
目标机零安装。

依赖:
    pip3 install bleak        # Windows 用 python -m pip install bleak

用法:
    python3 send_clipboard.py                # 发送当前剪贴板
    python3 send_clipboard.py --text "abc"   # 发送指定字符串
    python3 send_clipboard.py --file a.txt   # 发送文件内容
    python3 send_clipboard.py --watch        # 监听剪贴板，一变就发（Ctrl-C 退出）
    python3 send_clipboard.py --clear        # 清空板子上的文本
    python3 send_clipboard.py --scan         # 只扫描附近的板子

含中文 / emoji / Tab 等板子打不出来的字符时默认拦下不发送，并给出行列位置。
    --strip   剔掉这些字符后再发
    --force   原样发送（板子会跳过这些字符，输出会缺字）
"""

import argparse
import asyncio
import hashlib
import subprocess
import sys

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    _cmd = "python -m pip install bleak" if sys.platform.startswith("win") else "pip3 install bleak"
    sys.exit(f"缺少依赖，请先执行:  {_cmd}")

import hid_chars

IS_MAC = sys.platform == "darwin"

# Windows 控制台是 cp936 / cp1252，碰到编不出的字符会抛 UnicodeEncodeError。
# 只改错误策略、不改编码，中文照常显示，生僻符号退化成 "?" 而不是崩掉。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass

DEVICE_NAME = "ESP32_HID_KEY"
RX_CHAR = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
TX_CHAR = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

CHUNK = 180          # 单片字节数（通常协商到 MTU 185）
CHUNK_GAP = 0.004    # 片间间隔，太快的个别适配器会丢包
ACK_TIMEOUT = 30.0


_tk_clip = None


def read_clipboard() -> bytes:
    """读系统剪贴板。macOS 走 pbpaste，Windows 借 tkinter，都不引第三方库。"""
    if IS_MAC:
        r = subprocess.run(["pbpaste"], capture_output=True)
        if r.returncode != 0:
            raise SystemExit("读取剪贴板失败：pbpaste 返回非零")
        return r.stdout

    global _tk_clip
    if _tk_clip is None:
        import tkinter as tk
        _tk_clip = tk.Tk()
        _tk_clip.withdraw()
    try:
        return _tk_clip.clipboard_get().encode("utf-8")
    except Exception:
        return b""


def guard(text: str, *, force: bool = False, strip: bool = False) -> str:
    """规范化换行 + 拦截板子打不出来的字符。

    默认直接退出，含不支持字符时不发送；--strip 剔除后放行，--force 原样放行。
    """
    text = hid_chars.normalize_newlines(text)
    if strip:
        text = hid_chars.strip_unsupported(text)

    res = hid_chars.scan(text)
    if res.ok:
        return text
    if force:
        print(f"[!] 有 {res.count} 个不支持字符，--force 原样发送，板子会跳过它们")
        return text

    print(f"[X] 含 {res.count} 个不支持字符，已拦下，没有发送。")
    print(f"    首个位置：第 {res.line} 行第 {res.col} 列  {hid_chars.show_char(res.char)}")
    print(f"    涉及：{hid_chars.describe(res.chars)}")
    print("    板子按 US 键盘逐字符输出，只认英文、数字、常见半角符号和换行；")
    print("    中文、emoji、全角标点、Tab 都会被跳过，发过去就是缺字。")
    print("    用 --strip 剔除后发送，或 --force 原样发送。")
    raise SystemExit(2)


async def find_device(timeout: float = 10.0):
    dev = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=timeout)
    if dev is None:
        raise SystemExit(
            f"找不到 {DEVICE_NAME}。检查：板子是否上电、是否已被别的设备连走、"
            f"本机蓝牙是否打开。"
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
            print("[OK] 已存入板子。拔下插到目标机，点好输入框，按 BOOT 开始输出。")


async def scan():
    print("扫描 BLE 设备 8 秒 …")
    devices = await BleakScanner.discover(timeout=8.0)
    for d in devices:
        mark = "  ← 目标板子" if d.name == DEVICE_NAME else ""
        print(f"  {d.name or '(无名)'}  {d.address}{mark}")
    if not any(d.name == DEVICE_NAME for d in devices):
        print(f"\n没扫到 {DEVICE_NAME}。确认板子已上电且没被别的设备连着。")


async def watch(interval: float, force: bool = False, strip: bool = False):
    print(f"监听剪贴板（每 {interval}s 检查一次），Ctrl-C 结束。")
    last = hashlib.md5(read_clipboard()).hexdigest()
    print(f"· 当前剪贴板基线 {last[:8]}，复制新内容后会自动发送。")
    while True:
        await asyncio.sleep(interval)
        raw = read_clipboard()
        digest = hashlib.md5(raw).hexdigest()
        if digest == last:
            continue
        last = digest
        if not raw:
            print("· 剪贴板为空，跳过")
            continue
        text = raw.decode("utf-8", "replace")
        print(f"\n· 剪贴板变化：{len(text)} 字符")
        try:
            text = guard(text, force=force, strip=strip)
        except SystemExit:
            print("    （这一条跳过，监听继续）")
            continue
        try:
            await send_bytes(text.encode("utf-8"))
        except Exception as e:
            print(f"[X] 发送失败：{e}")


def main():
    ap = argparse.ArgumentParser(description="把剪贴板通过 BLE 发到 ESP32-S3 HID 板子")
    src = ap.add_mutually_exclusive_group()
    src.add_argument("--text", help="发送指定字符串")
    src.add_argument("--file", help="发送文件内容")
    src.add_argument("--clear", action="store_true", help="清空板子上的文本")
    src.add_argument("--watch", action="store_true", help="监听剪贴板变化并自动发送")
    src.add_argument("--scan", action="store_true", help="只扫描附近的 BLE 设备")
    ap.add_argument("--interval", type=float, default=0.8, help="watch 模式的轮询间隔秒数")
    ap.add_argument("--strip", action="store_true", help="剔除板子打不出来的字符后再发")
    ap.add_argument("--force", action="store_true", help="含不支持字符也照样发（会缺字）")
    args = ap.parse_args()

    if args.scan:
        asyncio.run(scan())
        return

    if args.watch:
        try:
            asyncio.run(watch(args.interval, args.force, args.strip))
        except KeyboardInterrupt:
            print("\n已退出监听。")
        return

    if args.clear:
        text = ""
    elif args.text is not None:
        text = args.text
    elif args.file:
        with open(args.file, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    else:
        text = read_clipboard().decode("utf-8", "replace")

    if not text and not args.clear:
        raise SystemExit("剪贴板是空的。")

    if args.clear:
        print("· 清空板子上的文本")
    else:
        text = guard(text, force=args.force, strip=args.strip)
        data = text.encode("utf-8")
        print(f"· 待发送 {len(text)} 字符 / {len(data)} 字节")

    asyncio.run(send_bytes(b"" if args.clear else data))


if __name__ == "__main__":
    main()
