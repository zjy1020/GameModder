# -*- coding: utf-8 -*-
"""
通用内存修改器 GameModder
=========================
中文 GUI 的 Windows 进程内存数值修改工具 (CE 式工作流)。

功能: 数值扫描(精确值/多轮过滤/自动识别类型) / 写入与锁定 / 游戏模板一键定位
       / 手动读写 / 右键保存自定义模板。
运行: python GameModder.py   (Windows + Python 3.8+, 仅标准库)
详细使用说明见 README.md
"""

import ctypes
import struct
import threading
import queue
import time
import json
import os
import re
import sys
from ctypes import wintypes

import tkinter as tk
from tkinter import ttk, messagebox
from tkinter.scrolledtext import ScrolledText

# ============================================================
# Win32 API
# ============================================================
PROCESS_QUERY_INFORMATION = 0x0400
PROCESS_VM_READ = 0x0010
PROCESS_VM_WRITE = 0x0020
PROCESS_VM_OPERATION = 0x0008
TH32CS_SNAPPROCESS = 0x00000002

kernel32 = ctypes.WinDLL('kernel32', use_last_error=True)
user32 = ctypes.WinDLL('user32', use_last_error=True)

IS_64BIT = ctypes.sizeof(ctypes.c_void_p) == 8


class MEMORY_BASIC_INFORMATION(ctypes.Structure):
    _fields_ = [
        ("BaseAddress", ctypes.c_void_p),
        ("AllocationBase", ctypes.c_void_p),
        ("AllocationProtect", wintypes.DWORD),
        ("RegionSize", ctypes.c_size_t),
        ("State", wintypes.DWORD),
        ("Protect", wintypes.DWORD),
        ("Type", wintypes.DWORD),
    ]


class PROCESSENTRY32W(ctypes.Structure):
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.POINTER(ctypes.c_ulong)),
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("pcPriClassBase", ctypes.c_long),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", ctypes.c_wchar * 260),
    ]


OpenProcess = kernel32.OpenProcess
OpenProcess.restype = wintypes.HANDLE
OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]

ReadProcessMemory = kernel32.ReadProcessMemory
ReadProcessMemory.restype = wintypes.BOOL
ReadProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                              ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]

WriteProcessMemory = kernel32.WriteProcessMemory
WriteProcessMemory.restype = wintypes.BOOL
WriteProcessMemory.argtypes = [wintypes.HANDLE, ctypes.c_void_p, ctypes.c_void_p,
                               ctypes.c_size_t, ctypes.POINTER(ctypes.c_size_t)]

VirtualQueryEx = kernel32.VirtualQueryEx
VirtualQueryEx.restype = ctypes.c_size_t
VirtualQueryEx.argtypes = [wintypes.HANDLE, ctypes.c_void_p,
                           ctypes.POINTER(MEMORY_BASIC_INFORMATION), ctypes.c_size_t]

CreateToolhelp32Snapshot = kernel32.CreateToolhelp32Snapshot
CreateToolhelp32Snapshot.restype = wintypes.HANDLE
CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]

Process32FirstW = kernel32.Process32FirstW
Process32FirstW.restype = wintypes.BOOL
Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]

Process32NextW = kernel32.Process32NextW
Process32NextW.restype = wintypes.BOOL
Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]

CloseHandle = kernel32.CloseHandle
CloseHandle.argtypes = [wintypes.HANDLE]

# 窗口标题 (用于进程列表显示)
EnumWindows = user32.EnumWindows
EnumWindows.argtypes = [ctypes.c_void_p, wintypes.LPARAM]
EnumWindows.restype = wintypes.BOOL
GetWindowThreadProcessId = user32.GetWindowThreadProcessId
GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
GetWindowThreadProcessId.restype = wintypes.DWORD
GetWindowTextLengthW = user32.GetWindowTextLengthW
GetWindowTextLengthW.argtypes = [wintypes.HWND]
GetWindowTextLengthW.restype = ctypes.c_int
GetWindowTextW = user32.GetWindowTextW
GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
GetWindowTextW.restype = ctypes.c_int
IsWindowVisible = user32.IsWindowVisible
IsWindowVisible.argtypes = [wintypes.HWND]
IsWindowVisible.restype = wintypes.BOOL

# ============================================================
# 图标提取 (SHGetFileInfo + GDI)
# ============================================================
SHGFI_ICON = 0x100
SHGFI_SMALLICON = 0x1
PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
DIB_RGB_COLORS = 0


class SHFILEINFO(ctypes.Structure):
    _fields_ = [
        ("hIcon", wintypes.HANDLE),
        ("iIcon", ctypes.c_int),
        ("dwAttributes", wintypes.DWORD),
        ("szDisplayName", ctypes.c_wchar * 260),
        ("szTypeName", ctypes.c_wchar * 80),
    ]


class BITMAP(ctypes.Structure):
    _fields_ = [
        ("bmType", ctypes.c_long),
        ("bmWidth", ctypes.c_long),
        ("bmHeight", ctypes.c_long),
        ("bmWidthBytes", ctypes.c_long),
        ("bmPlanes", wintypes.WORD),
        ("bmBitsPixel", wintypes.WORD),
        ("bmBits", ctypes.c_void_p),
    ]


class ICONINFO(ctypes.Structure):
    _fields_ = [
        ("fIcon", wintypes.BOOL),
        ("xHotspot", wintypes.DWORD),
        ("yHotspot", wintypes.DWORD),
        ("hbmMask", wintypes.HANDLE),
        ("hbmColor", wintypes.HANDLE),
    ]


class BITMAPINFOHEADER(ctypes.Structure):
    _fields_ = [
        ("biSize", wintypes.DWORD),
        ("biWidth", ctypes.c_long),
        ("biHeight", ctypes.c_long),
        ("biPlanes", wintypes.WORD),
        ("biBitCount", wintypes.WORD),
        ("biCompression", wintypes.DWORD),
        ("biSizeImage", wintypes.DWORD),
        ("biXPelsPerMeter", ctypes.c_long),
        ("biYPelsPerMeter", ctypes.c_long),
        ("biClrUsed", wintypes.DWORD),
        ("biClrImportant", wintypes.DWORD),
    ]


class BITMAPINFO(ctypes.Structure):
    _fields_ = [("bmiHeader", BITMAPINFOHEADER)]


shell32 = ctypes.WinDLL('shell32', use_last_error=True)
ExtractIconExW = shell32.ExtractIconExW
ExtractIconExW.restype = wintypes.UINT
ExtractIconExW.argtypes = [wintypes.LPCWSTR, ctypes.c_int,
                           ctypes.POINTER(wintypes.HANDLE), ctypes.POINTER(wintypes.HANDLE),
                           wintypes.UINT]

QueryFullProcessImageNameW = kernel32.QueryFullProcessImageNameW
QueryFullProcessImageNameW.restype = wintypes.BOOL
QueryFullProcessImageNameW.argtypes = [wintypes.HANDLE, wintypes.DWORD,
                                       wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)]

gdi32 = ctypes.WinDLL('gdi32', use_last_error=True)
GetIconInfo = user32.GetIconInfo
GetIconInfo.restype = wintypes.BOOL
GetIconInfo.argtypes = [wintypes.HANDLE, ctypes.POINTER(ICONINFO)]
DestroyIcon = user32.DestroyIcon
DestroyIcon.restype = wintypes.BOOL
DestroyIcon.argtypes = [wintypes.HANDLE]
GetObjectW = gdi32.GetObjectW
GetObjectW.restype = ctypes.c_int
GetObjectW.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p]
GetDIBits = gdi32.GetDIBits
GetDIBits.restype = ctypes.c_int
GetDIBits.argtypes = [wintypes.HDC, wintypes.HANDLE, wintypes.UINT, wintypes.UINT,
                      ctypes.c_void_p, ctypes.POINTER(BITMAPINFO), wintypes.UINT]
CreateCompatibleDC = gdi32.CreateCompatibleDC
CreateCompatibleDC.restype = wintypes.HDC
CreateCompatibleDC.argtypes = [wintypes.HDC]
SelectObject = gdi32.SelectObject
SelectObject.restype = wintypes.HANDLE
SelectObject.argtypes = [wintypes.HDC, wintypes.HANDLE]
DeleteObject = gdi32.DeleteObject
DeleteObject.restype = wintypes.BOOL
DeleteObject.argtypes = [wintypes.HANDLE]
DeleteDC = gdi32.DeleteDC
DeleteDC.restype = wintypes.BOOL
DeleteDC.argtypes = [wintypes.HDC]
GetDC = user32.GetDC
GetDC.restype = wintypes.HDC
GetDC.argtypes = [wintypes.HWND]
ReleaseDC = user32.ReleaseDC
ReleaseDC.restype = ctypes.c_int
ReleaseDC.argtypes = [wintypes.HWND, wintypes.HDC]


def exe_path_of(pid):
    """获取进程 exe 完整路径 (受限查询权限)"""
    h = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return None
    try:
        buf = ctypes.create_unicode_buffer(1024)
        sz = wintypes.DWORD(1024)
        if QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(sz)):
            return buf.value
        return None
    finally:
        CloseHandle(h)


def build_photoimage_from_icon(path):
    """从 exe 文件提取 16x16 图标, 转为 tk.PhotoImage; 失败返回 None"""
    # 用 ExtractIconExW 直接从 exe 资源提取小图标 (不走 shell 命名空间, 沙箱兼容)
    small = wintypes.HANDLE(0)
    large = wintypes.HANDLE(0)
    n = ExtractIconExW(path, 0, ctypes.byref(large), ctypes.byref(small), 1)
    hIcon = small.value or large.value
    if not n or not hIcon:
        return None
    try:
        ii = ICONINFO()
        if not GetIconInfo(hIcon, ctypes.byref(ii)):
            return None
        try:
            if not ii.hbmColor:
                return None
            bm = BITMAP()
            if not GetObjectW(ii.hbmColor, ctypes.sizeof(BITMAP), ctypes.byref(bm)):
                return None
            w, h = bm.bmWidth, bm.bmHeight
            if w <= 0 or h <= 0 or w > 64 or h > 64:
                return None
            hdc = GetDC(None)
            mdc = CreateCompatibleDC(hdc)
            old = SelectObject(mdc, ii.hbmColor)
            bmi = BITMAPINFO()
            bmi.bmiHeader.biSize = ctypes.sizeof(BITMAPINFOHEADER)
            bmi.bmiHeader.biWidth = w
            bmi.bmiHeader.biHeight = -h          # 自上而下
            bmi.bmiHeader.biPlanes = 1
            bmi.bmiHeader.biBitCount = 32
            bmi.bmiHeader.biCompression = 0      # BI_RGB
            buf = (ctypes.c_ubyte * (w * h * 4))()
            got = GetDIBits(mdc, ii.hbmColor, 0, h, buf, ctypes.byref(bmi), DIB_RGB_COLORS)
            SelectObject(mdc, old)
            DeleteDC(mdc)
            ReleaseDC(None, hdc)
            if not got:
                return None
            img = tk.PhotoImage(width=w, height=h)
            stride = w * 4
            for y in range(h):
                row = []
                base = y * stride
                for x in range(w):
                    o = base + x * 4
                    b = buf[o]
                    g = buf[o + 1]
                    r = buf[o + 2]
                    a = buf[o + 3]
                    if a < 128:
                        row.append("#F4F4F4")    # 透明 -> 浅底
                    else:
                        row.append("#%02x%02x%02x" % (r, g, b))
                img.put("{" + " ".join(row) + "}", to=(0, y))
            return img
        finally:
            if ii.hbmColor:
                DeleteObject(ii.hbmColor)
            if ii.hbmMask:
                DeleteObject(ii.hbmMask)
    finally:
        if small.value:
            DestroyIcon(small.value)
        if large.value:
            DestroyIcon(large.value)


