"""tests/hang_probe.py - 制造一个"无响应"或"极慢"的窗口。

配合 hang_test.py / hung_test.py 使用:

  默认        建好窗口后彻底不 pump 消息(45 秒)—— 任何向它发消息的
              调用方(GetWindowText / WM_GETICON)都会被拖住;
  --slow      消息循环每条消息睡 250ms —— 模拟"主线程很忙但没死"的
              程序,取标题会吃满 100ms 超时。用来验证"慢窗口缓存"。
"""
from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from windowhide.core.win32 import (  # noqa: E402
    CreateWindowExW, DefWindowProcW, GetModuleHandleW, RegisterClassW,
    WNDENUMPROC, user32,
)
import ctypes  # noqa: E402
from ctypes import wintypes  # noqa: E402


def main() -> None:
    class WC(ctypes.Structure):
        _fields_ = [("style", wintypes.UINT), ("lpfnWndProc", ctypes.c_void_p),
                    ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
                    ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
                    ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
                    ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR)]

    WP = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND,
                            wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

    @WP
    def wp(h, m, w, l):
        return DefWindowProcW(h, m, w, l)

    wc = WC()
    wc.lpfnWndProc = ctypes.cast(wp, ctypes.c_void_p)
    wc.hInstance = GetModuleHandleW(None)
    wc.lpszClassName = "WHHangProbeWnd"
    RegisterClassW(ctypes.byref(wc))

    hwnd = CreateWindowExW(0, "WHHangProbeWnd", "WH-Hang-Probe",
                           0x10CF0000,  # WS_OVERLAPPEDWINDOW
                           100, 100, 400, 300,
                           None, None, wc.hInstance, None)
    if not hwnd:
        print("创建窗口失败", flush=True)
        return
    user32.ShowWindow(hwnd, 1)
    print(f"hang window hwnd={hwnd}", flush=True)

    if "--slow" in sys.argv:
        # 慢速消息循环:每条消息前睡 250ms
        msg = wintypes.MSG()
        while True:
            while user32.PeekMessageW(ctypes.byref(msg), None, 0, 0, 1):
                user32.TranslateMessage(ctypes.byref(msg))
                user32.DispatchMessageW(ctypes.byref(msg))
            time.sleep(0.25)

    # 关键:不再 pumps 消息 —— 窗口进入"无响应"状态
    t0 = time.time()
    while time.time() - t0 < 45:
        time.sleep(0.5)


if __name__ == "__main__":
    main()
