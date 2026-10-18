"""win32.py - Windows API 的 ctypes 封装。

只做"读窗口信息"和"改窗口显示状态"两类事,不向目标程序发任何输入事件。
"""
from __future__ import annotations

import ctypes
from ctypes import wintypes
from dataclasses import dataclass

user32 = ctypes.windll.user32
kernel32 = ctypes.windll.kernel32
shell32 = ctypes.windll.shell32

# ---------------------------------------------------------------- 常量
GW_OWNER = 4
GWLP_HWNDPARENT = -6
GWL_STYLE = -16
GWL_EXSTYLE = -20
GCLP_HICON = -14
GCLP_HICONSM = -34

WS_VISIBLE = 0x10000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_APPWINDOW = 0x00040000
WS_EX_TOPMOST = 0x00000008

SW_HIDE = 0
SW_SHOWNORMAL = 1
SW_SHOWMINIMIZED = 2
SW_SHOWMAXIMIZED = 3
SW_SHOWNOACTIVATE = 4
SW_SHOW = 5
SW_MINIMIZE = 6
SW_SHOWMINNOACTIVE = 7
SW_SHOWNA = 8
SW_RESTORE = 9
SW_MAX = 11

WM_GETICON = 0x007F
WM_HOTKEY = 0x0312
WM_COPYDATA = 0x004A
WM_QUIT = 0x0012
WM_DESTROY = 0x0002
WM_APP = 0x8000
ICON_SMALL = 0
ICON_BIG = 1
ICON_SMALL2 = 2

HWND_MESSAGE = -3

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
PROCESS_VM_READ = 0x0010
STILL_ACTIVE = 259

ERROR_ACCESS_DENIED = 5

TH32CS_SNAPPROCESS = 0x00000002
MAX_PATH = 260


class PROCESSENTRY32W(ctypes.Structure):
    """toolhelp 进程快照项(64 位布局;本项目只支持 64 位 Windows)。"""
    _fields_ = [
        ("dwSize", wintypes.DWORD),
        ("cntUsage", wintypes.DWORD),
        ("th32ProcessID", wintypes.DWORD),
        ("th32DefaultHeapID", ctypes.c_ulonglong),   # ULONG_PTR
        ("th32ModuleID", wintypes.DWORD),
        ("cntThreads", wintypes.DWORD),
        ("th32ParentProcessID", wintypes.DWORD),
        ("cx", wintypes.LONG),
        ("dwFlags", wintypes.DWORD),
        ("szExeFile", wintypes.WCHAR * MAX_PATH),
    ]


CreateToolhelp32Snapshot = kernel32.CreateToolhelp32Snapshot
CreateToolhelp32Snapshot.argtypes = [wintypes.DWORD, wintypes.DWORD]
CreateToolhelp32Snapshot.restype = wintypes.HANDLE

Process32FirstW = kernel32.Process32FirstW
Process32FirstW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
Process32FirstW.restype = wintypes.BOOL

Process32NextW = kernel32.Process32NextW
Process32NextW.argtypes = [wintypes.HANDLE, ctypes.POINTER(PROCESSENTRY32W)]
Process32NextW.restype = wintypes.BOOL

# ---------------------------------------------------------------- 函数原型
WNDENUMPROC = ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)

EnumWindows = user32.EnumWindows
EnumWindows.argtypes = [WNDENUMPROC, wintypes.LPARAM]
EnumWindows.restype = wintypes.BOOL

GetWindowTextW = user32.GetWindowTextW
GetWindowTextW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
GetWindowTextW.restype = ctypes.c_int

GetClassNameW = user32.GetClassNameW
GetClassNameW.argtypes = [wintypes.HWND, wintypes.LPWSTR, ctypes.c_int]
GetClassNameW.restype = ctypes.c_int

GetWindowThreadProcessId = user32.GetWindowThreadProcessId
GetWindowThreadProcessId.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.DWORD)]
GetWindowThreadProcessId.restype = wintypes.DWORD

IsWindowVisible = user32.IsWindowVisible
IsWindowVisible.argtypes = [wintypes.HWND]
IsWindowVisible.restype = wintypes.BOOL