# ============================================================
# 值类型
# ============================================================
VALUE_TYPES = [
    ("双精度浮点 double (8字节)", "double", 8, "<d"),
    ("单精度浮点 float (4字节)", "float", 4, "<f"),
    ("64位整数 int64 (8字节)", "int64", 8, "<q"),
    ("32位整数 int32 (4字节)", "int32", 4, "<i"),
]
TYPE_LABELS = [t[0] for t in VALUE_TYPES]
TYPE_KEY = {t[1]: t for t in VALUE_TYPES}


def pack_value(vtype, val):
    _, _, _, fmt = TYPE_KEY[vtype]
    if vtype in ("double", "float"):
        return struct.pack(fmt, float(val))
    return struct.pack(fmt, int(val))


def unpack_value(vtype, data):
    _, _, size, fmt = TYPE_KEY[vtype]
    return struct.unpack(fmt, data[:size])[0]


def fmt_value(vtype, data):
    try:
        v = unpack_value(vtype, data)
        if vtype in ("double", "float"):
            if v == int(v) and abs(v) < 1e15:
                return "%.1f" % v
            return "%.6g" % v
        return str(v)
    except Exception:
        return "?"


# 大数缩写显示/输入: 1K=千, 1M=百万, 1B=十亿, 1T=万亿
ABBREV_UNITS = (("T", 1e12), ("B", 1e9), ("M", 1e6), ("K", 1e3))
ABBREV_MAP = {s: m for s, m in ABBREV_UNITS}


def fmt_abbrev(v):
    """把数值格式化为友好缩写: 2_288_973 -> '2.3M', 1_000_000_000 -> '1B'"""
    try:
        v = float(v)
    except Exception:
        return str(v)
    av = abs(v)
    for suf, m in ABBREV_UNITS:
        if av >= m:
            num = v / m
            if abs(num - round(num)) < 1e-9:
                return "%d%s" % (round(num), suf)
            return "%.1f%s" % (num, suf)
    return "%.6g" % v


def parse_number(text):
    """解析数值文本, 支持 1K/1M/1B/1T 等后缀 (大小写不敏感, 可带小数)。
    例: '2.3M' -> 2300000.0, '1B' -> 1e9, '1500' -> 1500.0"""
    t = (text or "").strip().lower()
    if not t:
        raise ValueError("空输入")
    last = t[-1].upper()          # 键统一大写
    if last in ABBREV_MAP:
        num_part = t[:-1].strip()
        if not num_part:
            raise ValueError("缺少数字: %r" % text)
        return float(num_part) * ABBREV_MAP[last]
    return float(t)


def parse_range(text):
    """解析范围文本 'min~max' (分隔符 ~ 或 -), 两端支持单位缩写。
    例: '1M~1.01M' -> (1000000.0, 1010000.0), '1000-1200' -> (1000.0, 1200.0)。
    不是范围/解析失败返回 None (调用方走精确扫描)。'-1M' 不会被误判 (前段为空)。"""
    t = (text or "").strip()
    if not t:
        return None
    for sep in ("~", "-"):
        if sep in t:
            parts = [p.strip() for p in t.split(sep)]
            if len(parts) == 2:
                try:
                    a = parse_number(parts[0])
                    b = parse_number(parts[1])
                    if a == b:
                        return None          # 区间退化 -> 精确值
                    return (min(a, b), max(a, b))
                except ValueError:
                    pass
    return None


# ============================================================
# 内存引擎
# ============================================================
class MemoryScanner:
    def __init__(self):
        self.pid = None
        self.name = None
        self.handle = None
        self.regions = []
        self.candidates = []      # [(addr, bytes)]
        self.scan_vtype = None    # 首次扫描后固定

    # ---------- 进程 ----------
    @staticmethod
    def find_processes():
        results = []
        h = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
        if not h or h == wintypes.HANDLE(-1).value:
            return results
        pe = PROCESSENTRY32W()
        pe.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = Process32FirstW(h, ctypes.byref(pe))
        while ok:
            results.append((pe.th32ProcessID, pe.szExeFile))
            ok = Process32NextW(h, ctypes.byref(pe))
        CloseHandle(h)
        return results

    @staticmethod
    def find_pid(name):
        for pid, exe in MemoryScanner.find_processes():
            if exe.lower() == name.lower():
                return pid
        return None

    @staticmethod
    def window_titles():
        """返回 {pid: [titles]}"""
        mapping = {}

        def cb(hwnd, _):
            if not IsWindowVisible(hwnd):
                return True
            pid = wintypes.DWORD()
            GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            ln = GetWindowTextLengthW(hwnd)
            if ln > 0:
                buf = ctypes.create_unicode_buffer(ln + 1)
                GetWindowTextW(hwnd, buf, ln + 1)
                t = buf.value.strip()
                if t:
                    mapping.setdefault(pid.value, []).append(t)
            return True

        WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
        EnumWindows(WNDENUMPROC(cb), 0)
        return mapping

    def attach(self, pid, name=""):
        if self.handle:
            self.detach()
        h = OpenProcess(PROCESS_QUERY_INFORMATION | PROCESS_VM_READ |
                        PROCESS_VM_WRITE | PROCESS_VM_OPERATION, False, pid)
        if not h:
            raise OSError("附加失败, 错误码 %d (可能权限不足/进程已退出)" % ctypes.get_last_error())
        self.handle = h
        self.pid = pid
        self.name = name
        self.refresh_regions()
        self.candidates = []
        self.scan_vtype = None

    def detach(self):
        if self.handle:
            CloseHandle(self.handle)
        self.handle = None
        self.pid = None
        self.name = None

    def refresh_regions(self):
        regions = []
        addr = 0
        while addr < (1 << 47):
            mbi = MEMORY_BASIC_INFORMATION()
            r = VirtualQueryEx(self.handle, ctypes.c_void_p(addr),
                               ctypes.byref(mbi), ctypes.sizeof(mbi))
            if not r:
                break
            if mbi.State == 0x1000:
                p = mbi.Protect & 0xFF
                if p in (0x02, 0x04, 0x08, 0x20, 0x40, 0x80):
                    regions.append((mbi.BaseAddress, mbi.RegionSize))
            if not mbi.RegionSize:
                break
            addr = (mbi.BaseAddress or 0) + mbi.RegionSize
        self.regions = regions

    @property
    def total_bytes(self):
        return sum(s for _, s in self.regions)

    # ---------- 读写 ----------
    def read(self, addr, size):
        buf = ctypes.create_string_buffer(size)
        n = ctypes.c_size_t(0)
        ok = ReadProcessMemory(self.handle, ctypes.c_void_p(addr), buf, size, ctypes.byref(n))
        if not ok or n.value < size:
            return None
        return buf.raw[:size]

    def read_value(self, addr, vtype):
        _, _, size, _ = TYPE_KEY[vtype]
        d = self.read(addr, size)
        if d is None:
            return None
        return unpack_value(vtype, d)

    def write(self, addr, data):
        n = ctypes.c_size_t(0)
        return bool(WriteProcessMemory(self.handle, ctypes.c_void_p(addr),
                                       data, len(data), ctypes.byref(n)))

    def write_value(self, addr, vtype, val):
        return self.write(addr, pack_value(vtype, val))

    # ---------- 扫描 ----------
    def scan_exact(self, value, vtype, progress_cb=None, cancel_evt=None, max_hits=50000,
                   full=False):
        """精确扫描值.
        full=False (默认): 老行为, max_hits 截断, 命中超限时低地址区先耗尽配额,
            托管堆(游戏对象)永远扫不到 — 小整数(如 17/33)会踩这个坑.
        full=True: 全量扫描不截断, 自动按类型对齐过滤 (double/int64 8 字节, float/int32 4 字节),
            anchor 本身为 0 时跳过零段, 返回全部命中 (候选多但完整覆盖托管堆).
        """
        pat = pack_value(vtype, value)
        size = len(pat)
        align = 8 if vtype in ("double", "int64") else (4 if vtype in ("float", "int32") else 1)
        hits = []
        total = self.total_bytes
        done = 0
        needs_nonzero = (value == 0 and full)
        for base, rsize in self.regions:
            if cancel_evt and cancel_evt.is_set():
                break
            buf = ctypes.create_string_buffer(rsize)
            n = ctypes.c_size_t(0)
            ok = ReadProcessMemory(self.handle, ctypes.c_void_p(base),
                                   buf, rsize, ctypes.byref(n))
            if ok and n.value >= size:
                data = buf.raw[:n.value]
                pos = 0
                while True:
                    pos = data.find(pat, pos)
                    if pos < 0:
                        break
                    if full:
                        # 对齐过滤 (过滤非对齐假命中)
                        if (base + pos) % align:
                            pos += 1
                            continue
                        # anchor 全零段跳过 (0.0/0 扫全内存不卡死)
                        if needs_nonzero and not data[pos:pos + size].strip(b"\x00"):
                            pos += 1
                            continue
                    hits.append((base + pos, pat))
                    pos += 1
                    if not full and len(hits) >= max_hits:
                        break
            done += rsize
            if progress_cb:
                progress_cb(done, total)
            if not full and len(hits) >= max_hits:
                break
        return hits

    def scan_range(self, vmin, vmax, vtype, progress_cb=None, cancel_evt=None,
                   max_hits=200000):
        """区间扫描: 匹配值在 [vmin, vmax] 内的所有对齐位置 (解决显示四舍五入/挂机增长,
        输入 1M 但实际是 1000002 扫不到的问题)。
        按类型对齐过滤, 区间不含 0 时跳过全零段; 返回 [(addr, 实际值字节)]。
        """
        _, _, size, fmt = TYPE_KEY[vtype]
        align = 8 if vtype in ("double", "int64") else (4 if vtype in ("float", "int32") else 1)
        results = []
        total = self.total_bytes
        done = 0
        skip_zero = not (vmin <= 0 <= vmax)
        for base, rsize in self.regions:
            if cancel_evt and cancel_evt.is_set():
                break
            buf = ctypes.create_string_buffer(rsize)
            n = ctypes.c_size_t(0)
            ok = ReadProcessMemory(self.handle, ctypes.c_void_p(base),
                                   buf, rsize, ctypes.byref(n))
            if ok and n.value >= size:
                data = buf.raw[:n.value]
                dlen = len(data)
                # 整块全零且区间不含 0: 直接跳过 (大部分内存是全零, 大幅加速)
                if skip_zero and not data.strip(b"\x00"):
                    done += rsize
                    continue
                first = (-base) % align          # 首对齐偏移
                for pos in range(first, dlen - size + 1, align):
                    try:
                        v = struct.unpack_from(fmt, data, pos)[0]
                    except struct.error:
                        break
                    if vmin <= v <= vmax:
                        results.append((base + pos, data[pos:pos + size]))
                        if len(results) >= max_hits:
                            break
            done += rsize
            if progress_cb:
                progress_cb(done, total)
            if len(results) >= max_hits:
                break
        return results

    def scan_template(self, value, vtype, verifies, progress_cb=None, cancel_evt=None,
                      max_hits=500, max_checks=8000000, align=1):
        """模板定位专用: 全内存扫描 anchor 值, 命中位置立即做结构验证。
        与 scan_exact 的区别: 验证在已读内存块内完成 (零额外系统调用),
        不按命中数截断扫描范围 — 解决高命中值 (如 int32 33) 时
        scan_exact 的 max_hits 在低地址区耗尽、永远扫不到托管堆的问题。
        性能保护: 命中处若 verify 偏移范围内全零 (零页), 且任一 verify 要求非零
        (expect>0 / min>0), 则整段跳过 — 低信息值 (如 0.0) 扫全内存也不会卡死。
        align: 要求命中地址按 align 字节对齐 (double 用 8, int32 用 4), 过滤非对齐假命中。
        verifies: [{"offset": int, "type": str, "expect": v, "tol": f,
                     "min": v, "max": v, "desc": str}]
        """
        pat = pack_value(vtype, value)
        size = len(pat)
        results = []
        total = self.total_bytes
        done = 0
        checks = 0
        if verifies:
            vlo = min(vf.get("offset", 0) for vf in verifies)
            vhi = max(vf.get("offset", 0) + TYPE_KEY[vf["type"]][2] for vf in verifies)
            # 任一 verify 要求非零值 => 全零段可安全跳过; anchor 本身为 0 时同理
            needs_nonzero = (
                any(vf.get("expect", 0) != 0 or vf.get("min", -1e300) > 0 for vf in verifies)
                or value == 0)
        else:
            vlo = vhi = 0
            needs_nonzero = False
        for base, rsize in self.regions:
            if cancel_evt and cancel_evt.is_set():
                break
            buf = ctypes.create_string_buffer(rsize)
            n = ctypes.c_size_t(0)
            ok = ReadProcessMemory(self.handle, ctypes.c_void_p(base),
                                   buf, rsize, ctypes.byref(n))
            if ok and n.value >= size:
                data = buf.raw[:n.value]
                dlen = len(data)
                pos = 0
                while True:
                    pos = data.find(pat, pos)
                    if pos < 0:
                        break
                    # 对齐过滤 (过滤非对齐假命中, 如 double 在奇数地址误匹配)
                    if align > 1 and ((base + pos) % align):
                        pos += 1
                        continue
                    # 快速跳过全零段 (verify 偏移范围内全是 0x00)
                    if needs_nonzero:
                        lo = pos + vlo
                        hi = pos + vhi
                        if 0 <= lo and hi <= dlen and not data[lo:hi].strip(b"\x00"):
                            pos += 1
                            continue
                    checks += 1
                    if self._check_verify_block(base, data, pos, verifies):
                        results.append(base + pos)
                        if len(results) >= max_hits:
                            break
                    if checks >= max_checks:
                        # 验证预算耗尽: 提前终止, 避免极端低信息值挂死
                        progress_cb and progress_cb(done, total)
                        return results
                    pos += 1
            done += rsize
            if progress_cb:
                progress_cb(done, total)
            if len(results) >= max_hits:
                break
        return results

    def _check_verify_block(self, base, data, pos, verifies):
        for vf in verifies:
            _, vk, vsize, fmt = TYPE_KEY[vf["type"]]
            p = pos + vf.get("offset", 0)
            if p < 0 or p + vsize > len(data):
                # 跨 region 边界: 回退单次 RPM 验证 (罕见)
                v = self.read_value(base + p, vf["type"])
                if v is None:
                    return False
            else:
                v = struct.unpack_from(fmt, data, p)[0]
            # nan/inf 永远不通过任何范围/等值验证 (避免 nan > tol 为 False 的坑)
            if isinstance(v, float) and (v != v or v in (float("inf"), float("-inf"))):
                return False
            if "expect" in vf:
                if abs(v - vf["expect"]) > vf.get("tol", 0.001):
                    return False
            else:
                if not (vf.get("min", -1e300) <= v <= vf.get("max", 1e300)):
                    return False
        return True

    def _cur_bytes(self, addr, size):
        return self.read(addr, size)

    # ---------- 过滤 ----------
    def filter_equal(self, new_value, vtype):
        pat = pack_value(vtype, new_value)
        keep = []
        for addr, _ in self.candidates:
            cur = self._cur_bytes(addr, len(pat))
            if cur is not None and cur == pat:
                keep.append((addr, pat))
        return keep

    def filter_unchanged(self):
        keep = []
        for addr, old in self.candidates:
            cur = self._cur_bytes(addr, len(old))
            if cur is not None and cur == old:
                keep.append((addr, old))
        return keep

    def filter_changed(self):
        keep = []
        for addr, old in self.candidates:
            cur = self._cur_bytes(addr, len(old))
            if cur is not None and cur != old:
                keep.append((addr, cur))
        return keep

    def filter_increased(self):
        keep = []
        for addr, old in self.candidates:
            cur = self._cur_bytes(addr, len(old))
            if cur is None:
                continue
            if self._cmp_num(cur, old) > 0:
                keep.append((addr, cur))
        return keep

    def filter_decreased(self):
        keep = []
        for addr, old in self.candidates:
            cur = self._cur_bytes(addr, len(old))
            if cur is None:
                continue
            if self._cmp_num(cur, old) < 0:
                keep.append((addr, cur))
        return keep

    def _cmp_num(self, a, b):
        vtype = self.scan_vtype or "double"
        try:
            return unpack_value(vtype, a) - unpack_value(vtype, b)
        except Exception:
            return 0

    def refresh_candidate_values(self):
        # 用索引方式更新候选的当前值 (元组解包赋值不会写回列表)
        for i in range(len(self.candidates)):
            addr, old = self.candidates[i]
            cur = self._cur_bytes(addr, len(old))
            if cur is not None:
                self.candidates[i] = (addr, cur)


