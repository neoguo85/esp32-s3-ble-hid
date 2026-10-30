#!/usr/bin/env python3
"""
ESP32 HID Sender — 图形界面（macOS / Windows 通用）

把剪贴板 / 文本框内容通过 BLE 发到 ESP32-S3 HID 板子，板子存 Flash 后
插到目标机（如不能装软件的 Windows VPC）按 BOOT 即用标准键盘输出。

依赖: bleak（BLE）、tkinter（Python 自带 GUI）
"""

import asyncio
import hashlib
import json
import os
import queue
import subprocess
import sys
import threading
import time
import tkinter as tk
from tkinter import font as tkfont
from tkinter import messagebox, ttk

try:
    from bleak import BleakClient, BleakScanner
except ImportError:
    _pip = "python -m pip install bleak" if sys.platform.startswith("win") else "pip3 install bleak"
    root = tk.Tk()
    root.withdraw()
    messagebox.showerror(
        "缺少依赖",
        f"未安装 bleak。请先执行：\n\n    {_pip}\n\n然后重新打开本 App。",
    )
    raise SystemExit(1)

DEVICE_NAME = "ESP32_HID_KEY"
RX_CHAR = "6e400002-b5a3-f393-e0a9-e50e24dcca9e"
TX_CHAR = "6e400003-b5a3-f393-e0a9-e50e24dcca9e"

MAX_HISTORY = 10
TEXT_LIMIT = 64 * 1024
CHUNK = 180
CHUNK_GAP = 0.004
SCAN_TIMEOUT = 15.0

IS_MAC = sys.platform == "darwin"
IS_WIN = sys.platform.startswith("win")

# --windowed 打包后 stdout 可能是 None；控制台里则可能是 cp936 / cp1252。
# 只把错误策略改成 replace，避免打日志时抛 UnicodeEncodeError。
for _s in (sys.stdout, sys.stderr):
    try:
        _s.reconfigure(errors="replace")
    except Exception:
        pass


def _app_support_dir() -> str:
    if IS_WIN:
        base = os.environ.get("APPDATA") or os.path.expanduser("~")
        return os.path.join(base, "ESP32HIDSender")
    return os.path.expanduser("~/Library/Application Support/ESP32HIDSender")


APP_SUPPORT = _app_support_dir()
HISTORY_FILE = os.path.join(APP_SUPPORT, "history.json")


def _ui_scale(root) -> float:
    """Windows 高分屏（150% / 200%）下把窗口尺寸按比例放大。

    开了 DPI 感知以后 Tk 的磅值字体会跟着放大，但 geometry 里的像素不会，
    不乘这个系数窗口会显得偏小、控件挤在一起。macOS 一律返回 1.0。
    """
    if not IS_WIN:
        return 1.0
    try:
        # tk scaling = 每磅多少像素，96 DPI 时是 96/72 ≈ 1.333
        s = float(root.tk.call("tk", "scaling")) / (96.0 / 72.0)
        return max(1.0, min(s, 3.0))
    except Exception:
        return 1.0


# ---------------------------------------------------------------- 剪贴板 / 历史

_tk_root = None          # Windows 上借 tk 读剪贴板


def read_clipboard() -> str:
    if IS_MAC:
        try:
            r = subprocess.run(["pbpaste"], capture_output=True, timeout=3)
            return r.stdout.decode("utf-8", "replace")
        except Exception:
            return ""
    if _tk_root is not None:
        try:
            return _tk_root.clipboard_get()
        except tk.TclError:
            return ""
    return ""


