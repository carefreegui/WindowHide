"""trayhide.py - 系统托盘图标的跨进程移除与恢复。

原理:
  移除 —— 对目标进程的每个顶层窗口,用 Shell_NotifyIcon(NIM_DELETE) 遍历
          uID 0..MAX_UID 尝试删除其注册的托盘图标(Shell_NotifyIcon 内部
          会向 explorer 的托盘工具栏发 WM_COPYDATA,不校验发送方进程)。
  恢复 —— 向目标进程窗口广播 "TaskbarCreated" 自定义消息,应用会按文档
          推荐的做法重新注册自己的托盘图标。

注意:以 GUID 方式注册图标的应用(新版 Electron/部分国产软件)无法
从外部得知 GUID,只能做到 uID 方式的移除;此类图标会保留,但不影响
窗口本身的隐藏。
"""
from __future__ import annotations

import ctypes
import uuid
from ctypes import wintypes

from .win32 import (
    PostMessageW, RegisterWindowMessageW,
    FindWindowW, FindWindowExW, user32, shell32,
    enum_process_windows_all,
)

NIM_ADD = 0x00000000
NIM_MODIFY = 0x00000001
NIM_DELETE = 0x00000002
NIM_SETFOCUS = 0x00000003
NIM_SETVERSION = 0x00000004

NIF_MESSAGE = 0x00000001
NIF_ICON = 0x00000002
NIF_TIP = 0x00000004
NIF_STATE = 0x00000008
NIF_INFO = 0x00000010
NIF_GUID = 0x00000020
NIF_REALTIME = 0x00000040
NIF_SHOWTIP = 0x00000080

TB_BUTTONCOUNT = 0x0418  # WM_USER + 24

MAX_UID = 64  # 遍历 uID 的上限,覆盖绝大多数应用


class GUID(ctypes.Structure):
    _fields_ = [
        ("Data1", wintypes.DWORD),
        ("Data2", wintypes.WORD),
        ("Data3", wintypes.WORD),
        ("Data4", ctypes.c_byte * 8),
    ]

    @classmethod
    def from_uuid(cls, u: uuid.UUID) -> "GUID":
        g = cls()
        g.Data1 = u.time_low
        g.Data2 = u.time_mid
        g.Data3 = u.time_hi_version
        b = u.bytes[8:]
        g.Data4 = (ctypes.c_byte * 8)(*b)
        return g

    @classmethod
    def zero(cls) -> "GUID":
        g = cls()
        ctypes.memset(ctypes.byref(g), 0, ctypes.sizeof(g))
        return g


class NOTIFYICONDATAW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("hWnd", wintypes.HWND),
        ("uID", wintypes.UINT),
        ("uFlags", wintypes.UINT),
        ("uCallbackMessage", wintypes.UINT),
        ("hIcon", wintypes.HICON),
        ("szTip", wintypes.WCHAR * 128),
        ("dwState", wintypes.DWORD),
        ("dwStateFlags", wintypes.DWORD),
        ("szInfo", wintypes.WCHAR * 256),
        ("uTimeoutOrVersion", wintypes.UINT),
        ("szInfoTitle", wintypes.WCHAR * 64),
        ("dwInfoFlags", wintypes.DWORD),
        ("guidItem", GUID),
        ("hBalloonIcon", wintypes.HICON),
    ]


Shell_NotifyIconW = shell32.Shell_NotifyIconW
Shell_NotifyIconW.argtypes = [wintypes.DWORD, ctypes.POINTER(NOTIFYICONDATAW)]
Shell_NotifyIconW.restype = wintypes.BOOL


def _find_tray_toolbars() -> list[int]:
    """收集任务栏通知区域(含溢出区)里的 ToolbarWindow32。

    仅用于兼容 Win10 及更早版本;Win11 的通知区域已改为 XAML 宿主
    (SystemTray_Main),没有 ToolbarWindow32,此时返回空列表。
    """
    toolbars: list[int] = []

    def _children(parent: int, cls: str | None) -> list[int]:
        out = []
        child = 0
        while True:
            child = FindWindowExW(parent, child, cls, None)
            if not child:
                break
            out.append(child)
        return out

    roots = []
    shell_tray = FindWindowW("Shell_TrayWnd", None)
    if shell_tray:
        roots.append(shell_tray)
    overflow = FindWindowW("NotifyIconOverflowWindow", None)
    if overflow:
        roots.append(overflow)

    for root in roots:
        stack = [root]
        seen = set()
        while stack:
            w = stack.pop()
            if w in seen:
                continue
            seen.add(w)
            for c in _children(w, "ToolbarWindow32"):
                toolbars.append(c)
            stack.extend(_children(w, None))
    return toolbars