# ============================================================
# 模板 (已分析的游戏结构)
# ============================================================
TEMPLATES = [
    {
        "name": "Scritchy Scratchy (挂个爽)",
        "process": "ScritchyScratchy.exe",
        "locates": [
            {
                "label": "Money 现金 (double)",
                "hint": "游戏界面显示的当前 Money 数值",
                "anchor": {"type": "double", "anchor_offset": 0x00, "align": 8,
                           "verify": [
                               {"offset": 0x08, "type": "double", "min": 0.0, "max": 1e7,
                                "desc": "TimeSpentInThisPrestige"},
                               {"offset": 0x40, "type": "double", "min": 0.0, "max": 1e15,
                                "desc": "LastUnlockedGoal"},
                               {"offset": 0x48, "type": "double", "min": 0.0, "max": 1e15,
                                "desc": "TotalEarned"},
                           ]},
                "fields": [
                    {"name": "Money 现金", "offset": 0x00, "type": "double"},
                    {"name": "LastUnlockedGoal 目标", "offset": 0x40, "type": "double"},
                    {"name": "TotalEarned 总赚取", "offset": 0x48, "type": "double"},
                ],
            },
            {
                "label": "声望 PrestigeCurrency (int32)",
                "hint": "游戏界面当前声望值 (注意: 声望是 4 字节整数, 不是 double)",
                "anchor": {"type": "int32", "anchor_offset": 0x28, "align": 4,
                           "verify": [
                               {"offset": -0x28, "type": "int32", "min": 0, "max": 1000000,
                                "desc": "TotalPrestigeSpent 总消耗"},
                               {"offset": -0x24, "type": "int32", "min": 0, "max": 1000,
                                "desc": "升级计数"},
                               {"offset": 0x20, "type": "int32", "expect": 257,
                                "desc": "结构标记"},
                           ]},
                "fields": [
                    {"name": "TotalPrestigeSpent 总消耗", "offset": 0x00, "type": "int32"},
                    {"name": "PrestigeCurrency 声望", "offset": 0x28, "type": "int32"},
                    {"name": "升级计数", "offset": 0x04, "type": "int32"},
                ],
            },
        ],
    },
]

# ============================================================
# 用户自定义模板 (右键"保存为模板"生成, 存结构特征而非固定地址)
# ============================================================
# 路径策略: frozen (exe) 时配置/模板写 exe 旁边, 内置资源从 _MEIPASS 读; 源码运行用脚本目录
if getattr(sys, "frozen", False):
    _BASE = os.path.dirname(sys.executable)
    _RES = sys._MEIPASS
else:
    _BASE = os.path.dirname(os.path.abspath(__file__))
    _RES = _BASE
USER_TEMPLATES_PATH = os.path.join(_BASE, "user_templates.json")