IsWindow = user32.IsWindow
IsWindow.argtypes = [wintypes.HWND]
IsWindow.restype = wintypes.BOOL

IsIconic = user32.IsIconic
IsIconic.argtypes = [wintypes.HWND]
IsIconic.restype = wintypes.BOOL

GetWindow = user32.GetWindow
GetWindow.argtypes = [wintypes.HWND, wintypes.UINT]
GetWindow.restype = wintypes.HWND

GetParent = user32.GetParent
GetParent.argtypes = [wintypes.HWND]
GetParent.restype = wintypes.HWND

GetWindowLongPtrW = getattr(user32, "GetWindowLongPtrW", None) or user32.GetWindowLongW
GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
GetWindowLongPtrW.restype = ctypes.c_ssize_t

SetWindowLongPtrW = getattr(user32, "SetWindowLongPtrW", None) or user32.SetWindowLongW
SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_ssize_t]
SetWindowLongPtrW.restype = ctypes.c_ssize_t

ShowWindow = user32.ShowWindow
ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
ShowWindow.restype = wintypes.BOOL

SetForegroundWindow = user32.SetForegroundWindow
SetForegroundWindow.argtypes = [wintypes.HWND]
SetForegroundWindow.restype = wintypes.BOOL

GetForegroundWindow = user32.GetForegroundWindow
GetForegroundWindow.argtypes = []
GetForegroundWindow.restype = wintypes.HWND

FindWindowW = user32.FindWindowW
FindWindowW.argtypes = [wintypes.LPCWSTR, wintypes.LPCWSTR]
FindWindowW.restype = wintypes.HWND

FindWindowExW = user32.FindWindowExW
FindWindowExW.argtypes = [wintypes.HWND, wintypes.HWND, wintypes.LPCWSTR, wintypes.LPCWSTR]
FindWindowExW.restype = wintypes.HWND

EnumChildWindows = user32.EnumChildWindows
EnumChildWindows.argtypes = [wintypes.HWND, WNDENUMPROC, wintypes.LPARAM]
EnumChildWindows.restype = wintypes.BOOL

SendMessageW = user32.SendMessageW
SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
SendMessageW.restype = wintypes.LPARAM

# 带超时的发送:目标线程无响应时最多等 timeout 毫秒,不会无限期阻塞调用方
SendMessageTimeoutW = user32.SendMessageTimeoutW
SendMessageTimeoutW.argtypes = [
    wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM,
    wintypes.UINT, wintypes.UINT, ctypes.POINTER(wintypes.ULONG),
]
SendMessageTimeoutW.restype = wintypes.LPARAM

WM_GETTEXT = 0x000D
SMTO_ABORTIFHUNG = 0x0002
SEND_MSG_TIMEOUT_MS = 100

PostMessageW = user32.PostMessageW
PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
PostMessageW.restype = wintypes.BOOL

RegisterWindowMessageW = user32.RegisterWindowMessageW
RegisterWindowMessageW.argtypes = [wintypes.LPCWSTR]
RegisterWindowMessageW.restype = wintypes.UINT

RegisterHotKey = user32.RegisterHotKey
RegisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int, wintypes.UINT, wintypes.UINT]
RegisterHotKey.restype = wintypes.BOOL

UnregisterHotKey = user32.UnregisterHotKey
UnregisterHotKey.argtypes = [wintypes.HWND, ctypes.c_int]
UnregisterHotKey.restype = wintypes.BOOL

GetMessageW = user32.GetMessageW
GetMessageW.argtypes = [ctypes.c_void_p, wintypes.HWND, wintypes.UINT, wintypes.UINT]
GetMessageW.restype = wintypes.BOOL

TranslateMessage = user32.TranslateMessage
TranslateMessage.argtypes = [ctypes.c_void_p]
TranslateMessage.restype = wintypes.BOOL

DispatchMessageW = user32.DispatchMessageW
DispatchMessageW.argtypes = [ctypes.c_void_p]
DispatchMessageW.restype = ctypes.c_ssize_t

DefWindowProcW = user32.DefWindowProcW
DefWindowProcW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
DefWindowProcW.restype = ctypes.c_ssize_t

