"""screenshot.py - 纯 ctypes 截屏并保存为 PNG(无第三方依赖)。

用法:
    python screenshot.py <输出.png>            # 全屏
    python screenshot.py <输出.png> <窗口标题>   # 只截指定窗口(PrintWindow)
"""
from __future__ import annotations

import ctypes
import struct
import sys
import zlib
from ctypes import wintypes


def _write_png(path: str, width: int, height: int, rgba: bytes) -> None:
    def chunk(tag: bytes, data: bytes) -> bytes:
        return (struct.pack(">I", len(data)) + tag + data
                + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF))

    ihdr = struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0)
    stride = width * 4
    rows = b"".join(b"\x00" + rgba[y * stride:(y + 1) * stride]
                    for y in range(height))
    png = (b"\x89PNG\r\n\x1a\n"
           + chunk(b"IHDR", ihdr)
           + chunk(b"IDAT", zlib.compress(rows, 6))
           + chunk(b"IEND", b""))
    with open(path, "wb") as f:
        f.write(png)


def _grab(hdc_src_getter, width: int, height: int) -> bytes:
    gdi32 = ctypes.windll.gdi32

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
            ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
            ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    hdc_src = hdc_src_getter()
    mem_dc = gdi32.CreateCompatibleDC(hdc_src)
    bmp = gdi32.CreateCompatibleBitmap(hdc_src, width, height)
    gdi32.SelectObject(mem_dc, bmp)
    gdi32.BitBlt(mem_dc, 0, 0, width, height, hdc_src, 0, 0, 0x00CC0020)

    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = width
    bmi.biHeight = -height
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0

    buf = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(mem_dc, bmp, 0, height, buf, ctypes.byref(bmi), 0)

    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(mem_dc)
    if hdc_src != ctypes.windll.user32.GetDC(None):
        ctypes.windll.user32.ReleaseDC(None, hdc_src)

    raw = bytearray(buf.raw)
    for i in range(0, len(raw), 4):
        raw[i], raw[i + 2] = raw[i + 2], raw[i]
    return bytes(raw)


def _make_dpi_aware() -> None:
    """声明 DPI 感知。

    本机缩放 150%:Qt 程序是 per-monitor-v2 感知的,而我们这个进程默认是
    不感知的 —— `GetWindowRect` 会返回"逻辑尺寸"(980x620),`PrintWindow`
    却按物理像素渲染(1470x930),结果截出来只剩左上角一块。
    必须在取矩形之前声明感知(只需一次,重复调用无副作用)。
    """
    try:
        ctypes.windll.user32.SetProcessDPIAware()
    except Exception:
        pass


def capture_screen(path: str) -> tuple[int, int]:
    _make_dpi_aware()
    user32 = ctypes.windll.user32
    width = user32.GetSystemMetrics(0)
    height = user32.GetSystemMetrics(1)
    screen_dc = user32.GetDC(None)
    rgba = _grab(lambda: screen_dc, width, height)
    _write_png(path, width, height, rgba)
    return width, height


def capture_window(path: str, title: str) -> tuple[int, int]:
    """用 PrintWindow 截取指定标题的窗口(可截被遮挡的窗口)。"""
    _make_dpi_aware()
    user32 = ctypes.windll.user32
    gdi32 = ctypes.windll.gdi32
    hwnd = user32.FindWindowW(None, title)
    if not hwnd:
        raise RuntimeError(f"找不到窗口: {title}")

    # 本工具在 core/win32.py 里给 GetWindowRect 声明过 argtypes(POINTER(它自己的
    # RECT)),ctypes 会按类型严格校验 —— 这里必须用同一个结构体,否则报
    # "expected LP_RECT instance instead of pointer to RECT"。
    try:
        from windowhide.core.win32 import RECT as _RECT
    except Exception:
        _RECT = wintypes.RECT

    rect = _RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    width, height = rect.right - rect.left, rect.bottom - rect.top
    if width <= 0 or height <= 0:
        raise RuntimeError("窗口大小为 0")

    hdc = gdi32.CreateCompatibleDC(None)
    bmp = gdi32.CreateCompatibleBitmap(user32.GetDC(None), width, height)
    gdi32.SelectObject(hdc, bmp)
    # PW_RENDERFULLCONTENT = 2,解决 DWM 合成内容截黑的问题
    ok = user32.PrintWindow(wintypes.HWND(hwnd), hdc, 2)
    if not ok:
        ok = user32.PrintWindow(wintypes.HWND(hwnd), hdc, 0)
    if not ok:
        raise RuntimeError("PrintWindow 失败")

    class BITMAPINFOHEADER(ctypes.Structure):
        _fields_ = [
            ("biSize", wintypes.DWORD), ("biWidth", ctypes.c_long),
            ("biHeight", ctypes.c_long), ("biPlanes", wintypes.WORD),
            ("biBitCount", wintypes.WORD), ("biCompression", wintypes.DWORD),
            ("biSizeImage", wintypes.DWORD), ("biXPelsPerMeter", ctypes.c_long),
            ("biYPelsPerMeter", ctypes.c_long), ("biClrUsed", wintypes.DWORD),
            ("biClrImportant", wintypes.DWORD),
        ]

    bmi = BITMAPINFOHEADER()
    bmi.biSize = ctypes.sizeof(BITMAPINFOHEADER)
    bmi.biWidth = width
    bmi.biHeight = -height
    bmi.biPlanes = 1
    bmi.biBitCount = 32
    bmi.biCompression = 0
    buf = ctypes.create_string_buffer(width * height * 4)
    gdi32.GetDIBits(hdc, bmp, 0, height, buf, ctypes.byref(bmi), 0)
    gdi32.DeleteObject(bmp)
    gdi32.DeleteDC(hdc)

    raw = bytearray(buf.raw)
    for i in range(0, len(raw), 4):
        raw[i], raw[i + 2] = raw[i + 2], raw[i]
    _write_png(path, width, height, bytes(raw))
    return width, height


if __name__ == "__main__":
    out = sys.argv[1] if len(sys.argv) > 1 else "screenshot.png"
    if len(sys.argv) > 2:
        w, h = capture_window(out, sys.argv[2])
    else:
        w, h = capture_screen(out)
    print(f"saved {out} ({w}x{h})")
