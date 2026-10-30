#!/usr/bin/env python3
"""
生成 ESP32 HID Sender 的图标。

一趟产出三个文件（都在本目录）：
    appicon.png    1024×1024，Tk 窗口图标 / README 用
    AppIcon.icns   macOS 应用图标（build.sh 会拷进 .app）
    app.ico        Windows exe 图标（build.bat 用 --icon 指定）

依赖 Pillow，只有想重画图标时才需要跑这个脚本；
仓库里已经带了生成好的 icns / ico，普通使用者不用装 Pillow。

    python3 make_icon.py

图形语言：键盘 + 上方三道信号弧 = 「无线键盘」。
所有尺寸都从 4096 超采样再 LANCZOS 降到目标尺寸，16px 下也不糊。
"""

import os
import struct
import subprocess
import sys
import shutil
import tempfile

from PIL import Image, ImageDraw, ImageFilter

HERE = os.path.dirname(os.path.abspath(__file__))

DESIGN = 1024.0          # 设计坐标系：0..1024
INSET = 64.0             # 圆角方块相对画布的内缩（留出阴影位）
K = (DESIGN - 2 * INSET) / DESIGN      # 设计坐标 → 实际边长比例
SS = 4                                 # 超采样倍数
PX = int(DESIGN * SS)                  # 超采样画布边长

TOP = (0x4C, 0x8D, 0xFF)               # 背景渐变（上）
BOTTOM = (0x5B, 0x4D, 0xE8)            # 背景渐变（下）
WHITE = (255, 255, 255, 255)


def cx(v):
    """设计坐标 → 超采样画布坐标。"""
    return (INSET + v * K) * SS


def L(v):
    """设计长度 → 超采样长度。"""
    return v * K * SS


def box(x0, y0, x1, y1):
    return [cx(x0), cx(y0), cx(x1), cx(y1)]


def make_gradient(size):
    grad = Image.new("RGB", (1, size))
    d = ImageDraw.Draw(grad)
    for y in range(size):
        t = y / max(1, size - 1)
        d.point((0, y), tuple(int(TOP[i] + (BOTTOM[i] - TOP[i]) * t) for i in range(3)))
    return grad.resize((size, size), Image.BILINEAR).convert("RGBA")


def rounded_mask(rect, radius, holes=()):
    m = Image.new("L", (PX, PX), 0)
    d = ImageDraw.Draw(m)
    d.rounded_rectangle(rect, radius=radius, fill=255)
    for h_rect, h_radius in holes:
        d.rounded_rectangle(h_rect, radius=h_radius, fill=0)
    return m


def arc_ends(center, r, a0, a1, width):
    """给 arc 补圆头：PIL 的 arc 只有平头。

    PIL 把带宽画在半径以内（外沿正好落在 r 上），所以圆头要放在
    r - width/2 处，半径 width/2，才能和弧端的径向切口齐平；
    放在 r 上会凸出去一小块，看起来像个珠子。
    """
    import math
    pts = []
    rc = r - width / 2.0
    for a in (a0, a1):
        rad = math.radians(a)
        pts.append((center[0] + rc * math.cos(rad), center[1] + rc * math.sin(rad)))
    return pts, width / 2.0