def load_history():
    try:
        with open(HISTORY_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if isinstance(data, list):
            return [str(x) for x in data][:MAX_HISTORY]
    except Exception:
        pass
    return []


def save_history(items):
    try:
        os.makedirs(APP_SUPPORT, exist_ok=True)
        with open(HISTORY_FILE, "w", encoding="utf-8") as f:
            json.dump(items[:MAX_HISTORY], f, ensure_ascii=False)
    except Exception:
        pass


def summary(text: str, width: int = 42) -> str:
    s = text.replace("\r\n", "\n").replace("\n", " ⏎ ").replace("\t", " → ")
    s = " ".join(s.split())
    if len(s) > width:
        s = s[:width] + "…"
    return f"{len(text):>6} 字  {s}"


# ---------------------------------------------------------------- BLE 线程

class BleWorker(threading.Thread):
    def __init__(self, ui_queue: queue.Queue):
        super().__init__(daemon=True)
        self.ui = ui_queue
        self.loop = asyncio.new_event_loop()
        self.client = None
        self.sending = False

    # -- 线程内
    def emit(self, kind, payload=None):
        self.ui.put((kind, payload))

    def run(self):
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def submit(self, coro):
        return asyncio.run_coroutine_threadsafe(coro, self.loop)

    def on_notify(self, _char, data: bytearray):
        text = data.decode("utf-8", "replace")
        if text.startswith("OK:"):
            self.emit("ack", text[3:])
        elif text == "DONE":
            self.emit("log", "✅ 板子输出完毕")
        else:
            self.emit("log", f"板子回执：{text}")

    def on_disconnect(self, _cli):
        self.client = None
        self.sending = False
        self.emit("status", ("已断开", "gray"))
        self.emit("log", "板子断开连接")

    async def connect(self):
        self.emit("status", ("扫描中 …", "orange"))
        self.emit("log", f"扫描 {DEVICE_NAME} …")
        try:
            dev = await BleakScanner.find_device_by_name(DEVICE_NAME, timeout=SCAN_TIMEOUT)
        except Exception as e:
            self.emit("status", ("扫描失败", "red"))
            self.emit("log", f"✗ 扫描出错：{e}")
            return
        if dev is None:
            self.emit("status", ("未找到板子", "red"))
            self.emit("log", "✗ 没扫到板子。确认已上电、没被别的设备连着、Mac 蓝牙已开")
            return

        self.emit("log", f"发现 {dev.name}（{dev.address}），连接中 …")
        self.emit("status", ("连接中 …", "orange"))
        try:
            self.client = BleakClient(dev, disconnected_callback=self.on_disconnect)
            await self.client.connect()
            await self.client.start_notify(TX_CHAR, self.on_notify)
            self.emit("status", ("已连接 · 就绪", "green"))
            self.emit("log", "✓ 已连接")
        except Exception as e:
            self.client = None
            self.emit("status", ("连接失败", "red"))
            self.emit("log", f"✗ 连接失败：{e}")

    async def disconnect(self):
        if self.client:
            try:
                await self.client.disconnect()
            except Exception:
                pass
        self.client = None
        self.emit("status", ("未连接", "gray"))
        self.emit("log", "已断开")

    async def send(self, text: str):
        if self.client is None or not self.client.is_connected:
            self.emit("log", "✗ 未连接板子，先点「扫描板子」")
            return
        if self.sending:
            self.emit("log", "正在发送，请稍候")
            return

        data = text.encode("utf-8")
        data = data.replace(b"\r\n", b"\n").replace(b"\r", b"\n")
        if not data:
            self.emit("log", "✗ 文本为空")
            return
        if len(data) > TEXT_LIMIT:
            self.emit("log", f"✗ 超过 64KB 上限（{len(data)} 字节）")
            return

        self.sending = True
        self.emit("status", ("发送中 …", "orange"))
        self.emit("log", f"开始发送 {len(data)} 字节 …")
        t0 = time.time()
        try:
            await self.client.write_gatt_char(RX_CHAR, f"LEN:{len(data)}\n".encode(), response=True)
            sent = 0
            for i in range(0, len(data), CHUNK):
                await self.client.write_gatt_char(RX_CHAR, data[i:i + CHUNK], response=True)
                sent = min(i + CHUNK, len(data))
                self.emit("progress", sent / len(data))
                await asyncio.sleep(CHUNK_GAP)
            dt = time.time() - t0
            speed = len(data) / dt / 1024 if dt > 0 else 0
            self.emit("log", f"· 写入完成，{dt:.1f}s，约 {speed:.1f} KB/s，等回执 …")
        except Exception as e:
            self.emit("log", f"✗ 发送失败：{e}")
            self.emit("status", ("已连接 · 就绪", "green") if self.client else ("未连接", "gray"))
        finally:
            self.sending = False

    async def clear_board(self):
        if self.client is None or not self.client.is_connected:
            self.emit("log", "✗ 未连接板子")
            return
        try:
            await self.client.write_gatt_char(RX_CHAR, b"LEN:0\n", response=True)
            self.emit("log", "已发送清空指令")
        except Exception as e:
            self.emit("log", f"✗ 清空失败：{e}")


# ---------------------------------------------------------------- 界面

class App:
    PALETTE = {"green": "#2ecc71", "orange": "#f39c12", "red": "#e74c3c", "gray": "#8e8e93"}

    def __init__(self, root: tk.Tk):
        global _tk_root
        _tk_root = root
        self.root = root
        self.scale = _ui_scale(root)
        self.ui_queue = queue.Queue()
        self.history = load_history()
        self.last_clip_hash = None
        self.watching = False
        self.watch_job = None
        self.progress = None

        self.worker = BleWorker(self.ui_queue)
        self.worker.start()

        self.build_ui()
        self.set_status("未连接", "gray")
        self.last_clip_hash = hashlib.md5(read_clipboard().encode()).hexdigest()
        self.pull_clipboard(silent=True)
        self.log(f"就绪（{'macOS' if IS_MAC else 'Windows'}）。点「扫描板子」开始。")
        self.root.after(80, self.poll_queue)

    # -- 构建
    def build_ui(self):
        s = self.scale
        self.root.title("ESP32 HID Sender")
        self.root.geometry(f"{int(940 * s)}x{int(700 * s)}")
        self.root.minsize(int(780 * s), int(560 * s))
        self.center_window(int(940 * s), int(700 * s))

        base = tkfont.nametofont("TkDefaultFont").actual()
        family = base.get("family", "Helvetica")
        mono_family = "Menlo" if IS_MAC else "Consolas"
        self.f_mono = tkfont.Font(family=mono_family, size=12)
        self.f_small = tkfont.Font(family=family, size=11)
        self.f_bold = tkfont.Font(family=family, size=12, weight="bold")

        style = ttk.Style()
        for theme in (("vista", "winnative", "xpnative") if IS_WIN else ("aqua",)):
            try:
                style.theme_use(theme)
                break
            except tk.TclError:
                continue

        outer = ttk.Frame(self.root, padding=14)
        outer.pack(fill="both", expand=True)
        outer.columnconfigure(0, weight=1)
        outer.rowconfigure(2, weight=1)

        # —— 状态行 ——
        bar = ttk.Frame(outer)
        bar.grid(row=0, column=0, sticky="ew")

        self.dot = tk.Canvas(bar, width=10, height=10, highlightthickness=0, bd=0)
        self.dot.pack(side="left", padx=(0, 8), pady=2)
        self.dot_id = self.dot.create_oval(1, 1, 9, 9, fill=self.PALETTE["gray"], outline="")

        self.status_label = ttk.Label(bar, text="未连接", font=self.f_bold)
        self.status_label.pack(side="left")

        self.scan_btn = ttk.Button(bar, text="扫描板子", command=self.on_scan)
        self.scan_btn.pack(side="right")

        self.disc_btn = ttk.Button(bar, text="断开", command=self.on_disconnect, width=6)
        self.disc_btn.pack(side="right", padx=(0, 8))

        self.progress = ttk.Progressbar(bar, length=int(140 * s), mode="determinate", maximum=100)
        self.progress.pack(side="right", padx=12)

        ttk.Separator(outer).grid(row=1, column=0, sticky="ew", pady=10)

        # —— 主体：文本 + 历史 ——
        body = ttk.Frame(outer)
        body.grid(row=2, column=0, sticky="nsew")

        # 先放右侧固定宽面板，再让左侧占满剩余
        right = ttk.Frame(body, width=int(300 * s))
        right.pack(side="right", fill="y", padx=(12, 0))
        right.pack_propagate(False)

        left = ttk.Frame(body)
        left.pack(side="left", fill="both", expand=True)

        ttk.Label(left, text="要输出的文本", font=self.f_small, foreground="#888").pack(anchor="w")

        text_wrap = ttk.Frame(left)
        text_wrap.pack(fill="both", expand=True, pady=(4, 6))

        self.text = tk.Text(text_wrap, wrap="none", font=self.f_mono, undo=True,
                            bd=1, relief="solid", padx=8, pady=6,
                            highlightthickness=0, insertwidth=2)
        yscroll = ttk.Scrollbar(text_wrap, orient="vertical", command=self.text.yview)
        xscroll = ttk.Scrollbar(text_wrap, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)
        self.text.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        text_wrap.rowconfigure(0, weight=1)
        text_wrap.columnconfigure(0, weight=1)
        self.text.bind("<<Modified>>", self.on_text_modified)

        self.info = ttk.Label(left, text="0 字符", font=self.f_small, foreground="#888")
        self.info.pack(anchor="w")

        # 历史面板
        head = ttk.Frame(right)
        head.pack(fill="x")
        ttk.Label(head, text=f"剪贴板历史（最近 {MAX_HISTORY} 条）", font=self.f_small,
                  foreground="#888").pack(side="left")
        ttk.Button(head, text="清空", width=4, command=self.on_clear_history).pack(side="right")

        hist_wrap = ttk.Frame(right)
        hist_wrap.pack(fill="both", expand=True, pady=(4, 0))

        self.hist_list = tk.Listbox(hist_wrap, font=self.f_small, activestyle="none",
                                    bd=1, relief="solid", highlightthickness=0,
                                    selectmode="browse")
        hscroll = ttk.Scrollbar(hist_wrap, orient="horizontal", command=self.hist_list.xview)
        self.hist_list.configure(xscrollcommand=hscroll.set)
        self.hist_list.pack(fill="both", expand=True)
        hscroll.pack(fill="x")
        self.hist_list.bind("<Double-Button-1>", self.on_pick_history)
        self.hist_list.bind("<Return>", self.on_pick_history)

        ttk.Label(right, text="双击载入到左侧文本框", font=self.f_small,
                  foreground="#888").pack(anchor="w", pady=(4, 0))

        # —— 操作行 ——
        ttk.Separator(outer).grid(row=3, column=0, sticky="ew", pady=10)
        ops = ttk.Frame(outer)
        ops.grid(row=4, column=0, sticky="ew")

        ttk.Button(ops, text="从剪贴板填入", command=lambda: self.pull_clipboard(silent=False)).pack(side="left")
        self.watch_var = tk.BooleanVar(value=False)
        ttk.Checkbutton(ops, text="监听剪贴板（复制即发）", variable=self.watch_var,
                        command=self.on_toggle_watch).pack(side="left", padx=12)
        ttk.Button(ops, text="存入历史", command=self.push_history_from_text, width=10).pack(side="left")

        self.send_btn = ttk.Button(ops, text="发送到板子", command=self.on_send)
        self.send_btn.pack(side="right")
        ttk.Button(ops, text="清空板子", command=self.on_clear_board).pack(side="right", padx=8)

        # —— 日志 ——
        ttk.Separator(outer).grid(row=5, column=0, sticky="ew", pady=10)
        log_wrap = ttk.Frame(outer, height=int(150 * s))
        log_wrap.grid(row=6, column=0, sticky="ew")
        log_wrap.grid_propagate(False)
        self.log_text = tk.Text(log_wrap, height=7, font=self.f_small, bd=1, relief="solid",
                                padx=8, pady=4, highlightthickness=0, state="disabled",
                                background="#1b1b1d", foreground="#c8c8cc")
        log_scroll = ttk.Scrollbar(log_wrap, orient="vertical", command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)
        log_scroll.pack(side="right", fill="y")
        self.log_text.pack(side="left", fill="both", expand=True)

        self.refresh_history_list()
        self.refresh_info()

    # -- 状态与日志
    def center_window(self, w, h):
        self.root.update_idletasks()
        sw = self.root.winfo_screenwidth()
        sh = self.root.winfo_screenheight()
        x = max(0, (sw - w) // 2)
        y = max(0, (sh - h) // 2 - 20)
        self.root.geometry(f"{w}x{h}+{x}+{y}")
        self.root.lift()
        self.root.attributes("-topmost", True)
        self.root.after(300, lambda: self.root.attributes("-topmost", False))
        self.root.focus_force()

    def set_status(self, text, color):
        self.status_label.configure(text=text)
        self.dot.itemconfigure(self.dot_id, fill=self.PALETTE.get(color, self.PALETTE["gray"]))

    def log(self, msg):
        stamp = time.strftime("%H:%M:%S")
        self.log_text.configure(state="normal")
        self.log_text.insert("end", f"[{stamp}] {msg}\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    # -- 文本
    def get_text(self):
        return self.text.get("1.0", "end-1c")

    def set_text(self, s):
        self.text.delete("1.0", "end")
        self.text.insert("1.0", s)
        self.refresh_info()

    def on_text_modified(self, _e=None):
        if self.text.edit_modified():
            self.text.edit_modified(False)
            self.refresh_info()

    def refresh_info(self):
        s = self.get_text()
        nbytes = len(s.encode("utf-8"))
        bad = sum(1 for ch in s if ord(ch) > 127)
        msg = f"{len(s)} 字符 · {nbytes} 字节"
        color = "#888"
        if bad:
            msg += f"   ⚠ 含 {bad} 个非 ASCII 字符，板子会跳过"
            color = self.PALETTE["orange"]
        if nbytes > TEXT_LIMIT:
            msg += "   ⚠ 超过 64KB 上限"
            color = self.PALETTE["red"]
        self.info.configure(text=msg, foreground=color)

    # -- 剪贴板
    def pull_clipboard(self, silent=True):
        s = read_clipboard()
        self.set_text(s)
        if s.strip():
            self.add_history(s)
        if not silent:
            self.log(f"已从剪贴板读取 {len(s)} 字符")

    def on_toggle_watch(self):
        self.watching = self.watch_var.get()
        if self.watching:
            self.last_clip_hash = hashlib.md5(read_clipboard().encode()).hexdigest()
            self.log(f"开始监听剪贴板（每 0.8s），复制后自动发送")
            self.watch_job = self.root.after(800, self._watch_tick)
        else:
            if self.watch_job:
                self.root.after_cancel(self.watch_job)
                self.watch_job = None
            self.log("停止监听剪贴板")

    def _watch_tick(self):
        if not self.watching:
            return
        s = read_clipboard()
        h = hashlib.md5(s.encode()).hexdigest()
        if h != self.last_clip_hash:
            self.last_clip_hash = h
            if s.strip():
                self.set_text(s)
                self.add_history(s)
                self.log(f"剪贴板变化：{len(s)} 字符，自动发送")
                self.on_send()
        self.watch_job = self.root.after(800, self._watch_tick)

    # -- 历史
    def add_history(self, text):
        if not text.strip():
            return
        if self.history and self.history[0] == text:
            return
        self.history = [x for x in self.history if x != text]
        self.history.insert(0, text)
        self.history = self.history[:MAX_HISTORY]
        save_history(self.history)
        self.refresh_history_list()

    def refresh_history_list(self):
        self.hist_list.delete(0, "end")
        for i, item in enumerate(self.history, 1):
            self.hist_list.insert("end", f"{i:>2}. {summary(item)}")

    def on_pick_history(self, _e=None):
        sel = self.hist_list.curselection()
        if not sel:
            return
        item = self.history[sel[0]]
        self.set_text(item)
        self.log(f"已载入历史 #{sel[0] + 1}（{len(item)} 字符）")

    def push_history_from_text(self):
        s = self.get_text()
        if not s.strip():
            return
        self.add_history(s)
        self.log("已存入历史")

    def on_clear_history(self):
        if not self.history:
            return
        if messagebox.askyesno("清空历史", f"确定清空 {len(self.history)} 条剪贴板历史？"):
            self.history = []
            save_history(self.history)
            self.refresh_history_list()
            self.log("已清空历史")

    # -- BLE 动作
    def on_scan(self):
        self.scan_btn.configure(state="disabled")
        self.progress.configure(value=0)
        self.worker.submit(self.worker.connect())

    def on_disconnect(self):
        self.worker.submit(self.worker.disconnect())

    def on_send(self):
        text = self.get_text()
        if not text.strip():
            messagebox.showinfo("没有内容", "文本框是空的。")
            return
        self.add_history(text)
        self.worker.submit(self.worker.send(text))

    def on_clear_board(self):
        if messagebox.askyesno("清空板子", "清空板子上已存的文本？"):
            self.worker.submit(self.worker.clear_board())

    # -- 队列轮询
    def poll_queue(self):
        try:
            while True:
                kind, payload = self.ui_queue.get_nowait()
                if kind == "status":
                    self.set_status(*payload)
                    self.scan_btn.configure(state="normal")
                elif kind == "log":
                    self.log(payload)
                elif kind == "progress":
                    self.progress.configure(value=payload * 100)
                elif kind == "ack":
                    self.log(f"✅ 板子已存 {payload} 字节，可以拔下来插目标机了")
                    self.set_status("已连接 · 已存入板子", "green")
                    self.progress.configure(value=100)
        except queue.Empty:
            pass
        self.root.after(80, self.poll_queue)


def _enable_hidpi():
    """Windows 上开启 DPI 感知，否则高分屏字会发虚。"""
    if not IS_WIN:
        return
    try:
        import ctypes
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(1)
        except Exception:
            ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def main():
    _enable_hidpi()
    root = tk.Tk()
    app = App(root)

    if "--selftest" in sys.argv:
        root.update()
        # 只用 ASCII 输出：英文版 Windows 控制台是 cp1252，打中文会
        # UnicodeEncodeError，build.bat 会把它误判成构建失败。
        widgets = {
            "window": (root.winfo_width(), root.winfo_height()),
            "text": (app.text.winfo_width(), app.text.winfo_height()),
            "history": (app.hist_list.winfo_width(), app.hist_list.winfo_height()),
            "log": (app.log_text.winfo_width(), app.log_text.winfo_height()),
            "scan btn": (app.scan_btn.winfo_width(), app.scan_btn.winfo_height()),
            "send btn": (app.send_btn.winfo_width(), app.send_btn.winfo_height()),
        }
        for name, (w, h) in widgets.items():
            flag = "ok" if w > 20 and h > 10 else "!! BAD SIZE"
            print(f"  {name:<9} {w:>4} x {h:<4}  {flag}")
        print("  visible  :", bool(root.winfo_ismapped()))
        print("  history  :", len(app.history))
        print("  platform :", "windows" if IS_WIN else ("macos" if IS_MAC else sys.platform))
        print("  scale    :", app.scale)
        root.destroy()
        return

    def on_close():
        try:
            save_history(app.history)
        finally:
            root.destroy()

    root.protocol("WM_DELETE_WINDOW", on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