RegisterClassW = user32.RegisterClassW
RegisterClassW.argtypes = [ctypes.c_void_p]
RegisterClassW.restype = wintypes.ATOM

UnregisterClassW = user32.UnregisterClassW
UnregisterClassW.argtypes = [wintypes.LPCWSTR, wintypes.HINSTANCE]
UnregisterClassW.restype = wintypes.BOOL

CreateWindowExW = user32.CreateWindowExW
CreateWindowExW.argtypes = [
    wintypes.DWORD, wintypes.LPCWSTR, wintypes.LPCWSTR, wintypes.DWORD,
    ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int,
    wintypes.HWND, wintypes.HMENU, wintypes.HINSTANCE, wintypes.LPVOID,
]
CreateWindowExW.restype = wintypes.HWND

DestroyWindow = user32.DestroyWindow
DestroyWindow.argtypes = [wintypes.HWND]
DestroyWindow.restype = wintypes.BOOL

PostQuitMessage = user32.PostQuitMessage
PostQuitMessage.argtypes = [ctypes.c_int]
PostQuitMessage.restype = None

GetModuleHandleW = kernel32.GetModuleHandleW
GetModuleHandleW.argtypes = [wintypes.LPCWSTR]
GetModuleHandleW.restype = wintypes.HINSTANCE

OpenProcess = kernel32.OpenProcess
OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
OpenProcess.restype = wintypes.HANDLE

CloseHandle = kernel32.CloseHandle
CloseHandle.argtypes = [wintypes.HANDLE]
CloseHandle.restype = wintypes.BOOL

GetExitCodeProcess = kernel32.GetExitCodeProcess
GetExitCodeProcess.argtypes = [wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD)]
GetExitCodeProcess.restype = wintypes.BOOL

QueryFullProcessImageNameW = kernel32.QueryFullProcessImageNameW
QueryFullProcessImageNameW.argtypes = [
    wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD)
]
QueryFullProcessImageNameW.restype = wintypes.BOOL

GetLastError = kernel32.GetLastError
GetLastError.argtypes = []
GetLastError.restype = wintypes.DWORD


class POINT(ctypes.Structure):
    _fields_ = [("x", ctypes.c_long), ("y", ctypes.c_long)]


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long),
                ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


GetWindowRect = user32.GetWindowRect
GetWindowRect.argtypes = [wintypes.HWND, ctypes.POINTER(RECT)]
GetWindowRect.restype = wintypes.BOOL


class WINDOWPLACEMENT(ctypes.Structure):
    _fields_ = [
        ("length", wintypes.UINT),
        ("flags", wintypes.UINT),
        ("showCmd", wintypes.UINT),
        ("ptMinPosition", POINT),
        ("ptMaxPosition", POINT),
        ("rcNormalPosition", RECT),
    ]


GetWindowPlacement = user32.GetWindowPlacement
GetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
GetWindowPlacement.restype = wintypes.BOOL


# ---------------------------------------------------------------- 数据结构
@dataclass(slots=True)
class WindowInfo:
    """一个顶层窗口的快照。"""
    hwnd: int
    pid: int
    title: str
    class_name: str
    visible: bool
    minimized: bool
    ex_style: int
    style: int
    process_name: str = ""
    process_path: str = ""
    rect: tuple[int, int, int, int] = (0, 0, 0, 0)  # left, top, right, bottom

    @property
    def is_toolwindow(self) -> bool:
        return bool(self.ex_style & WS_EX_TOOLWINDOW)

    @property
    def size(self) -> int:
        l, t, r, b = self.rect
        return max(0, r - l) * max(0, b - t)


# ---------------------------------------------------------------- 基础函数
def get_window_text(hwnd: int) -> str:
    """取窗口标题。

    必须用带超时的 SendMessageTimeoutW 而不是 GetWindowTextW:
    GetWindowTextW 内部向目标窗口所在线程发 WM_GETTEXT,遇到无响应的
    进程会无限期阻塞调用方(曾导致隐藏操作整个卡死)。
    """
    buf = ctypes.create_unicode_buffer(512)
    result = wintypes.ULONG(0)
    n = SendMessageTimeoutW(hwnd, WM_GETTEXT, 512,
                            ctypes.cast(buf, ctypes.c_void_p).value,
                            SMTO_ABORTIFHUNG, SEND_MSG_TIMEOUT_MS,
                            ctypes.byref(result))
    return buf.value if n > 0 else ""