def load_user_templates():
    try:
        with open(USER_TEMPLATES_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
            if isinstance(data, list):
                return data
    except Exception:
        pass
    return []


def save_user_templates(tpls):
    try:
        with open(USER_TEMPLATES_PATH, "w", encoding="utf-8") as f:
            json.dump(tpls, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


# ============================================================
# GUI
# ============================================================
CONFIG_PATH = os.path.join(_BASE, "modder_config.json")
ICON_PATH = os.path.join(_RES, "modder.ico")
FONT = ("Microsoft YaHei UI", 10)
FONT_S = ("Microsoft YaHei UI", 9)
FONT_MONO = ("Consolas", 10)


def load_config():
    try:
        with open(CONFIG_PATH, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return {"last_process": None, "lock_interval": 0.3}


def save_config(cfg):
    try:
        with open(CONFIG_PATH, "w", encoding="utf-8") as f:
            json.dump(cfg, f, ensure_ascii=False, indent=2)
    except Exception:
        pass


class ProcessPicker(tk.Toplevel):
    """进程选择对话框"""

    def __init__(self, parent, on_pick):
        super().__init__(parent)
        self.title("选择进程")
        self.geometry("720x460")
        self.transient(parent)
        self.grab_set()
        try:
            if os.path.exists(ICON_PATH):
                self.iconbitmap(ICON_PATH)
        except Exception:
            pass
        self.on_pick = on_pick
        self.all_procs = []          # [(pid, exe, title)]
        self.titles = MemoryScanner.window_titles()
        self.icons = {}              # pid -> PhotoImage (持有引用防 GC)
        self._icon_queue = []        # 待提取的 pid
        self._row_of_pid = {}        # pid -> Treeview 行 iid

        ttk.Label(self, text="搜索:").pack(anchor="w", padx=8, pady=(8, 2))
        self.search_var = tk.StringVar()
        self.search_var.trace_add("write", lambda *_: self._refresh())
        self.search_box = ttk.Entry(self, textvariable=self.search_var, font=FONT)
        self.search_box.pack(fill="x", padx=8)

        cols = ("pid", "exe", "title")
        self.tree = ttk.Treeview(self, columns=cols, show="tree headings", height=16)
        self.tree.heading("#0", text="")
        self.tree.heading("pid", text="PID")
        self.tree.heading("exe", text="进程名")
        self.tree.heading("title", text="窗口标题")
        self.tree.column("#0", width=24, minwidth=24, stretch=False, anchor="center")
        self.tree.column("pid", width=80, anchor="e")
        self.tree.column("exe", width=220, anchor="w")
        self.tree.column("title", width=360, anchor="w")
        vsb = ttk.Scrollbar(self, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True, padx=(8, 0), pady=8)
        vsb.pack(side="left", fill="y", pady=8)

        btn_row = ttk.Frame(self)
        btn_row.pack(fill="x", padx=8, pady=(0, 8))
        ttk.Button(btn_row, text="刷新", command=self.on_refresh_btn).pack(side="left")
        ttk.Label(btn_row, text="图标加载中...", foreground="#999", font=FONT_S).pack(side="left", padx=8)
        ttk.Button(btn_row, text="附加选中进程", command=self._pick).pack(side="right")
        self.tree.bind("<Double-1>", lambda _: self._pick())

        self._load_procs()
        self._refresh()
        self._start_icon_loading()
        self.search_box.focus_set()

    def _load_procs(self):
        self.all_procs = []
        self.icons = {}
        for pid, exe in MemoryScanner.find_processes():
            if exe.lower() in ("system idle process",):
                continue
            titles = self.titles.get(pid, [])
            title = titles[0] if titles else ""
            self.all_procs.append((pid, exe, title))
        # 排序: 有窗口标题的进程置顶 (用户可见程序优先), 无窗口的沉底
        # 次级: 窗口标题 -> 进程名 -> PID
        self.all_procs.sort(key=lambda p: (0 if p[2] else 1,
                                            p[2].lower(),
                                            p[1].lower(),
                                            p[0]))

    def on_refresh_btn(self):
        self._load_procs()
        self._refresh()
        self._start_icon_loading()

    def _start_icon_loading(self):
        """分片提取图标: 每批 5 个, 避免卡 UI"""
        self._icon_queue = [p[0] for p in self.all_procs]
        self._icon_batch(0)

    def _icon_batch(self, idx):
        end = min(idx + 5, len(self._icon_queue))
        for i in range(idx, end):
            pid = self._icon_queue[i]
            if pid in self.icons:
                continue
            path = exe_path_of(pid)
            if path:
                try:
                    img = build_photoimage_from_icon(path)
                    if img is not None:
                        self.icons[pid] = img
                        # 只更新该行的图标, 不重建列表 (保留用户选中)
                        iid = self._row_of_pid.get(pid)
                        if iid:
                            self.tree.item(iid, image=img)
                except Exception:
                    pass
        if end < len(self._icon_queue):
            self.after(15, lambda: self._icon_batch(end))

    def _refresh(self):
        # 记录当前选中的 PID, 重建后恢复
        sel_pids = set()
        for item in self.tree.selection():
            vals = self.tree.item(item, "values")
            if vals:
                try:
                    sel_pids.add(int(vals[0]))
                except Exception:
                    pass
        self.tree.delete(*self.tree.get_children())
        self._row_of_pid = {}
        kw = self.search_var.get().strip().lower()
        for pid, exe, title in self.all_procs:
            if kw and kw not in exe.lower() and kw not in title.lower():
                continue
            img = self.icons.get(pid)
            # 注意: image=None 会导致 ttk.insert 参数解析错乱崩溃, 无图标时不传该参数
            if img is not None:
                iid = self.tree.insert("", "end", image=img,
                                       values=(str(pid), exe, title))
            else:
                iid = self.tree.insert("", "end", values=(str(pid), exe, title))
            self._row_of_pid[pid] = iid
        # 恢复选中
        for pid in sel_pids:
            iid = self._row_of_pid.get(pid)
            if iid:
                self.tree.selection_add(iid)
        # 若选中了有窗口的进程, 确保可见
        if sel_pids:
            first = self.tree.selection()
            if first:
                self.tree.see(first[0])

    def _pick(self):
        sel = self.tree.selection()
        if not sel:
            messagebox.showinfo("提示", "请先在列表中单击选中一个进程, 再点此按钮", parent=self)
            return
        vals = self.tree.item(sel[0], "values")
        pid = int(vals[0])
        exe = vals[1]
        self.destroy()
        self.on_pick(pid, exe)


class AddTemplateDialog(tk.Toplevel):
    """右键"保存为模板"对话框: 输入模板名/目标名/类型, 保存结构特征"""

    def __init__(self, parent, addr, scanner, on_save):
        super().__init__(parent)
        self.title("保存为模板")
        self.geometry("460x300")
        self.resizable(False, False)
        self.transient(parent)
        self.grab_set()
        self.addr = addr
        self.scanner = scanner
        self.on_save = on_save
        try:
            if os.path.exists(ICON_PATH):
                self.iconbitmap(ICON_PATH)
        except Exception:
            pass

        pad = {"padx": 10, "pady": 4}
        ttk.Label(self, text="将当前地址及附近结构保存为模板", font=FONT).pack(anchor="w", **pad)

        frm = ttk.Frame(self)
        frm.pack(fill="x", padx=10)
        ttk.Label(frm, text="模板名:").grid(row=0, column=0, sticky="w", pady=3)
        default_name = (scanner.name or "MyGame").rsplit(".", 1)[0]
        self.name_var = tk.StringVar(value=default_name)
        ttk.Entry(frm, textvariable=self.name_var, width=30, font=FONT).grid(
            row=0, column=1, sticky="w", pady=3)

        ttk.Label(frm, text="目标名:").grid(row=1, column=0, sticky="w", pady=3)
        self.target_var = tk.StringVar(value="数据")
        ttk.Entry(frm, textvariable=self.target_var, width=30, font=FONT).grid(
            row=1, column=1, sticky="w", pady=3)

        ttk.Label(frm, text="类型:").grid(row=2, column=0, sticky="w", pady=3)
        self.type_combo = ttk.Combobox(frm, width=28, state="readonly", font=FONT)
        self.type_combo["values"] = TYPE_LABELS
        self.type_combo.current(0)     # 默认 double
        self.type_combo.grid(row=2, column=1, sticky="w", pady=3)

        info = ("地址: 0x%X\n\n"
                "⚠ 重要: Unity 游戏的内存地址不是固定的!\n"
                "游戏每次启动 / GC 压缩时, 对象都会被移动, 地址会变。\n"
                "所以这里保存的是【结构特征】(锚点类型 + 附近字段验证),\n"
                "不是固定地址。下次直接用 [游戏模板] 页输入当前值重新定位即可。"
                % addr)
        ttk.Label(self, text=info, foreground="#555", font=FONT_S,
                  justify="left").pack(anchor="w", padx=10, pady=6)

        btns = ttk.Frame(self)
        btns.pack(fill="x", padx=10, pady=(0, 8))
        ttk.Button(btns, text="保存模板", command=self._ok).pack(side="right")
        ttk.Button(btns, text="取消", command=self.destroy).pack(side="right", padx=6)

    def _ok(self):
        name = self.name_var.get().strip()
        target = self.target_var.get().strip()
        if not name:
            messagebox.showwarning("提示", "请填写模板名", parent=self)
            return
        if not target:
            target = "数据"
        vtype = VALUE_TYPES[self.type_combo.current()][1]
        self.destroy()
        self.on_save(self.addr, name, target, vtype)


class GameModderGUI:
    def __init__(self, root):
        self.root = root
        self.scanner = MemoryScanner()
        self.cfg = load_config()
        self.msg_queue = queue.Queue()
        self.cancel_evt = threading.Event()
        self.lock_stop = threading.Event()
        self.lock_thread = None
        self.lock_targets = []       # [(addr, data)]
        self.template_idx = 0
        self.template_addr = None    # anchor 字段地址 (第一个)
        self.template_addrs = []     # 全部命中的 anchor 地址 (写入时全部改)
        self._proc_icon = None       # 已附加进程的图标引用

        root.title("通用内存修改器 GameModder")
        root.geometry("1000x760")
        root.minsize(880, 640)
        try:
            if os.path.exists(ICON_PATH):
                root.iconbitmap(ICON_PATH)
        except Exception:
            pass

        style = ttk.Style(root)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass
        style.configure("Treeview", font=FONT_MONO, rowheight=24)
        style.configure("Treeview.Heading", font=FONT)

        self._build_ui()
        self._poll_queue()

        # 自动附加上次进程
        last = self.cfg.get("last_process")
        pid = MemoryScanner.find_pid(last) if last else None
        if pid:
            self._do_attach(pid, last)
        else:
            self.log("未找到上次进程 %r, 请点击 [选择进程]" % last)
            self.root.after(300, self.open_process_picker)

    # ================= UI =================
    def _build_ui(self):
        top = ttk.Frame(self.root, padding=(8, 6))
        top.pack(fill="x")
        ttk.Label(top, text="进程:").pack(side="left")
        self.proc_lbl = ttk.Label(top, text="未附加", foreground="#c00", font=FONT)
        self.proc_lbl.pack(side="left", padx=8)
        ttk.Button(top, text="选择进程", command=self.open_process_picker).pack(side="left", padx=4)
        ttk.Button(top, text="断开", command=self.on_detach).pack(side="left", padx=4)
        ttk.Button(top, text="刷新内存区域", command=self.on_refresh_mem).pack(side="left", padx=4)
        self.lock_lbl = ttk.Label(top, text="", foreground="#c60", font=FONT)
        self.lock_lbl.pack(side="right", padx=8)

        nb = ttk.Notebook(self.root)
        nb.pack(fill="both", expand=True, padx=8, pady=(4, 2))
        self.tab_scan = ttk.Frame(nb, padding=8)
        self.tab_tpl = ttk.Frame(nb, padding=8)
        self.tab_man = ttk.Frame(nb, padding=8)
        nb.add(self.tab_scan, text=" 数值扫描 ")
        nb.add(self.tab_tpl, text=" 游戏模板 ")
        nb.add(self.tab_man, text=" 手动读写 ")

        self._build_scan_tab()
        self._build_tpl_tab()
        self._build_man_tab()

        log_frame = ttk.LabelFrame(self.root, text=" 运行日志 ", padding=4)
        log_frame.pack(fill="both", padx=8, pady=(0, 8))
        self.log_box = ScrolledText(log_frame, height=8, font=("Consolas", 9),
                                    state="disabled", background="#1e1e1e", foreground="#d4d4d4")
        self.log_box.pack(fill="both", expand=True)

    # ---------- 数值扫描页 ----------
    def _build_scan_tab(self):
        f = self.tab_scan

        # 引导提示
        guide = ("扫描提示: 第一次扫到成千上万地址是正常的!\n"
                 "① 首次扫描输入当前值 ② 回游戏让数值变化(花钱/赚到) ③ 点 [已变化] 或输入新值点 [等于新值]\n"
                 "④ 重复 ②③ 两三轮, 候选收敛到 1~3 个 ⑤ 选中写入并回游戏验证")
        ttk.Label(f, text=guide, foreground="#444", font=FONT_S).pack(anchor="w", pady=(0, 6))

        row1 = ttk.Frame(f)
        row1.pack(fill="x", pady=2)
        ttk.Label(row1, text="扫描值:").pack(side="left")
        self.scan_value = ttk.Entry(row1, width=14, font=FONT_MONO)
        self.scan_value.pack(side="left", padx=4)
        self.scan_value.insert(0, "0")
        ttk.Label(row1, text="(支持 1K/1M/1B/1T 缩写; 范围如 1M~1.01M 扫区间)",
                  foreground="#888", font=FONT_S).pack(side="left", padx=(0, 8))
        ttk.Label(row1, text="类型:").pack(side="left")
        self.scan_type = ttk.Combobox(row1, width=28, state="readonly", font=FONT)
        self.scan_type["values"] = TYPE_LABELS
        # 默认不选类型: 推荐点 [自动识别类型], 工具自动判断 double/float/int64/int32
        self.scan_type.pack(side="left", padx=4)
        self.scan_type.set("")
        ttk.Button(row1, text="自动识别类型", command=self.on_auto_detect).pack(side="left", padx=4)
        ttk.Label(row1, text="(推荐! 四种类型自动扫一遍并推荐, 不用自己判断)", foreground="#c60",
                  font=FONT_S).pack(side="left")
        ttk.Label(row1, text="(首次扫描后锁定)", foreground="#999", font=FONT_S).pack(side="left")

        row2 = ttk.Frame(f)
        row2.pack(fill="x", pady=2)
        ttk.Button(row2, text="① 首次扫描", command=self.on_first_scan).pack(side="left")
        ttk.Button(row2, text="等于新值", command=self.on_rescan).pack(side="left", padx=4)
        ttk.Button(row2, text="已变化", command=self.on_changed).pack(side="left", padx=4)
        ttk.Button(row2, text="未变化", command=self.on_unchanged).pack(side="left", padx=4)
        ttk.Button(row2, text="增大了", command=self.on_increased).pack(side="left", padx=4)
        ttk.Button(row2, text="减小了", command=self.on_decreased).pack(side="left", padx=4)
        ttk.Button(row2, text="清除", command=self.on_clear).pack(side="left", padx=8)

        row_f = ttk.Frame(f)
        row_f.pack(fill="x", pady=2)
        ttk.Label(row_f, text="搜索地址:").pack(side="left")
        self.addr_filter_var = tk.StringVar()
        self.addr_filter_entry = ttk.Entry(row_f, textvariable=self.addr_filter_var,
                                           width=18, font=FONT_MONO)
        self.addr_filter_entry.pack(side="left", padx=4)
        self.addr_filter_entry.bind("<KeyRelease>", self._on_filter_changed)
        ttk.Label(row_f, text="搜索当前值:").pack(side="left", padx=(12, 2))
        self.val_filter_var = tk.StringVar()
        self.val_filter_entry = ttk.Entry(row_f, textvariable=self.val_filter_var,
                                          width=14, font=FONT_MONO)
        self.val_filter_entry.pack(side="left", padx=4)
        self.val_filter_entry.bind("<KeyRelease>", self._on_filter_changed)
        ttk.Button(row_f, text="清空搜索", command=self._clear_filter).pack(side="left", padx=4)
        ttk.Label(row_f, text="(地址输片段如 0x1793; 当前值输数值, 可同时用)",
                  foreground="#999", font=FONT_S).pack(side="left")
        self._filter_job = None

        self.count_lbl = ttk.Label(f, text="候选: 0", foreground="#c00", font=FONT)
        self.count_lbl.pack(anchor="w", pady=(4, 0))
        self.guide_lbl = ttk.Label(f, text="", foreground="#c80", font=FONT_S)
        self.guide_lbl.pack(anchor="w")
        self.progress = ttk.Progressbar(f, mode="determinate", maximum=100)
        self.progress.pack(fill="x", pady=4)

        # 候选列表 + 右键菜单
        cols = ("addr", "value")
        self.tree = ttk.Treeview(f, columns=cols, show="headings", height=12)
        self.tree.heading("addr", text="地址 (16进制)")
        self.tree.heading("value", text="当前值")
        self.tree.column("addr", width=240, anchor="w")
        self.tree.column("value", width=160, anchor="e")
        vsb = ttk.Scrollbar(f, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=vsb.set)
        self.tree.pack(side="left", fill="both", expand=True, pady=4)
        vsb.pack(side="left", fill="y", pady=4)

        menu = tk.Menu(self.root, tearoff=0)
        menu.add_command(label="写入值...", command=self.on_write_selected_dialog)
        menu.add_command(label="锁定该地址", command=self.on_lock_selected)
        menu.add_separator()
        menu.add_command(label="保存为模板...", command=self.on_save_template)
        menu.add_separator()
        menu.add_command(label="复制地址", command=self._copy_addr)
        menu.add_command(label="刷新显示值", command=self.on_refresh_display)
        self.tree_menu = menu
        self.tree.bind("<Button-3>", self._popup_menu)

        row3 = ttk.Frame(f)
        row3.pack(fill="x", pady=2)
        ttk.Label(row3, text="修改为:").pack(side="left")
        self.newval = ttk.Entry(row3, width=12, font=FONT_MONO)
        self.newval.pack(side="left", padx=4)
        ttk.Button(row3, text="写入选中", command=self.on_write_selected).pack(side="left", padx=4)
        ttk.Button(row3, text="写入全部", command=self.on_write_all).pack(side="left", padx=4)
        ttk.Button(row3, text="锁定选中", command=self.on_lock_selected).pack(side="left", padx=4)
        ttk.Button(row3, text="停止锁定", command=self.on_unlock).pack(side="left", padx=4)

    # ---------- 模板页 ----------
    def _build_tpl_tab(self):
        f = self.tab_tpl
        info = ("针对已分析过的游戏结构一键定位字段。\n"
                "选择模板 -> 附加对应进程 -> 选定位目标(如 声望/金钱) -> 输入界面显示的当前值 -> 自动定位并验证特征, 然后直接改字段。\n"
                "换游戏没有模板时, 用 [数值扫描] 页手动定位即可。")
        ttk.Label(f, text=info, foreground="#555", font=FONT_S).pack(anchor="w", pady=(0, 6))

        row1 = ttk.Frame(f)
        row1.pack(fill="x", pady=2)
        ttk.Label(row1, text="模板:").pack(side="left")
        self.tpl_combo = ttk.Combobox(row1, width=26, state="readonly", font=FONT)
        self.tpl_combo["values"] = [t["name"] for t in self.all_templates()]
        self.tpl_combo.current(0)
        self.tpl_combo.pack(side="left", padx=4)
        self.tpl_combo.bind("<<ComboboxSelected>>", lambda e: self._tpl_changed())
        ttk.Button(row1, text="附加模板进程", command=self.on_tpl_attach).pack(side="left", padx=4)

        row1b = ttk.Frame(f)
        row1b.pack(fill="x", pady=2)
        ttk.Label(row1b, text="定位目标:").pack(side="left")
        self.tpl_target = ttk.Combobox(row1b, width=30, state="readonly", font=FONT)
        self.tpl_target.pack(side="left", padx=4)
        self.tpl_target.bind("<<ComboboxSelected>>", lambda e: self._tpl_changed())
        ttk.Label(row1b, text="当前值:").pack(side="left", padx=(16, 2))
        self.tpl_value = ttk.Entry(row1b, width=12, font=FONT_MONO)
        self.tpl_value.pack(side="left", padx=4)
        ttk.Label(row1b, text="(支持 1K/1M/1B)", foreground="#888", font=FONT_S).pack(side="left", padx=(0, 4))
        ttk.Button(row1b, text="自动定位", command=self.on_tpl_locate).pack(side="left", padx=4)
        self.tpl_status = ttk.Label(row1b, text="未定位", foreground="#c00")
        self.tpl_status.pack(side="left", padx=8)
        self.tpl_hint = ttk.Label(f, text="", foreground="#888", font=FONT_S)
        self.tpl_hint.pack(anchor="w", pady=(0, 4))

        cols = ("field", "offset", "value", "type")
        self.tpl_tree = ttk.Treeview(f, columns=cols, show="headings", height=10)
        self.tpl_tree.heading("field", text="字段")
        self.tpl_tree.heading("offset", text="偏移")
        self.tpl_tree.heading("value", text="当前值")
        self.tpl_tree.heading("type", text="类型")
        self.tpl_tree.column("field", width=220, anchor="w")
        self.tpl_tree.column("offset", width=90, anchor="w")
        self.tpl_tree.column("value", width=200, anchor="e")
        self.tpl_tree.column("type", width=110, anchor="w")
        vsb = ttk.Scrollbar(f, orient="vertical", command=self.tpl_tree.yview)
        self.tpl_tree.configure(yscrollcommand=vsb.set)
        self.tpl_tree.pack(side="left", fill="both", expand=True, pady=6)
        vsb.pack(side="left", fill="y", pady=6)

        row2 = ttk.Frame(f)
        row2.pack(fill="x", pady=2)
        ttk.Label(row2, text="字段:").pack(side="left")
        self.tpl_field = ttk.Combobox(row2, width=26, state="readonly", font=FONT)
        self.tpl_field.pack(side="left", padx=4)
        ttk.Label(row2, text="修改为:").pack(side="left")
        self.tpl_newval = ttk.Entry(row2, width=12, font=FONT_MONO)
        self.tpl_newval.pack(side="left", padx=4)
        ttk.Button(row2, text="写入", command=self.on_tpl_write).pack(side="left", padx=4)
        ttk.Button(row2, text="锁定", command=self.on_tpl_lock).pack(side="left", padx=4)
        ttk.Button(row2, text="停止锁定", command=self.on_unlock).pack(side="left", padx=4)
        ttk.Button(row2, text="刷新显示", command=self.on_tpl_refresh).pack(side="left", padx=4)

        self._tpl_changed()   # 初始化定位目标下拉框与提示

    # ---------- 手动页 ----------
    def _build_man_tab(self):
        f = self.tab_man
        row1 = ttk.Frame(f)
        row1.pack(fill="x", pady=6)
        ttk.Label(row1, text="地址 (16进制, 可带 0x):").pack(side="left")
        self.man_addr = ttk.Entry(row1, width=20, font=FONT_MONO)
        self.man_addr.pack(side="left", padx=4)
        ttk.Label(row1, text="类型:").pack(side="left")
        self.man_type = ttk.Combobox(row1, width=28, state="readonly", font=FONT)
        self.man_type["values"] = TYPE_LABELS
        self.man_type.current(0)
        self.man_type.pack(side="left", padx=4)
        ttk.Label(row1, text="数值:").pack(side="left")
        self.man_value = ttk.Entry(row1, width=12, font=FONT_MONO)
        self.man_value.pack(side="left", padx=4)
        ttk.Button(row1, text="读取", command=self.on_man_read).pack(side="left", padx=4)
        ttk.Button(row1, text="写入", command=self.on_man_write).pack(side="left", padx=4)
        ttk.Label(f, text="提示: 地址示例 0x17A7FA55590。读取后可直接改值写入。",
                  foreground="#888", font=FONT_S).pack(anchor="w", pady=4)

    # ================= 通用 =================
    def log(self, msg):
        ts = time.strftime("%H:%M:%S")
        self.log_box.configure(state="normal")
        self.log_box.insert("end", "[%s] %s\n" % (ts, msg))
        self.log_box.see("end")
        self.log_box.configure(state="disabled")

    def _check(self):
        if not self.scanner.handle:
            raise RuntimeError("未附加进程")

    def _require_pid(self):
        if not self.scanner.handle:
            self.open_process_picker()
            return False
        return True

    def get_vtype(self, widget):
        idx = widget.current()
        if idx < 0:
            return None
        return VALUE_TYPES[idx][1]

    def parse_val(self, text, vtype):
        """解析用户输入的数值, 支持 1K/1M/1B/1T 缩写 (如 '2.3M' -> 2300000)"""
        text = text.strip()
        num = parse_number(text)
        if vtype in ("double", "float"):
            return num
        return int(num)

    # ---------- 进程 ----------
    def open_process_picker(self):
        ProcessPicker(self.root, self._on_pick)

    def _on_pick(self, pid, exe):
        self._do_attach(pid, exe)
        self.cfg["last_process"] = exe
        save_config(self.cfg)

    def on_detach(self):
        self.on_unlock()
        self.scanner.detach()
        self.proc_lbl.configure(text="未附加", foreground="#c00")
        self.log("已断开进程")

    def on_refresh_mem(self):
        if not self.scanner.handle:
            return
        self.scanner.refresh_regions()
        self.log("已刷新内存区域: %d 个, %.1f MB" %
                 (len(self.scanner.regions), self.scanner.total_bytes / 1024 / 1024))

    def _do_attach(self, pid, name):
        try:
            self.scanner.attach(pid, name)
        except OSError as e:
            self.log("附加失败: %s" % e)
            self.proc_lbl.configure(text="附加失败", foreground="#c00")
            messagebox.showwarning("附加失败",
                                   "无法附加 %s (PID %d):\n%s\n\n"
                                   "可能原因: 权限不足 / 进程已退出 / 该进程受保护。" %
                                   (name, pid, e))
            return
        # 先显示附加成功, 图标在主线程同步提取 (PhotoImage 构造涉及 Tcl, 不可放后台线程)
        self.proc_lbl.configure(text="%s (PID %d) | %.0f MB" %
                                (name, pid, self.scanner.total_bytes / 1024 / 1024),
                                foreground="#080")
        self.log("已附加 %s (PID %d), 可读内存 %.1f MB" %
                 (name, pid, self.scanner.total_bytes / 1024 / 1024))
        try:
            path = exe_path_of(pid)
            if path:
                icon = build_photoimage_from_icon(path)
                if icon is not None:
                    self.proc_lbl.configure(image=icon, compound="left")
                    self._proc_icon = icon   # 持有引用防 GC
        except Exception:
            pass

    # ---------- 扫描 ----------
    def _scan_worker(self, action, val, vtype, rng=None):
        self.cancel_evt.clear()
        try:
            if action == "first":
                self.scanner.scan_vtype = vtype
                if rng:
                    vmin, vmax = rng
                    self.log("范围扫描: %.6g ~ %.6g (%s, 全量) ..." % (vmin, vmax, vtype))
                    hits = self.scanner.scan_range(vmin, vmax, vtype,
                                                   self._prog_cb, self.cancel_evt)
                else:
                    self.log("首次扫描: %s (%s, 全量) ..." % (val, vtype))
                    hits = self.scanner.scan_exact(val, vtype, self._prog_cb, self.cancel_evt,
                                                   full=True)
                self.scanner.candidates = hits
            elif action == "equal":
                if not vtype:
                    self.msg_queue.put(("error", "请先执行首次扫描"))
                    return
                self.scanner.candidates = self.scanner.filter_equal(val, vtype)
            elif action == "changed":
                self.scanner.candidates = self.scanner.filter_changed()
            elif action == "unchanged":
                self.scanner.candidates = self.scanner.filter_unchanged()
            elif action == "increased":
                self.scanner.candidates = self.scanner.filter_increased()
            elif action == "decreased":
                self.scanner.candidates = self.scanner.filter_decreased()
        except Exception as e:
            self.msg_queue.put(("error", "操作出错: %s" % e))
            return
        # 注意: 必须是 2 元组 (kind, payload), payload=(n, action);
        # 3 元组会让 _poll_queue 的 kind,payload = get_nowait() 解包失败炸消息泵
        self.msg_queue.put(("scan_done", (len(self.scanner.candidates), action)))

    def _prog_cb(self, done, total):
        if total > 0:
            self.msg_queue.put(("progress", int(done * 100 / total)))

    def _run_scan(self, action):
        # 注意: 必须在主线程解析 UI 输入, 后台线程禁止访问 tkinter 控件 (Tcl 非线程安全会死锁)
        if not self._require_pid():
            return
        val = None
        rng = None
        vtype = None
        try:
            if action == "first":
                vtype = self.get_vtype(self.scan_type)
                if not vtype:
                    self.log("请先选择类型 — 推荐点 [自动识别类型], 或手动从下拉框选一个")
                    return
                text = self.scan_value.get()
                rng = parse_range(text)         # 范围语法 (如 1M~1.01M)
                val = self.parse_val(text, vtype) if rng is None else None
            elif action == "equal":
                vtype = self.scanner.scan_vtype
                if not vtype:
                    self.log("请先执行首次扫描")
                    return
                val = self.parse_val(self.scan_value.get(), vtype)
        except Exception as e:
            self.log("数值格式错误: %s" % e)
            return
        threading.Thread(target=self._scan_worker, args=(action, val, vtype, rng),
                         daemon=True).start()

    # ---------- 自动识别类型 ----------
    def on_auto_detect(self):
        if not self._require_pid():
            return
        try:
            text = self.scan_value.get().strip()
            if not text:
                self.log("请先输入游戏里显示的当前数值")
                return
            v = parse_number(text)      # 支持 1K/1M/1B 缩写
        except Exception:
            self.log("数值格式错误, 请输入数字(如 33 或 33.5)")
            return
        self.progress["value"] = 0
        self.log("自动识别中: 依次扫描 double/float/int64/int32 (约 10~15 秒) ...")
        threading.Thread(target=self._auto_detect_worker, args=(v,), daemon=True).start()

    def _auto_detect_worker(self, v):
        try:
            results = {}
            order = ("double", "float", "int64", "int32")
            for i, vtype in enumerate(order):
                base = i * 25
                def cb(done, total, _vt=vtype, _base=base):
                    if total > 0:
                        self.msg_queue.put(("progress", _base + int(done * 100 / total) // 4))
                scan_v = v if vtype in ("double", "float") else int(v)
                hits = self.scanner.scan_exact(scan_v, vtype, cb, self.cancel_evt, full=True)
                results[vtype] = (len(hits), len(hits) >= 50000)
            rec = self._pick_type(v, results)
            self.msg_queue.put(("auto_done", (v, results, rec)))
        except Exception as e:
            self.msg_queue.put(("error", "自动识别出错: %s" % e))

    def _pick_type(self, v, results):
        """启发式推荐类型 (不保证绝对正确, 推荐后仍需多轮过滤验证):
        - 输入含小数 -> 必是 float/double, 命中少的优先
        - 输入整数 -> 超过 21 亿必须 int64; int32 命中截断(≥5万)强烈提示 int 字段;
          否则对比 double/int32 命中数, 适中且更少者优先
        """
        if isinstance(v, float) and not v.is_integer():
            d = results["double"][0]
            f = results["float"][0]
            if f and (d == 0 or f <= d):
                return "float"
            return "double"
        if abs(v) > 0x7FFFFFFF:
            return "int64"
        d, dt = results["double"]
        i32, i32t = results["int32"]
        i64, i64t = results["int64"]
        if i32t:
            return "int32"          # int 值遍布内存是整数计数器的典型特征
        # 整数输入且均未截断: 取命中更少者 (真字段在内存里通常比噪声少)
        if 0 < d and (i32 == 0 or d <= i32):
            return "double"
        if 0 < i32 and (d == 0 or i32 < d):
            return "int32"
        if d == 0 and i32 == 0:
            if 0 < i64 < 50000:
                return "int64"
        return "double"

    def on_first_scan(self):
        self._run_scan("first")

    def on_rescan(self):
        self._run_scan("equal")

    def on_changed(self):
        self._run_scan("changed")

    def on_unchanged(self):
        self._run_scan("unchanged")

    def on_increased(self):
        self._run_scan("increased")

    def on_decreased(self):
        self._run_scan("decreased")

    def on_clear(self):
        self.scanner.candidates = []
        self.scanner.scan_vtype = None
        self.scan_type.configure(state="readonly")
        self.addr_filter_var.set("")
        self.val_filter_var.set("")
        self._render_candidates()
        self.log("已清除候选")

    def _render_candidates(self):
        self.tree.delete(*self.tree.get_children())
        vtype = self.scanner.scan_vtype or "double"
        kw_addr = self.addr_filter_var.get().strip().lower()
        kw_val = self.val_filter_var.get().strip().lower()
        total = len(self.scanner.candidates)
        if kw_addr or kw_val:
            # 两个搜索框独立匹配, 同时填时取交集 (全量过滤, 不受显示上限限制)
            # 值匹配同时支持: 精确值 (如 2289737 / 2289737.0) 和缩写 (如 2.3M / 2m)
            matched = []
            for addr, data in self.scanner.candidates:
                if kw_addr and kw_addr not in ("0x%X" % addr).lower():
                    continue
                if kw_val:
                    fv = fmt_value(vtype, data)
                    fa = fmt_abbrev(unpack_value(vtype, data)).lower()
                    if kw_val not in fv.lower() and kw_val not in fa:
                        continue
                matched.append((addr, data))
            src = matched
        else:
            src = self.scanner.candidates
        # 渲染限流: 最多显示 500 行, 避免命中几万时 Treeview 卡死 UI
        shown = 0
        for addr, data in src[:500]:
            v = unpack_value(vtype, data)
            disp = fmt_value(vtype, data)
            if isinstance(v, float) and abs(v) >= 1e6:
                disp += "  (%s)" % fmt_abbrev(v)
            self.tree.insert("", "end", values=("0x%X" % addr, disp))
            shown += 1
        if kw_addr or kw_val:
            if len(matched) == 0:
                self.count_lbl.configure(text="无匹配 (候选 %d 个)" % total, foreground="#c00")
            else:
                self.count_lbl.configure(
                    text="匹配 %d / 候选 %d" % (len(matched), total), foreground="#080")
        elif total > shown:
            self.count_lbl.configure(text="候选: %d (仅显示前 %d 条)" % (total, shown),
                                     foreground="#c00")
        else:
            self.count_lbl.configure(text="候选: %d" % total, foreground="#c00")
        n = total
        if n == 0:
            self.guide_lbl.configure(
                text="没有匹配。检查类型是否选对(金币一般是 double/int64), 或数值是否输对。")
        elif n <= 3:
            self.guide_lbl.configure(
                text="候选很少了! 若游戏数值在自动增长(挂机游戏), 先填目标值再点 [锁定] 持续压值; "
                     "或选中地址->写入->回游戏验证, 不生效说明不是真身(内存有多个副本), 点 [写入全部] 或重新定位。",
                foreground="#080")
        elif n <= 100:
            self.guide_lbl.configure(
                text="还有 %d 个候选。再回游戏让数值变化一次, 点 [已变化] 或输入新值点 [等于新值] 继续缩小。" % n)
        elif n <= 5000:
            self.guide_lbl.configure(
                text="候选较多 (%d 个)。回游戏让数值变化(花钱/获得), 再点 [已变化] 或 [等于新值]。" % n)
        else:
            self.guide_lbl.configure(
                text="候选很多 (%d 个, 正常现象)。关键: 回游戏把数值改一下, 回来点 [已变化] 或输入新值 [等于新值]。" % n)

    def _on_filter_changed(self, *a):
        # 输入防抖: 200ms 内不重复渲染
        if self._filter_job:
            self.root.after_cancel(self._filter_job)
        self._filter_job = self.root.after(200, self._render_candidates)

    def _clear_filter(self):
        self.addr_filter_var.set("")
        self.val_filter_var.set("")
        self._render_candidates()

    # ---------- 写入选中的 ----------
    def _selected_addrs(self):
        addrs = []
        for item in self.tree.selection():
            vals = self.tree.item(item, "values")
            if vals:
                try:
                    addrs.append(int(vals[0], 16))
                except Exception:
                    pass
        return addrs

    def _write_addrs(self, addrs, label):
        """写入地址列表, 返回 (成功数, 读回不一致数, 尝试总数)。失败返回 None"""
        vtype = self.scanner.scan_vtype or "double"
        try:
            val = self.parse_val(self.newval.get(), vtype)
        except Exception as e:
            self.log("数值格式错误: %s" % e)
            return None
        data = pack_value(vtype, val)
        ok = 0
        mism = 0
        for a in addrs:
            if self.scanner.write(a, data):
                ok += 1
                # 写后读回校验: 地址可能被游戏重启/Unity GC 移动而失效
                try:
                    rb = self.scanner.read_value(a, vtype)
                    if rb is None or rb != val:
                        mism += 1
                except Exception:
                    pass
        self.log("已写入 %d/%d 个地址 -> %s (%s) [%s]" % (ok, len(addrs), val, vtype, label))
        total_attempt = len(addrs)
        if mism:
            # 写后读回不一致: 该地址多半不是真身 (游戏内存有多个副本, 只写显示/缓存副本无效)
            # 或地址已失效。候选少时自动补写其余候选, 提高命中真身概率。
            rest = [a for a, _ in self.scanner.candidates if a not in addrs]
            if rest and len(self.scanner.candidates) <= 30:
                extra_ok = 0
                for a in rest:
                    if self.scanner.write(a, data):
                        extra_ok += 1
                total_attempt += len(rest)
                self.log("警告: %d 个地址写后读回不一致(可能不是真身, 游戏内存有多个副本)。"
                         "已自动补写其余 %d 个候选(成功 %d)。回游戏确认, 若仍不生效: "
                         "值在自动增长(挂机)就点 [锁定], 或重新定位再写" %
                         (mism, len(rest), extra_ok))
            elif rest:
                self.log("警告: %d 个地址写后读回不一致, 可能不是真身。候选较多(%d 个), "
                         "请选中全部候选用 [写入全部]; 值在自动增长(挂机)就点 [锁定]" %
                         (mism, len(self.scanner.candidates)))
            else:
                self.log("警告: 写入后读回不一致。只有 1 个候选还写不进, 通常是: "
                         "① 该值在自动增长(挂机游戏)被游戏覆盖 -> 用 [锁定] 持续压值; "
                         "② 地址已失效(重启/GC 移动) -> 重新定位; "
                         "③ 真身不止一份但扫描时其他副本不是 %s -> 用 [模板] 页定位更准" % vtype)
        self._render_candidates()
        return ok, mism, total_attempt

    def on_write_selected(self):
        if not self._require_pid():
            return
        addrs = self._selected_addrs()
        if not addrs:
            self.log("请先在列表中选中地址")
            return
        vtype = self.scanner.scan_vtype or "double"
        try:
            val = self.parse_val(self.newval.get(), vtype)
        except Exception:
            val = "<未填>"
        # 交互确认: 显示将写入的地址数和目标值, 防止手滑
        preview = ", ".join("0x%X" % a for a in addrs[:5])
        if len(addrs) > 5:
            preview += " ... 等 %d 个" % len(addrs)
        if not messagebox.askyesno(
                "确认写入",
                "将向选中 %d 个地址写入:\n\n%s\n\n目标值: %s (%s)\n\n确定写入吗?" %
                (len(addrs), preview, val, vtype)):
            self.log("已取消写入")
            return
        result = self._write_addrs(addrs, "选中")
        self._show_write_result(result, "写入选中")

    def on_write_selected_dialog(self):
        self.on_write_selected()

    def _show_write_result(self, result, label):
        """写入后的结果反馈弹窗 (result=None 表示输入错误/未执行)"""
        if result is None:
            return
        ok, mism, total = result
        if mism:
            messagebox.showwarning(
                "写入结果",
                "%s: 系统写入成功 %d/%d 处\n\n⚠ %d 处写后读回不一致 (游戏内存有多个副本/数值被游戏覆盖)。\n"
                "详细诊断已输出到运行日志, 建议回游戏看是否生效; 无效则点 [锁定] 或重新定位。" %
                (label, ok, total, mism))
        elif ok == 0:
            messagebox.showerror("写入失败", "%s: 全部写入失败 (错误码见日志)" % label)
        else:
            messagebox.showinfo("写入结果", "%s: 成功写入 %d/%d 处, 读回校验通过。\n回游戏确认数值是否变化。" %
                                (label, ok, total))

    def on_write_all(self):
        if not self._require_pid():
            return
        if not self.scanner.candidates:
            self.log("候选为空")
            return
        if not messagebox.askyesno("确认", "将写入全部 %d 个候选地址, 确定?" %
                                   len(self.scanner.candidates)):
            self.log("已取消写入全部")
            return
        result = self._write_addrs([a for a, _ in self.scanner.candidates], "全部")
        self._show_write_result(result, "写入全部")

    # ---------- 锁定 ----------
    def _start_lock(self, targets):
        self.on_unlock()
        time.sleep(0.15)
        self.lock_stop.clear()
        self.lock_targets = targets
        self.lock_thread = threading.Thread(target=self._lock_loop, daemon=True)
        self.lock_thread.start()
        self.lock_lbl.configure(text="锁定中 (%d 个地址)" % len(targets))
        self.log("已开启锁定 %d 个地址 (每 %.1fs 自动写入)" %
                 (len(targets), self.cfg.get("lock_interval", 0.3)))

    def _lock_loop(self):
        interval = self.cfg.get("lock_interval", 0.3)
        while not self.lock_stop.is_set():
            for addr, data in self.lock_targets:
                self.scanner.write(addr, data)
            time.sleep(interval)

    def on_lock_selected(self):
        if not self._require_pid():
            return
        addrs = self._selected_addrs()
        if not addrs:
            self.log("请先在列表中选中地址")
            return
        vtype = self.scanner.scan_vtype or "double"
        try:
            val = self.parse_val(self.newval.get(), vtype)
        except Exception as e:
            self.log("数值格式错误: %s" % e)
            return
        data = pack_value(vtype, val)
        self._start_lock([(a, data) for a in addrs])

    def on_unlock(self):
        self.lock_stop.set()
        if self.lock_thread:
            self.lock_thread.join(timeout=1)
            self.lock_thread = None
        self.lock_targets = []
        self.lock_lbl.configure(text="")

    # ---------- 右键 ----------
    def _popup_menu(self, evt):
        item = self.tree.identify_row(evt.y)
        if item:
            self.tree.selection_set(item)
            self.tree_menu.tk_popup(evt.x_root, evt.y_root)

    def _copy_addr(self):
        addrs = self._selected_addrs()
        if addrs:
            self.root.clipboard_clear()
            self.root.clipboard_append(hex(addrs[0]))
            self.log("已复制地址 %s" % hex(addrs[0]))

    def on_refresh_display(self):
        self._render_candidates()

    # ---------- 保存为模板 (右键) ----------
    def on_save_template(self):
        if not self._require_pid():
            return
        addrs = self._selected_addrs()
        if not addrs:
            self.log("请先在列表中选中地址")
            return
        AddTemplateDialog(self.root, addrs[0], self.scanner, self._finish_save_template)

    def _finish_save_template(self, addr, tpl_name, target_name, vtype):
        """把当前地址及附近结构保存为用户模板 (存特征, 不存固定地址)"""
        size = TYPE_KEY[vtype][2]
        align = 8 if vtype in ("double", "int64") else 4
        fields = [{"name": target_name, "offset": 0x00, "type": vtype}]
        verifies = []
        # 扫描 +size..+0x60 区域 (跳过 anchor 字段本身: 用户下次输入的是新值,
        # 用旧值范围验证 anchor 会失败), 取最多 3 个"合理非零"字段做验证特征 (±50% 容差)
        found = 0
        off = size
        while off <= 0x60 and found < 3:
            v = self.scanner.read_value(addr + off, vtype)
            plausible = False
            if v is not None:
                if vtype in ("double", "float"):
                    plausible = abs(v) > 1e-9 and abs(v) < 1e15
                else:
                    plausible = abs(v) > 0 and abs(v) < 1e12
            if plausible:
                if v > 0:
                    lo, hi = v * 0.5, v * 2.0
                else:
                    lo, hi = v * 2.0, v * 0.5
                verifies.append({"offset": off, "type": vtype,
                                 "min": min(lo, hi), "max": max(lo, hi),
                                 "desc": "字段@0x%02X" % off})
                fields.append({"name": "字段@0x%02X" % off, "offset": off, "type": vtype})
                found += 1
            off += size
        if not verifies:
            self.log("警告: 地址附近 0x00..0x60 未找到非零字段, 模板缺少验证特征, "
                     "下次定位可能命中较多垃圾地址")
        tpl = {
            "name": tpl_name,
            "process": self.scanner.name or tpl_name,
            "locates": [{
                "label": "%s (%s)" % (target_name, vtype),
                "hint": "游戏界面当前 %s 数值" % target_name,
                "anchor": {"type": vtype, "anchor_offset": 0x00, "align": align,
                           "verify": verifies},
                "fields": fields,
            }],
        }
        tpls = load_user_templates()
        # 同名覆盖
        tpls = [t for t in tpls if t.get("name") != tpl_name]
        tpls.append(tpl)
        save_user_templates(tpls)
        # 刷新模板下拉框并切到新模板
        self.tpl_combo["values"] = [t["name"] for t in self.all_templates()]
        self.tpl_combo.set(tpl_name)
        self._tpl_changed()
        self.log("已保存模板 [%s]: 目标 %s (%s), 字段 %d 个, 验证特征 %d 个 -> %s" %
                 (tpl_name, target_name, vtype, len(fields), len(verifies),
                  os.path.basename(USER_TEMPLATES_PATH)))
        self.log("提示: 保存的是结构特征不是固定地址 (Unity 游戏地址每次启动/GC 都会变)。"
                 "下次直接在 [游戏模板] 页输入当前值重新定位即可。")

    # ---------- 模板 ----------
    def all_templates(self):
        """内置模板 + 用户自建模板"""
        return TEMPLATES + load_user_templates()

    def _tpl(self):
        return self.all_templates()[self.tpl_combo.current()]

    def _tpl_target(self):
        tpl = self._tpl()
        locates = tpl.get("locates") or []
        if not locates:
            return None
        idx = self.tpl_target.current()
        if idx < 0 or idx >= len(locates):
            idx = 0
            self.tpl_target.current(0)
        return locates[idx]

    def _tpl_changed(self):
        """模板或定位目标切换时: 重置定位结果并同步提示"""
        self.template_addr = None
        self.template_addrs = []
        self.tpl_status.configure(text="未定位", foreground="#c00")
        self.tpl_value.delete(0, "end")
        tpl = self._tpl()
        self.tpl_target["values"] = [l["label"] for l in (tpl.get("locates") or [])]
        if self.tpl_target["values"]:
            self.tpl_target.current(0)
        target = self._tpl_target()
        self.tpl_hint.configure(text=("提示: " + target["hint"]) if target else "")
        self._render_tpl_fields()

    def on_tpl_attach(self):
        tpl = self._tpl()
        pid = MemoryScanner.find_pid(tpl["process"])
        if pid:
            self._do_attach(pid, tpl["process"])
            self.cfg["last_process"] = tpl["process"]
            save_config(self.cfg)
        else:
            self.log("未找到进程 %s" % tpl["process"])

    def on_tpl_locate(self):
        if not self._require_pid():
            return
        target = self._tpl_target()
        if not target:
            self.log("该模板没有可定位的字段")
            return
        try:
            val = parse_number(self.tpl_value.get().strip())   # 支持 1K/1M/1B 缩写
        except Exception:
            self.log("请输入当前显示的数值 (支持 1K/1M/1B 缩写, 如 2.3M)")
            return
        self.tpl_status.configure(text="定位中 ...", foreground="#c80")
        # target 在主线程解析后传入, worker 禁止访问 tkinter 控件 (Tcl 非线程安全会死锁)
        threading.Thread(target=self._tpl_locate_worker, args=(target, val), daemon=True).start()

    def _tpl_locate_worker(self, target, val):
        try:
            anchor = target["anchor"]
            vtype = anchor["type"]
            self.scanner.scan_vtype = vtype
            results = self.scanner.scan_template(val, vtype, anchor.get("verify", []),
                                                 self._prog_cb, self.cancel_evt,
                                                 align=anchor.get("align", 1))
        except Exception as e:
            self.msg_queue.put(("error", "定位出错: %s" % e))
            return
        self.msg_queue.put(("tpl_done", results))

    def _render_tpl_fields(self):
        self.tpl_tree.delete(*self.tpl_tree.get_children())
        self.tpl_field["values"] = []
        if not self.template_addr:
            return
        target = self._tpl_target()
        if not target:
            return
        fields = target.get("fields", [])
        self.tpl_field["values"] = [fd["name"] for fd in fields]
        if fields:
            self.tpl_field.current(0)
        for fd in fields:
            v = self.scanner.read_value(self.template_addr + fd["offset"], fd["type"])
            if v is None:
                vs = "?"
            else:
                vs = fmt_value(fd["type"], pack_value(fd["type"], v))
                if isinstance(v, float) and abs(v) >= 1e6:
                    vs += "  (%s)" % fmt_abbrev(v)
            self.tpl_tree.insert("", "end",
                                 values=(fd["name"], "+0x%02X" % fd["offset"], vs, fd["type"]))

    def on_tpl_refresh(self):
        self._render_tpl_fields()

    def _tpl_field_sel(self):
        target = self._tpl_target()
        if not target:
            return None
        name = self.tpl_field.get()
        for fd in target.get("fields", []):
            if fd["name"] == name:
                return fd
        return None

    def on_tpl_write(self):
        if not self.template_addr:
            self.log("请先自动定位")
            return
        fd = self._tpl_field_sel()
        if not fd:
            self.log("请选择字段")
            return
        try:
            val = self.parse_val(self.tpl_newval.get(), fd["type"])
        except Exception as e:
            self.log("数值格式错误: %s" % e)
            return
        n_ok = 0
        for base in self.template_addrs or [self.template_addr]:
            addr = base + fd["offset"]
            if self.scanner.write_value(addr, fd["type"], val):
                n_ok += 1
        if n_ok:
            self.log("已写入 %s -> %s (%d/%d 处)" % (fd["name"], val, n_ok,
                                                   len(self.template_addrs or [1])))
            self._render_tpl_fields()
        else:
            self.log("写入失败, 错误码 %d" % ctypes.get_last_error())

    def on_tpl_lock(self):
        if not self.template_addr:
            self.log("请先自动定位")
            return
        fd = self._tpl_field_sel()
        if not fd:
            self.log("请选择字段")
            return
        try:
            val = self.parse_val(self.tpl_newval.get(), fd["type"])
        except Exception as e:
            self.log("数值格式错误: %s" % e)
            return
        targets = [(base + fd["offset"], pack_value(fd["type"], val))
                   for base in (self.template_addrs or [self.template_addr])]
        self._start_lock(targets)

    # ---------- 手动 ----------
    def _parse_addr(self):
        text = self.man_addr.get().strip()
        if text.lower().startswith("0x"):
            text = text[2:]
        return int(text, 16)

    def on_man_read(self):
        if not self._require_pid():
            return
        try:
            addr = self._parse_addr()
        except Exception:
            self.log("地址格式错误")
            return
        vtype = self.get_vtype(self.man_type)
        v = self.scanner.read_value(addr, vtype)
        if v is None:
            self.log("读取失败 (0x%X)" % addr)
            return
        self.man_value.delete(0, "end")
        self.man_value.insert(0, ("%.6g" % v) if vtype in ("double", "float") else str(v))
        self.log("读取 0x%X (%s) = %s" % (addr, vtype, v))

    def on_man_write(self):
        if not self._require_pid():
            return
        try:
            addr = self._parse_addr()
        except Exception:
            self.log("地址格式错误")
            return
        vtype = self.get_vtype(self.man_type)
        try:
            val = self.parse_val(self.man_value.get(), vtype)
        except Exception as e:
            self.log("数值格式错误: %s" % e)
            return
        if self.scanner.write_value(addr, vtype, val):
            self.log("已写入 0x%X (%s) = %s" % (addr, vtype, val))
        else:
            self.log("写入失败, 错误码 %d" % ctypes.get_last_error())

    # ---------- 消息泵 ----------
    def _poll_queue(self):
        try:
            while True:
                kind, payload = self.msg_queue.get_nowait()
                try:
                    if kind == "scan_done":
                        n, action = payload
                        self.progress["value"] = 100
                        self._render_candidates()
                        names = {"first": "首次扫描", "equal": "等于新值", "changed": "已变化",
                                 "unchanged": "未变化", "increased": "增大了", "decreased": "减小了"}
                        self.log("[%s] 完成, 候选 %d 个" % (names.get(action, "?"), n))
                    elif kind == "progress":
                        self.progress["value"] = payload
                    elif kind == "error":
                        self.log(payload)
                        self.progress["value"] = 0
                    elif kind == "auto_done":
                        v, results, rec = payload
                        self.progress["value"] = 100
                        lines = []
                        for t in ("double", "float", "int64", "int32"):
                            n, tr = results[t]
                            lines.append("%s=%s%s" % (t, "5万+" if tr else n, "截断" if tr else ""))
                        self.log("类型探测: " + " | ".join(lines))
                        self.log("推荐类型: %s (若候选太多, 回游戏改数值后点[已变化]/[等于新值] 多轮过滤)" % rec)
                        # 应用推荐: 类型下拉 + 候选
                        idx = next((i for i, t in enumerate(TYPE_LABELS) if rec in t), 0)
                        self.scan_type.current(idx)
                        self.scanner.scan_vtype = rec
                        scan_v = v if rec in ("double", "float") else int(v)
                        hits = self.scanner.scan_exact(scan_v, rec, full=True)
                        self.scanner.candidates = hits
                        self._render_candidates()
                        self.log("已按 %s 类型载入候选 %d 个, 可直接过滤/多轮收敛" % (rec, len(hits)))
                    elif kind == "tpl_done":
                        self.progress["value"] = 100
                        if payload:
                            # anchor 命中位置回退到结构基址 (anchor_offset)
                            off = self._tpl_target()["anchor"].get("anchor_offset", 0)
                            self.template_addrs = [a - off for a in payload]
                            self.template_addr = self.template_addrs[0]
                            self.tpl_status.configure(
                                text="已定位 0x%X (共 %d 处)" % (self.template_addr, len(payload)),
                                foreground="#080")
                            self.log("模板定位成功: 结构基址 0x%X (%d 处, 含镜像副本)" %
                                     (self.template_addr, len(payload)))
                            self._render_tpl_fields()
                        else:
                            self.template_addr = None
                            self.tpl_status.configure(text="未找到, 请确认当前值", foreground="#c00")
                            self.log("模板定位失败: 未找到匹配地址")
                except Exception as e:
                    # 单条消息处理异常不杀死消息泵, 记录后继续
                    self.log("消息处理异常: %s" % e)
        except queue.Empty:
            pass
        self.root.after(100, self._poll_queue)

    def on_close(self):
        self.on_unlock()
        self.cancel_evt.set()
        self.scanner.detach()
        self.root.destroy()


def main():
    root = tk.Tk()
    app = GameModderGUI(root)
    root.protocol("WM_DELETE_WINDOW", app.on_close)
    root.mainloop()


if __name__ == "__main__":
    main()
