"""dummy_app.py - 测试专用假目标程序。

创建一个带标题的可见窗口并跑消息循环,标题形如
"WH-Test-Target-<pid>",或者全屏版 "WH-Test-Fullscreen-<pid>"。
供 integration_test.py / fullscreen_test.py 启动、隐藏、恢复、关闭,
不会碰任何用户自己的程序。

--fullscreen 模式:无边框 + 置顶 + 铺满整块显示器,用来模拟全屏游戏
(注意:它只是几何意义上的全屏,不是 D3D 独占全屏,所以能验证"全屏
窗口走先最小化再隐藏"这条代码路径,但验证不了真实游戏中 DXGI 的
独占输出行为 —— 那部分只能靠真机实测)。
"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from windowhide.core.win32 import (
    user32, CreateWindowExW, DefWindowProcW, RegisterClassW, GetMessageW,
    TranslateMessage, DispatchMessageW, DestroyWindow, GetModuleHandleW,
    HWND_MESSAGE, WM_DESTROY,
    WS_EX_TOPMOST, WS_EX_APPWINDOW,
)


class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT), ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int), ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE), ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE), ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR), ("lpszClassName", wintypes.LPCWSTR),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND), ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM), ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD), ("pt", ctypes.c_long * 2),
    ]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND,
                             wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

WS_OVERLAPPEDWINDOW = 0x00CF0000
WS_VISIBLE = 0x10000000
WS_POPUP = 0x80000000
CW_USEDEFAULT = 0x80000000


def main() -> int:
    fullscreen = "--fullscreen" in sys.argv
    hinstance = GetModuleHandleW(None)

    @WNDPROC
    def wndproc(hwnd, umsg, wparam, lparam):
        if umsg == WM_DESTROY:
            return 0
        return DefWindowProcW(hwnd, umsg, wparam, lparam)

    wc = WNDCLASS()
    wc.style = 0
    wc.lpfnWndProc = ctypes.cast(wndproc, ctypes.c_void_p)
    wc.hInstance = hinstance
    wc.lpszClassName = "WHDummyAppWnd"
    wc.hbrBackground = 16  # COLOR_BACKGROUND
    RegisterClassW(ctypes.byref(wc))

    pid = ctypes.windll.kernel32.GetCurrentProcessId()
    if fullscreen:
        title = f"WH-Test-Fullscreen-{pid}"
        sw, sh = user32.GetSystemMetrics(0), user32.GetSystemMetrics(1)
        hwnd = CreateWindowExW(
            WS_EX_TOPMOST | WS_EX_APPWINDOW, "WHDummyAppWnd", title,
            WS_POPUP | WS_VISIBLE, 0, 0, sw, sh,
            None, None, hinstance, None)
    else:
        title = f"WH-Test-Target-{pid}"
        hwnd = CreateWindowExW(
            0, "WHDummyAppWnd", title, WS_OVERLAPPEDWINDOW | WS_VISIBLE,
            CW_USEDEFAULT, CW_USEDEFAULT, 480, 320,
            None, None, hinstance, None)
    if not hwnd:
        print("create window failed")
        return 1
    print(title, flush=True)

    msg = MSG()
    while True:
        r = GetMessageW(ctypes.byref(msg), None, 0, 0)
        if r == 0 or r == -1:
            break
        TranslateMessage(ctypes.byref(msg))
        DispatchMessageW(ctypes.byref(msg))
    DestroyWindow(hwnd)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