def render():
    canvas = Image.new("RGBA", (PX, PX), (0, 0, 0, 0))

    outer = box(0, 0, DESIGN, DESIGN)
    radius = L(196)

    # -- 阴影 ---------------------------------------------------------
    shadow = Image.new("RGBA", (PX, PX), (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    off = L(10)
    sd.rounded_rectangle(
        [outer[0], outer[1] + off, outer[2], outer[3] + off],
        radius=radius, fill=(20, 24, 48, 90),
    )
    shadow = shadow.filter(ImageFilter.GaussianBlur(L(22)))
    canvas = Image.alpha_composite(canvas, shadow)

    # -- 背景 ---------------------------------------------------------
    bg = make_gradient(PX)
    bg.putalpha(rounded_mask(outer, radius))
    canvas = Image.alpha_composite(canvas, bg)

    # -- 键盘（白色实体，按键位置挖空露出渐变）------------------------
    bx0, by0, bx1, by1 = 162, 455, 862, 755
    body = box(bx0, by0, bx1, by1)
    pad, rows, gap_x, gap_y = 48, 3, 18, 20
    inner_x0, inner_x1 = bx0 + pad, bx1 - pad
    inner_w = inner_x1 - inner_x0                                  # 604
    row_h = (by1 - by0 - 2 * pad - (rows - 1) * gap_y) / rows      # 56
    key_r = L(13)

    holes = []
    # 前两行：6 个等宽键
    key_w = (inner_w - 5 * gap_x) / 6
    for r in range(2):
        y0 = by0 + pad + r * (row_h + gap_y)
        for c in range(6):
            x0 = inner_x0 + c * (key_w + gap_x)
            holes.append((box(x0, y0, x0 + key_w, y0 + row_h), key_r))
    # 第三行：两侧键 + 空格键
    y0 = by0 + pad + 2 * (row_h + gap_y)
    widths = [128, 312, 128]
    x = inner_x0
    for w in widths:
        holes.append((box(x, y0, x + w, y0 + row_h), key_r))
        x += w + gap_x

    keyboard = Image.new("RGBA", (PX, PX), (0, 0, 0, 0))
    keyboard.paste(Image.new("RGBA", (PX, PX), WHITE), (0, 0),
                   rounded_mask(body, L(44), holes))
    canvas = Image.alpha_composite(canvas, keyboard)

    # -- 信号弧（三道，从键盘上沿向上扩散）----------------------------
    arcs = Image.new("RGBA", (PX, PX), (0, 0, 0, 0))
    ad = ImageDraw.Draw(arcs)
    cxy = (cx(512), cx(by0))
    a0, a1 = 212, 328
    width = L(26)
    for r in (78, 142, 206):
        rr = L(r)
        b = [cxy[0] - rr, cxy[1] - rr, cxy[0] + rr, cxy[1] + rr]
        ad.arc(b, start=a0, end=a1, fill=WHITE, width=int(width))
        pts, cap_r = arc_ends(cxy, rr, a0, a1, width)
        for p in pts:
            ad.ellipse([p[0] - cap_r, p[1] - cap_r, p[0] + cap_r, p[1] + cap_r], fill=WHITE)
    canvas = Image.alpha_composite(canvas, arcs)

    return canvas.resize((1024, 1024), Image.LANCZOS)


def write_icns(master, path):
    sizes = {
        "icon_16x16.png": 16, "icon_16x16@2x.png": 32,
        "icon_32x32.png": 32, "icon_32x32@2x.png": 64,
        "icon_128x128.png": 128, "icon_128x128@2x.png": 256,
        "icon_256x256.png": 256, "icon_256x256@2x.png": 512,
        "icon_512x512.png": 512, "icon_512x512@2x.png": 1024,
    }
    tmp = tempfile.mkdtemp(prefix="iconset-")
    try:
        iconset = os.path.join(tmp, "AppIcon.iconset")
        os.makedirs(iconset)
        for name, size in sizes.items():
            master.resize((size, size), Image.LANCZOS).save(
                os.path.join(iconset, name), "PNG")
        subprocess.run(["iconutil", "-c", "icns", iconset, "-o", path], check=True)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def write_ico(master, path):
    """手写 ICO：每个尺寸单独超采样，避免 PIL 内部插值把 16px 弄糊。"""
    sizes = [16, 24, 32, 48, 64, 128, 256]
    blobs = []
    for size in sizes:
        tmp = os.path.join(tempfile.mkdtemp(prefix="ico-"), f"{size}.png")
        master.resize((size, size), Image.LANCZOS).save(tmp, "PNG")
        with open(tmp, "rb") as f:
            blobs.append((size, f.read()))
        shutil.rmtree(os.path.dirname(tmp), ignore_errors=True)

    header = struct.pack("<HHH", 0, 1, len(blobs))
    offset = 6 + 16 * len(blobs)
    entries, data = b"", b""
    for size, blob in blobs:
        dim = 0 if size >= 256 else size
        entries += struct.pack("<BBBBHHII", dim, dim, 0, 0, 1, 32, len(blob), offset)
        data += blob
        offset += len(blob)
    with open(path, "wb") as f:
        f.write(header + entries + data)


def main():
    master = render()
    png = os.path.join(HERE, "appicon.png")
    icns = os.path.join(HERE, "AppIcon.icns")
    ico = os.path.join(HERE, "app.ico")

    master.save(png, "PNG")
    write_icns(master, icns)
    write_ico(master, ico)

    for p in (png, icns, ico):
        print(f"  {os.path.basename(p):<14} {os.path.getsize(p):>8,} bytes")


if __name__ == "__main__":
    if sys.platform != "darwin":
        sys.exit("icns 生成依赖 macOS 的 iconutil")
    main()