def get_class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    n = GetClassNameW(hwnd, buf, 256)
    return buf.value if n > 0 else ""


def get_window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD(0)
    GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


def get_window_styles(hwnd: int) -> tuple[int, int]:
    return GetWindowLongPtrW(hwnd, GWL_STYLE), GetWindowLongPtrW(hwnd, GWL_EXSTYLE)


def get_window_placement_cmd(hwnd: int) -> int:
    wp = WINDOWPLACEMENT()
    wp.length = ctypes.sizeof(WINDOWPLACEMENT)
    if GetWindowPlacement(hwnd, ctypes.byref(wp)):
        return int(wp.showCmd)
    return SW_SHOWNORMAL


def get_window_rect(hwnd: int) -> tuple[int, int, int, int]:
    r = RECT()
    # GetWindowRect 用 int 指针即可
    if user32.GetWindowRect(hwnd, ctypes.byref(r)):
        return (r.left, r.top, r.right, r.bottom)
    return (0, 0, 0, 0)


def process_image_path(pid: int) -> str:
    """取进程可执行文件完整路径;失败返回空串。"""
    h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        CloseHandle(h)


def process_is_alive(pid: int) -> bool:
    h = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return False
    try:
        code = wintypes.DWORD(0)
        if GetExitCodeProcess(h, ctypes.byref(code)):
            return code.value == STILL_ACTIVE
        return False
    finally:
        CloseHandle(h)


def process_descendants(pid: int) -> set[int]:
    """返回 pid 的所有子孙进程 id(不含自身)。

    用途:Chromium 系浏览器(Edge/Chrome)的音频不在主进程放,而在
    audio service 子进程放 —— 按主窗口 pid 静音找不到会话,必须把
    整个进程树都算上。快照失败时返回空集合(调用方按单进程处理)。
    """
    snap = CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snap in (0, -1) or snap == wintypes.HANDLE(-1).value:
        return set()
    children: dict[int, list[int]] = {}
    try:
        entry = PROCESSENTRY32W()
        entry.dwSize = ctypes.sizeof(PROCESSENTRY32W)
        ok = Process32FirstW(snap, ctypes.byref(entry))
        while ok:
            children.setdefault(int(entry.th32ParentProcessID), []).append(
                int(entry.th32ProcessID))
            ok = Process32NextW(snap, ctypes.byref(entry))
    finally:
        CloseHandle(snap)

    out: set[int] = set()
    queue = list(children.get(pid, ()))
    while queue:
        cur = queue.pop()
        if cur in out or cur == pid:
            continue
        out.add(cur)
        queue.extend(children.get(cur, ()))
    return out


def enum_top_level_windows(pid: int | None = None) -> list[WindowInfo]:
    """枚举所有顶层窗口(parent==0),包含隐藏窗口。

    pid 非 None 时只收集该进程的窗口 —— 在回调里就按 pid 过滤,
    避免对全系统窗口逐个取标题(既慢又会放大无响应窗口的影响面)。
    """
    result: list[WindowInfo] = []

    @WNDENUMPROC
    def _cb(hwnd, _lparam):
        if not IsWindow(hwnd):
            return True
        # 注意:GetParent 的 restype 是 HWND(c_void_p),无父窗口时返回 None
        if GetParent(hwnd):
            return True  # 只收顶层
        if pid is not None and get_window_pid(hwnd) != pid:
            return True
        try:
            style, ex_style = get_window_styles(hwnd)
            result.append(WindowInfo(
                hwnd=int(hwnd),
                pid=get_window_pid(hwnd),
                title=get_window_text(hwnd),
                class_name=get_class_name(hwnd),
                visible=bool(IsWindowVisible(hwnd)),
                minimized=bool(IsIconic(hwnd)),
                ex_style=ex_style,
                style=style,
                rect=get_window_rect(hwnd),
            ))
        except Exception:
            pass
        return True

    EnumWindows(_cb, 0)
    return result


