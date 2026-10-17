"""tray_probe.py - 托盘图标探针(测试辅助)。

在自己进程里注册一个托盘图标(模拟真实应用),把 PID/hWnd/uID 写到
probe_state.json,然后跑消息循环。收到 TaskbarCreated 时会按文档
推荐的做法重新注册图标 —— 这正是恢复流程依赖的行为。

用法:
    python tray_probe.py start     # 启动探针
    python tray_probe.py stop      # 停止探针(PostQuitMessage)
"""
from __future__ import annotations

import ctypes
import json
import sys
import time
from ctypes import wintypes
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from windowhide.core.win32 import (
    user32, CreateWindowExW, DefWindowProcW, RegisterClassW, GetMessageW,
    TranslateMessage, DispatchMessageW, PostQuitMessage, DestroyWindow,
    GetModuleHandleW, RegisterWindowMessageW, PostMessageW,
    WM_DESTROY, HWND_MESSAGE,
)
from windowhide.core.trayhide import (
    NOTIFYICONDATAW, Shell_NotifyIconW, GUID,
    NIM_ADD, NIM_DELETE, NIF_MESSAGE, NIF_ICON, NIF_TIP,
)

STATE_FILE = str(Path(__file__).resolve().parent / "probe_state.json")
WM_APP = 0x8000
PROBE_ICON_ID = 42
state: dict = {}


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

hwnd_ref = {"value": 0}
taskbar_created = {"id": 0}


def add_icon() -> None:
    hicon = user32.LoadIconW(None, 32512) or 0  # IDI_APPLICATION
    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    nid.hWnd = wintypes.HWND(hwnd_ref["value"])
    nid.uID = PROBE_ICON_ID
    nid.uFlags = NIF_MESSAGE | NIF_ICON | NIF_TIP
    nid.uCallbackMessage = WM_APP + 1
    nid.hIcon = hicon if hicon else 0
    nid.szTip = "WH-Probe-TrayIcon"
    Shell_NotifyIconW(NIM_ADD, ctypes.byref(nid))


def delete_icon() -> None:
    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    nid.hWnd = wintypes.HWND(hwnd_ref["value"])
    nid.uID = PROBE_ICON_ID
    Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid))


def wndproc(hwnd, umsg, wparam, lparam):
    if umsg == taskbar_created["id"] and taskbar_created["id"]:
        add_icon()   # 按文档推荐的做法,任务栏重建时重新注册
        state["taskbar_created"] = state.get("taskbar_created", 0) + 1
        try:
            with open(STATE_FILE, "w", encoding="utf-8") as f:
                json.dump(state, f)
        except OSError:
            pass
        return 0
    if umsg == WM_DESTROY:
        delete_icon()
        return 0
    return DefWindowProcW(hwnd, umsg, wparam, lparam)


def run() -> None:
    global state
    hinstance = GetModuleHandleW(None)
    wndproc_ref = WNDPROC(wndproc)
    wc = WNDCLASS()
    wc.lpfnWndProc = ctypes.cast(wndproc_ref, ctypes.c_void_p)
    wc.hInstance = hinstance
    wc.lpszClassName = "WHTrayProbeWnd"
    RegisterClassW(ctypes.byref(wc))
    hwnd = CreateWindowExW(0, "WHTrayProbeWnd", "WH-Tray-Probe", 0,
                           0, 0, 0, 0, wintypes.HWND(HWND_MESSAGE),
                           None, hinstance, None)
    hwnd_ref["value"] = hwnd
    taskbar_created["id"] = RegisterWindowMessageW("TaskbarCreated")

    add_icon()
    pid = ctypes.windll.kernel32.GetCurrentProcessId()
    state = {"pid": pid, "hwnd": hwnd, "uid": PROBE_ICON_ID,
             "taskbar_created": 0, "started": time.time()}
    with open(STATE_FILE, "w", encoding="utf-8") as f:
        json.dump(state, f)

    msg = MSG()
    while True:
        r = GetMessageW(ctypes.byref(msg), None, 0, 0)
        if r == 0 or r == -1:
            break
        TranslateMessage(ctypes.byref(msg))
        DispatchMessageW(ctypes.byref(msg))
    DestroyWindow(hwnd)


if __name__ == "__main__":
    run()
