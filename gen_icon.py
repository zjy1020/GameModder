# -*- coding: utf-8 -*-
"""
生成修改器图标 modder.ico (纯标准库, 无 Pillow 依赖)
设计: 深蓝圆角背景 + 亮绿芯片(带引脚) + 中心白色 "10" 点阵
输出: 多尺寸 ico (16/32/48/64), 32bpp BGRA, Windows 自动选尺寸
"""
import struct
import os

# ---------- 颜色 ----------
BG_TOP = (0x1E, 0x2A, 0x4A)        # 深蓝 #1E2A4A
BG_BOT = (0x12, 0x1C, 0x38)
CHIP = (0x4A, 0xDE, 0x80)          # 亮绿 #4ADE80
CHIP_DARK = (0x2E, 0xA0, 0x5C)     # 深绿
DOT = (0xF8, 0xFA, 0xFC)           # 近白
TRANSPARENT = (0, 0, 0, 0)


def rounded_rect_inside(x, y, w, h, r):
    if r <= x < w - r or r <= y < h - r:
        return True
    cx = r if x < r else w - 1 - r
    cy = r if y < r else h - 1 - r
    return (x - cx) ** 2 + (y - cy) ** 2 <= r * r


def draw_icon(size):
    """按尺寸绘制 64 位 BGRA 像素"""
    px = [[TRANSPARENT] * size for _ in range(size)]
    r = max(3, size // 5)

    # 背景(垂直渐变)
    for y in range(size):
        t = y / (size - 1)
        c = tuple(int(BG_TOP[i] + (BG_BOT[i] - BG_TOP[i]) * t) for i in range(3))
        for x in range(size):
            if rounded_rect_inside(x, y, size, size, r):
                px[y][x] = (c[0], c[1], c[2], 255)

    # 芯片: 中心 60%
    chip = max(6, int(size * 0.6))
    x0 = (size - chip) // 2
    y0 = (size - chip) // 2
    for y in range(y0, y0 + chip):
        for x in range(x0, x0 + chip):
            edge = (x == x0 or x == x0 + chip - 1 or y == y0 or y == y0 + chip - 1)
            px[y][x] = CHIP_DARK if edge else CHIP

    # 引脚: 大尺寸 4 根/边, 小尺寸 2 根
    n_pins = 4 if size >= 32 else 2
    if n_pins == 4:
        pin_pos = [x0 + chip * i // (n_pins + 1) for i in range(1, n_pins + 1)]
    else:
        pin_pos = [x0 + chip * i // (n_pins + 1) for i in range(1, n_pins + 1)]
    pin_len = max(2, size // 8)

    for p in pin_pos:
        for y in range(y0 - pin_len, y0):
            if 0 <= y < size:
                px[y][p] = CHIP
        for y in range(y0 + chip, y0 + chip + pin_len):
            if 0 <= y < size:
                px[y][p] = CHIP
        for x in range(x0 - pin_len, x0):
            if 0 <= x < size:
                px[p][x] = CHIP
        for x in range(x0 + chip, x0 + chip + pin_len):
            if 0 <= x < size:
                px[p][x] = CHIP

    # 中心点阵 "1 0" (仅 >=32 清晰可辨)
    if size >= 32:
        one = [[0, 1, 0], [1, 1, 0], [0, 1, 0], [0, 1, 0], [1, 1, 1]]
        zero = [[1, 1, 1], [1, 0, 1], [1, 0, 1], [1, 0, 1], [1, 1, 1]]
        d = max(1, size // 32)          # 每点边长
        cell = d + 1                     # 间距
        gw, gh = 3 * cell, 5 * cell      # 两组图案占宽/高
        start_x = (size - gw) // 2
        start_y = (size - gh) // 2
        for dy, row in enumerate(one):
            for dx, v in enumerate(row):
                if v:
                    for yy in range(start_y + dy * cell, start_y + dy * cell + d):
                        for xx in range(start_x + dx * cell, start_x + dx * cell + d):
                            if 0 <= yy < size and 0 <= xx < size:
                                px[yy][xx] = DOT
        off = (3 * cell) // 2 + cell // 2
        for dy, row in enumerate(zero):
            for dx, v in enumerate(row):
                if v:
                    for yy in range(start_y + dy * cell, start_y + dy * cell + d):
                        for xx in range(start_x + off + dx * cell, start_x + off + dx * cell + d):
                            if 0 <= yy < size and 0 <= xx < size:
                                px[yy][xx] = DOT
    return px


def make_dib(px, size):
    """像素 -> DIB (BITMAPINFOHEADER + BGRA + AND mask)"""
    header = struct.pack(
        "<IiiHHIIiiII",
        40, size, size * 2, 1, 32, 0,
        size * size * 4 + (size * size // 8), 0, 0, 0, 0,
    )
    img = b""
    for y in range(size - 1, -1, -1):
        for x in range(size):
            c = px[y][x]
            r, g, b = c[0], c[1], c[2]
            a = c[3] if len(c) > 3 else 255
            img += struct.pack("<BBBB", b, g, r, a)
    and_mask = b"\x00" * (size // 8) * size
    return header + img + and_mask


def main():
    sizes = [16, 32, 48, 64]
    dbs = [make_dib(draw_icon(s), s) for s in sizes]

    icondir = struct.pack("<HHH", 0, 1, len(sizes))
    offset = 6 + 16 * len(sizes)
    body = b""
    for s, dib in zip(sizes, dbs):
        entry = struct.pack(
            "<BBBBHHII",
            s if s < 256 else 0, s if s < 256 else 0,
            0, 0, 1, 32, len(dib), offset,
        )
        body += entry
        offset += len(dib)

    ico = icondir + body + b"".join(dbs)
    out = os.path.join(os.path.dirname(os.path.abspath(__file__)), "modder.ico")
    with open(out, "wb") as f:
        f.write(ico)

    # 校验
    assert ico[:4] == b"\x00\x00\x01\x00"
    cnt = struct.unpack_from("<H", ico, 4)[0]
    print(f"OK -> {out}  共 {cnt} 个尺寸")
    for i in range(cnt):
        w, h = ico[6 + i * 16], ico[7 + i * 16]
        bpp = struct.unpack_from("<H", ico, 12 + i * 16)[0]
        print(f"  entry{i}: {w}x{h}  bpp={bpp}")
    print(f"  总大小 {len(ico)} bytes")


if __name__ == "__main__":
    main()