def _delete_icon(hwnd: int, u_id: int) -> bool:
    nid = NOTIFYICONDATAW()
    nid.cbSize = ctypes.sizeof(NOTIFYICONDATAW)
    nid.hWnd = wintypes.HWND(hwnd)
    nid.uID = u_id
    nid.uFlags = 0
    nid.guidItem = GUID.zero()
    return bool(Shell_NotifyIconW(NIM_DELETE, ctypes.byref(nid)))


def delete_process_tray_icons(pid: int) -> list[tuple[int, int]]:
    """删除指定进程注册的所有托盘图标。

    返回 [(hwnd, uID), ...] 成功删除的清单(Shell_NotifyIcon 返回
    FALSE 表示该 (hwnd, uID) 没有注册过图标)。
    """
    deleted: list[tuple[int, int]] = []
    for win in enum_process_windows_all(pid):
        for u_id in range(MAX_UID + 1):
            if _delete_icon(win.hwnd, u_id):
                deleted.append((win.hwnd, u_id))
    return deleted


def request_tray_icon_restore(pid: int) -> None:
    """广播 TaskbarCreated,让应用重新注册托盘图标。"""
    msg = RegisterWindowMessageW("TaskbarCreated")
    if not msg:
        return
    for win in enum_process_windows_all(pid):
        PostMessageW(win.hwnd, msg, 0, 0)


def self_test() -> bool:
    """自检:在本进程注册一个托盘图标再删除,验证 Shell_NotifyIcon 通路。

    返回 True 表示 API 工作正常(NIM_DELETE 对已存在的图标返回 TRUE,
    对不存在的返回 FALSE)。
    """
    import ctypes as _ctypes
    from .win32 import (
        CreateWindowExW, DefWindowProcW, RegisterClassW, DestroyWindow,
        GetModuleHandleW, HWND_MESSAGE, user32,
    )
    from ctypes import wintypes as _wt

    class _WC(_ctypes.Structure):
        _fields_ = [("style", _wt.UINT), ("lpfnWndProc", _ctypes.c_void_p),
                    ("cbClsExtra", _ctypes.c_int), ("cbWndExtra", _ctypes.c_int),
                    ("hInstance", _wt.HINSTANCE), ("hIcon", _wt.HICON),
                    ("hCursor", _wt.HANDLE), ("hbrBackground", _wt.HBRUSH),
                    ("lpszMenuName", _wt.LPCWSTR), ("lpszClassName", _wt.LPCWSTR)]

    wndproc_type = _ctypes.WINFUNCTYPE(_ctypes.c_ssize_t, _wt.HWND,
                                       _wt.UINT, _wt.WPARAM, _wt.LPARAM)

    @wndproc_type
    def _wp(hwnd, msg, wparam, lparam):
        return DefWindowProcW(hwnd, msg, wparam, lparam)

    hinstance = GetModuleHandleW(None)
    wc = _WC()
    wc.lpfnWndProc = _ctypes.cast(_wp, _ctypes.c_void_p)
    wc.hInstance = hinstance
    wc.lpszClassName = "WHTraySelfTestWnd"
    if not RegisterClassW(_ctypes.byref(wc)):
        return False
    hwnd = CreateWindowExW(0, "WHTraySelfTestWnd", None, 0, 0, 0, 0, 0,
                           _wt.HWND(HWND_MESSAGE), None, hinstance, None)
    if not hwnd:
        return False
    try:
        test_uid = 0x7FEE
        # 先确保没有残留
        _delete_icon(hwnd, test_uid)
        # 注册
        nid = NOTIFYICONDATAW()
        nid.cbSize = _ctypes.sizeof(NOTIFYICONDATAW)
        nid.hWnd = _wt.HWND(hwnd)
        nid.uID = test_uid
        nid.uFlags = NIF_ICON | NIF_TIP
        nid.szTip = "WH-SelfTest"
        if not Shell_NotifyIconW(NIM_ADD, _ctypes.byref(nid)):
            return False
        # 删除应成功
        ok_del = _delete_icon(hwnd, test_uid)
        # 再删一次应失败
        ok_del2 = not _delete_icon(hwnd, test_uid)
        return bool(ok_del and ok_del2)
    finally:
        DestroyWindow(hwnd)