def enum_process_windows(pid: int) -> list[WindowInfo]:
    """枚举指定进程的所有顶层窗口(含隐藏、无标题的托盘回调窗口)。"""
    return enum_top_level_windows(pid)


def enum_process_windows_all(pid: int) -> list[WindowInfo]:
    """枚举进程的全部窗口,包括 EnumWindows 看不到的消息窗口(HWND_MESSAGE)。

    托盘回调窗口经常是消息窗口(Shell_NotifyIcon 的 hWnd 就挂在它上面),
    删除/恢复托盘图标必须用这个全量枚举。消息窗口只能用
    FindWindowExW(HWND_MESSAGE, ...) 遍历到。
    """
    result: list[WindowInfo] = []
    seen: set[int] = set()

    def _collect(hwnd: int) -> None:
        if hwnd in seen or not IsWindow(hwnd):
            return
        if get_window_pid(hwnd) != pid:
            return  # 先按 pid 过滤,只对目标进程的窗口取标题等详情
        seen.add(hwnd)
        try:
            style, ex_style = get_window_styles(hwnd)
            result.append(WindowInfo(
                hwnd=int(hwnd),
                pid=get_window_pid(hwnd),
                title=get_window_text(hwnd),
                class_name=get_class_name(hwnd),
                visible=bool(IsWindowVisible(hwnd)),
                minimized=bool(IsIconic(hwnd)),
                ex_style=ex_style,
                style=style,
                rect=get_window_rect(hwnd),
            ))
        except Exception:
            pass

    @WNDENUMPROC
    def _cb_toplevel(hwnd, _lparam):
        if GetParent(hwnd):
            return True
        _collect(hwnd)
        return True

    EnumWindows(_cb_toplevel, 0)
    # 消息窗口:FindWindowExW(HWND_MESSAGE, childAfter, NULL, NULL) 逐个遍历
    child = 0
    while True:
        child = FindWindowExW(wintypes.HWND(HWND_MESSAGE),
                              wintypes.HWND(child) if child else None, None, None)
        if not child:
            break
        _collect(child)
    return [w for w in result if w.pid == pid]


# ---------------------------------------------------------------- 显示状态操作
def set_ex_style(hwnd: int, add: int = 0, remove: int = 0) -> int:
    """安全地增删窗口扩展样式,返回修改后的样式。"""
    old = GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    new = (old | add) & ~remove
    if new != old:
        SetWindowLongPtrW(hwnd, GWL_EXSTYLE, new)
    return new


def hide_window(hwnd: int) -> tuple[int, int]:
    """隐藏窗口:先加 WS_EX_TOOLWINDOW(移出任务栏/Alt+Tab),再 SW_HIDE。

    返回 (原扩展样式, 原显示命令),供恢复用。
    """
    ex_style = GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    show_cmd = get_window_placement_cmd(hwnd)
    set_ex_style(hwnd, add=WS_EX_TOOLWINDOW)
    ShowWindow(hwnd, SW_HIDE)
    return ex_style, show_cmd


def restore_window(hwnd: int, ex_style: int, show_cmd: int) -> None:
    """恢复窗口:还原扩展样式,再按记录的命令显示。"""
    set_ex_style(hwnd, remove=WS_EX_TOOLWINDOW, add=ex_style & WS_EX_TOOLWINDOW)
    cmd = show_cmd
    if cmd in (SW_HIDE, SW_SHOWMINNOACTIVE, SW_MINIMIZE):
        cmd = SW_SHOWNORMAL
    elif cmd == SW_SHOWMINIMIZED:
        cmd = SW_RESTORE  # 原来是最小化的,恢复成普通显示
    elif cmd in (SW_SHOWNOACTIVATE, SW_SHOWNA):
        cmd = SW_SHOWNOACTIVATE
    ShowWindow(hwnd, cmd)


def is_hidden(hwnd: int) -> bool:
    return not IsWindowVisible(hwnd)
