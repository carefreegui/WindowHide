"""win32.py - Windows API 的 ctypes 封装。

只做"读窗口信息"和"改窗口显示状态"两类事,不向目标程序发任何输入事件。
"""
from __future__ import annotations

import ctypes
import os
import threading
import time
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
WS_CAPTION = 0x00C00000
WS_DISABLED = 0x02000000
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_LAYERED = 0x00080000
WS_EX_NOACTIVATE = 0x08000000
WS_EX_NOREDIRECTIONBITMAP = 0x00200000
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

# SetWindowPos 标志
SWP_NOSIZE = 0x0001
SWP_NOMOVE = 0x0002
SWP_NOZORDER = 0x0004
SWP_NOACTIVATE = 0x0010
SWP_SHOWWINDOW = 0x0040
SWP_HIDEWINDOW = 0x0080
SWP_STATECHANGED = 0x8000
# 目标窗口在别的线程时,把这次 SetWindowPos 投递到对方队列就返回,
# 不在这里等它处理。目标无响应时,同步版会一直等下去(实测把整个 UI 冻死)。
SWP_ASYNCWINDOWPOS = 0x4000

MONITOR_DEFAULTTONEAREST = 0x00000002

WM_GETICON = 0x007F
WM_SYSCOMMAND = 0x0112
SC_MINIMIZE = 0xF020
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

# Windows 自己的"未响应"判定(任务管理器里那个),比自建超时可靠
IsHungAppWindow = user32.IsHungAppWindow
IsHungAppWindow.argtypes = [wintypes.HWND]
IsHungAppWindow.restype = wintypes.BOOL

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

# 异步版:把显示状态变更投递给目标线程后立刻返回。
# 同步 ShowWindow 跨进程要等目标线程处理完 WM_WINDOWPOSCHANGED,
# 全屏游戏主循环一卡,本工具的 UI 就跟着卡住。
ShowWindowAsync = user32.ShowWindowAsync
ShowWindowAsync.argtypes = [wintypes.HWND, ctypes.c_int]
ShowWindowAsync.restype = wintypes.BOOL

SetWindowPos = user32.SetWindowPos
SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int,
                         ctypes.c_int, ctypes.c_int, wintypes.UINT]
SetWindowPos.restype = wintypes.BOOL

MonitorFromWindow = user32.MonitorFromWindow
MonitorFromWindow.argtypes = [wintypes.HWND, wintypes.DWORD]
MonitorFromWindow.restype = wintypes.HANDLE

GetMonitorInfoW = user32.GetMonitorInfoW
GetMonitorInfoW.argtypes = [wintypes.HANDLE, ctypes.c_void_p]
GetMonitorInfoW.restype = wintypes.BOOL

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


class MONITORINFO(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
    ]


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

# SetWindowPlacement 是跨进程"把窗口从最小化拉回来"最可靠的一招:
# 它直接改窗口的显示状态与还原矩形,不依赖目标程序配合处理 WM_SHOWWINDOW。
# (实测永劫无间:ShowWindow(SW_SHOWNORMAL) 发了没反应,走这个能回来)
SetWindowPlacement = user32.SetWindowPlacement
SetWindowPlacement.argtypes = [wintypes.HWND, ctypes.POINTER(WINDOWPLACEMENT)]
SetWindowPlacement.restype = wintypes.BOOL


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
    hung: bool = False        # 线程"未响应"(IsHungAppWindow),界面上要标出来

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

    **慢窗口记忆**:不 pump 消息的窗口每问一次都要等满 100ms,而列表
    每 1.5s 全量刷新一轮 —— 实测 3 个这种窗口就让刷新从 31ms 变成
    349ms,点界面像卡住。所以第一次发现"问标题很慢"就把 hwnd 记下来,
    之后 30 秒内直接返回空串、一次 IPC 都不发。
    """
    if is_known_slow(hwnd):
        return ""
    buf = ctypes.create_unicode_buffer(512)
    result = wintypes.ULONG(0)
    started = time.monotonic()
    n = SendMessageTimeoutW(hwnd, WM_GETTEXT, 512,
                            ctypes.cast(buf, ctypes.c_void_p).value,
                            SMTO_ABORTIFHUNG, SEND_MSG_TIMEOUT_MS,
                            ctypes.byref(result))
    cost = time.monotonic() - started
    # 判"慢"只看耗时,不看结果空不空:空标题的窗口是瞬间返回的,
    # 而卡住的窗口必然吃满超时。用耗时判定才不会把空标题误标成无响应。
    if cost >= SLOW_TITLE_SECONDS:
        _mark_slow(hwnd)
        return ""
    _forget_slow(hwnd)
    return buf.value if n > 0 else ""


# ---------------------------------------------------------------- 慢窗口缓存
SLOW_TITLE_SECONDS = 0.04     # 一次取标题超过 40ms 就当"这窗口不值得再问"
SLOW_TITLE_TTL = 30.0         # 记多久;过期后重试一次(它可能已经恢复了)
_slow_until: dict[int, float] = {}
_slow_lock = threading.Lock()


def _mark_slow(hwnd: int) -> None:
    with _slow_lock:
        _slow_until[hwnd] = time.monotonic() + SLOW_TITLE_TTL
        if len(_slow_until) > 512:
            now = time.monotonic()
            for h in [h for h, t in _slow_until.items() if t < now]:
                del _slow_until[h]


def _forget_slow(hwnd: int) -> None:
    with _slow_lock:
        _slow_until.pop(hwnd, None)


def is_known_slow(hwnd: int) -> bool:
    """这个窗口近期是否被记成"问标题要等很久"。"""
    with _slow_lock:
        until = _slow_until.get(hwnd)
        if until is None:
            return False
        if until < time.monotonic():
            del _slow_until[hwnd]
            return False
        return True


def clear_slow_cache() -> None:
    with _slow_lock:
        _slow_until.clear()


def get_class_name(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    n = GetClassNameW(hwnd, buf, 256)
    return buf.value if n > 0 else ""


def get_window_pid(hwnd: int) -> int:
    pid = wintypes.DWORD(0)
    GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    return int(pid.value)


# 桌面/任务栏的窗口类名。这些属于 explorer,按进程隐藏会连桌面一起藏掉
# (用户实测踩过一次"点一下连桌面都没了")。列表不显示、隐藏也拦一道。
SHELL_WINDOW_CLASSES = ("Progman", "WorkerW", "Shell_TrayWnd",
                        "Shell_SecondaryTrayWnd")


def is_shell_window(hwnd: int) -> bool:
    """是不是桌面/任务栏这类外壳窗口。"""
    return get_class_name(hwnd) in SHELL_WINDOW_CLASSES


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


def process_is_higher_integrity(pid: int) -> bool:
    """目标进程权限是否比本进程高(UIPI 会静默吃掉所有窗口操作)。

    `PROCESS_VM_READ` 是受完整性级别检查的权限:普通权限进程拿管理员权限
    进程的这个权限必然得到 ACCESS_DENIED。

    为什么必须在动手前问这一句(用户实测踩的坑):永劫无间以管理员权限
    运行时,`SetWindowLongPtrW`(加 TOOLWINDOW)、`SetWindowPlacement`
    (拉回窗口)全部静默失败,只有 `ShowWindowAsync(SW_MINIMIZE)` 生效 ——
    结果就是把窗口最小化成"没有任务栏按钮、Alt+Tab 也没有"的状态,
    用户再也点不回来,游戏卡死。宁可不藏,也不能把窗口丢在这个状态里。
    """
    if pid == os.getpid():
        return False
    h = OpenProcess(PROCESS_VM_READ, False, pid)
    if h:
        CloseHandle(h)
        return False
    return GetLastError() == ERROR_ACCESS_DENIED


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

    **process_name 一定要在这里填好**。以前留给调用方自己补,结果规则匹配
    (`TargetRule.matches` 比较进程名)在控制器里悄悄全部失配 —— 场景切了
    一个窗口都没藏,还不报错。按 pid 缓存,同一进程只查一次,可忽略成本。
    """
    result: list[WindowInfo] = []
    names: dict[int, str] = {}

    @WNDENUMPROC
    def _cb(hwnd, _lparam):
        if not IsWindow(hwnd):
            return True
        # 注意:GetParent 的 restype 是 HWND(c_void_p),无父窗口时返回 None
        if GetParent(hwnd):
            return True  # 只收顶层
        wpid = get_window_pid(hwnd)
        if pid is not None and wpid != pid:
            return True
        try:
            style, ex_style = get_window_styles(hwnd)
            title = get_window_text(hwnd)
            if wpid not in names:
                names[wpid] = process_image_path(wpid).rsplit("\\", 1)[-1]
            result.append(WindowInfo(
                hwnd=int(hwnd),
                pid=wpid,
                title=title,
                class_name=get_class_name(hwnd),
                visible=bool(IsWindowVisible(hwnd)),
                minimized=bool(IsIconic(hwnd)),
                ex_style=ex_style,
                style=style,
                rect=get_window_rect(hwnd),
                process_name=names[wpid],
                # 界面上要能看出"这窗口无响应",否则用户只会看到标题空着,
                # 以为是工具没刷新出来。
                # 只在"标题取不到"时才问内核:有标题就说明它活着,而
                # IsHungAppWindow 对 188 个窗口逐个查要多花 ~20ms。
                hung=is_known_slow(int(hwnd))
                     or (not title and bool(IsHungAppWindow(hwnd))),
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


# ---------------------------------------------------------------- 全屏判定
def set_ex_style(hwnd: int, add: int = 0, remove: int = 0) -> int:
    """安全地增删窗口扩展样式,返回修改后的样式。"""
    old = GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    new = (old | add) & ~remove
    if new != old:
        SetWindowLongPtrW(hwnd, GWL_EXSTYLE, new)
    return new


def monitor_rect(hwnd: int) -> tuple[int, int, int, int]:
    """窗口所在显示器的物理矩形;失败返回全 0。"""
    hmon = MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    if not hmon:
        return (0, 0, 0, 0)
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if GetMonitorInfoW(hmon, ctypes.byref(mi)):
        r = mi.rcMonitor
        return (r.left, r.top, r.right, r.bottom)
    return (0, 0, 0, 0)


def is_fullscreen_window(hwnd: int) -> bool:
    """窗口是否处于全屏(无边框且铺满整块显示器)。

    判定标准:没有标题栏 + 覆盖所在显示器 95% 以上 + 是个正常可激活的
    应用窗口。三条都要满足。

    排除项全是实测踩出来的坑 —— 系统里有不少"铺满屏幕但不是游戏"的窗口:
    - 带 WS_CAPTION 的最大化浏览器/编辑器;
    - `Windows 输入体验`(WS_EX_NOREDIRECTIONBITMAP)、`NVIDIA GeForce
      Overlay`(WS_EX_NOACTIVATE|LAYERED)这类覆盖层:全是不可激活的假窗口;
    - 桌面 `Program Manager`(Progman):WS_DISABLED + 铺满整个虚拟桌面。
    把它们误判成全屏的代价是隐藏时白等一次最小化,还可能把覆盖层弄乱。
    """
    style, ex_style = get_window_styles(hwnd)
    if style & (WS_CAPTION | WS_DISABLED):
        return False
    if ex_style & (WS_EX_TOOLWINDOW | WS_EX_NOACTIVATE | WS_EX_LAYERED
                   | WS_EX_NOREDIRECTIONBITMAP):
        return False
    l, t, r, b = get_window_rect(hwnd)
    ml, mt, mr, mb = monitor_rect(hwnd)
    mw, mh = mr - ml, mb - mt
    if mw <= 0 or mh <= 0:
        return False
    return (r - l) >= mw * 0.95 and (b - t) >= mh * 0.95


def is_offscreen(hwnd: int) -> bool:
    """窗口是否已经不在屏幕上了(隐藏,或已最小化)。

    最小化也算 —— 最小化的窗口 IsWindowVisible 仍是 True,但用户看不见它。
    旧的失败判据只用 IsWindowVisible,会把"全屏游戏被最小化"误报成失败。
    """
    if not IsWindowVisible(hwnd):
        return True
    return bool(IsIconic(hwnd))


def is_hung(hwnd: int) -> bool:
    """窗口所属线程是否已经"未响应"(和任务管理器里显示的一致)。

    硬性前置检查:对未响应的窗口做**同步**窗口操作
    (`SetWindowLongPtr` / `SetWindowPlacement` / 同步 `SetWindowPos`)会把
    调用线程一起拖住 —— 实测工具整个界面变成"未响应",用户只能强杀。
    窗口不存在也返回 True(没什么可操作的)。
    """
    if not IsWindow(hwnd):
        return True
    return bool(IsHungAppWindow(hwnd))


def set_placement_cmd(hwnd: int, show_cmd: int) -> bool:
    """直接把窗口的显示状态改成 show_cmd(SetWindowPlacement)。

    比 `ShowWindow` 可靠的地方:不依赖目标程序处理 `WM_SHOWWINDOW`,
    是真正改窗口放置状态。对"被最小化的全屏游戏"这是唯一拉得回来的招
    (实测:永劫无间对 `ShowWindow(SW_SHOWNORMAL)` 完全没反应)。
    """
    wp = WINDOWPLACEMENT()
    wp.length = ctypes.sizeof(WINDOWPLACEMENT)
    if not GetWindowPlacement(hwnd, ctypes.byref(wp)):
        return False
    wp.showCmd = show_cmd
    return bool(SetWindowPlacement(hwnd, ctypes.byref(wp)))


def force_foreground(hwnd: int) -> None:
    """把窗口抢到前台并置顶。

    后台进程直接 `SetForegroundWindow` 常被系统拒绝(前台锁),
    标准绕法是 AttachThreadInput 把自己的输入队列接到当前前台线程上。
    """
    fg = GetForegroundWindow()
    t_fg = GetWindowThreadProcessId(fg, None) if fg else 0
    t_self = kernel32.GetCurrentThreadId()
    attached = False
    try:
        if t_fg and t_fg != t_self:
            attached = bool(user32.AttachThreadInput(t_self, t_fg, True))
        user32.BringWindowToTop(wintypes.HWND(hwnd))
        SetForegroundWindow(wintypes.HWND(hwnd))
    finally:
        if attached:
            user32.AttachThreadInput(t_self, t_fg, False)


def hide_plan(hwnd: int, allow_minimize: bool = False) -> str:
    """隐藏策略。

    `allow_minimize=True`(设置里的「强制隐藏全屏游戏」)时,全屏窗口走
    "先最小化再隐藏";**默认关闭**。

    为什么默认不走这条路:独占全屏的游戏由 D3D/DXGI 直接把画面翻转输出到
    显示器,不看 WS_VISIBLE,所以得先最小化让它掉出独占全屏。但实测永劫
    无间最小化后主线程就不再处理消息,后续调用全部卡死、游戏自己回不来 ——
    比"藏不掉"糟得多。默认只做安全的那一半:SW_HIDE + 工具窗样式,
    藏不住如实告诉用户,不动它的最小化状态。
    """
    if not allow_minimize:
        return "hide"
    return "minimize_then_hide" if is_fullscreen_window(hwnd) else "hide"


def _wait_offscreen(hwnd: int, timeout: float) -> bool:
    """轮询等待窗口离开屏幕,最多 timeout 秒。返回是否已离开。"""
    deadline = time.monotonic() + timeout
    while True:
        if is_offscreen(hwnd):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def wait_offscreen(hwnd: int, timeout: float = 0.3) -> bool:
    """等窗口真的离开屏幕(隐藏或最小化)。给上层判成败用。"""
    return _wait_offscreen(hwnd, timeout)


def _wait_hidden(hwnd: int, timeout: float) -> bool:
    """轮询等待 SW_HIDE 真正生效(WS_VISIBLE 被清掉)。

    和 `_wait_offscreen` 的区别:最小化立刻就算"离开屏幕",但异步的
    SW_HIDE 要等目标线程处理完才落地。判"藏干净了"必须等这一个。
    """
    deadline = time.monotonic() + timeout
    while True:
        if not IsWindowVisible(hwnd):
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.05)


def minimize_window(hwnd: int) -> None:
    """让窗口最小化(两条路一起走,谁先生效算谁的)。

    - PostMessage(WM_SYSCOMMAND, SC_MINIMIZE):和点最小化按钮同一条路径,
      游戏普遍会在这里暂停渲染并交出全屏。异步发送,不阻塞调用方。
    - ShowWindowAsync(SW_MINIMIZE):目标线程消息循环忙时的兜底。
    """
    PostMessageW(hwnd, WM_SYSCOMMAND, SC_MINIMIZE, 0)
    ShowWindowAsync(hwnd, SW_MINIMIZE)


def hide_window(hwnd: int, allow_minimize: bool = False) -> tuple[int, int]:
    """隐藏窗口:先加 WS_EX_TOOLWINDOW(移出任务栏/Alt+Tab),再隐藏。

    返回 (原扩展样式, 原显示命令),供恢复用。

    两条铁律(用户实测"点一下整个界面未响应"之后加的):
    1. **目标未响应就别碰** —— `SetWindowLongPtr`、同步 `SetWindowPos`
       都要等目标线程处理,目标是死的就一起卡死。这种窗口直接返回,
       让上层报"目标无响应",退出状态一律不动。
    2. **能用异步就不用同步** —— `ShowWindowAsync` / `SWP_ASYNCWINDOWPOS`
       把请求投递到对方队列就返回,绝不阻塞调用方线程。
    """
    ex_style = GetWindowLongPtrW(hwnd, GWL_EXSTYLE)
    show_cmd = get_window_placement_cmd(hwnd)
    if is_hung(hwnd):
        return ex_style, show_cmd      # 上层用 is_offscreen 判失败

    # 必须先判定策略再加样式:is_fullscreen_window 会把 WS_EX_TOOLWINDOW
    # 当作"辅助窗口"排除,先加样式的话自己就把判断废掉了。
    plan = hide_plan(hwnd, allow_minimize)
    set_ex_style(hwnd, add=WS_EX_TOOLWINDOW)

    if plan == "minimize_then_hide":
        minimize_window(hwnd)
        _wait_offscreen(hwnd, 0.8)      # 等它真的掉出独占全屏
        ShowWindowAsync(hwnd, SW_HIDE)  # 退出全屏后隐藏才有效
        SetWindowPos(hwnd, None, 0, 0, 0, 0,
                     SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
                     | SWP_NOACTIVATE | SWP_HIDEWINDOW | SWP_ASYNCWINDOWPOS)
        # 等 SW_HIDE 落地:异步投递后立刻检查会看到"还是可见"的旧状态,
        # 误判成"藏不住"就白白回滚了(实测被自己的异步坑过)。
        if not _wait_hidden(hwnd, 0.6) and IsIconic(hwnd):
            # 最小化生效了、隐藏没生效 = 半残状态:窗口在最小化里,而
            # WS_POPUP 的游戏窗口没有任务栏按钮、也不在 Alt+Tab 里,
            # 用户根本点不回来(永劫无间实测就是这么卡死的)。
            # 全有或全无 —— 撤回到原样,让上层如实报"没藏掉"。
            _undo_hide(hwnd, ex_style, show_cmd)
        return ex_style, show_cmd

    ShowWindowAsync(hwnd, SW_HIDE)
    if not _wait_hidden(hwnd, 0.4):
        # 兜底:再投一次(异步,不会等目标线程)。投完再等一小会儿 ——
        # 异步操作立刻检查必然是"还没生效",上层会把成功当失败上报
        # (实测日志里全是"未能隐藏 ... 失败 1/1",其实窗口已经藏了)。
        SetWindowPos(hwnd, None, 0, 0, 0, 0,
                     SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER
                     | SWP_NOACTIVATE | SWP_HIDEWINDOW | SWP_ASYNCWINDOWPOS)
        _wait_hidden(hwnd, 0.3)
    return ex_style, show_cmd


def _undo_hide(hwnd: int, ex_style: int, show_cmd: int) -> None:
    """把没藏干净的操作撤回原样(去样式 + 拉回显示)。"""
    if not is_hung(hwnd):
        set_ex_style(hwnd, remove=WS_EX_TOOLWINDOW,
                     add=ex_style & WS_EX_TOOLWINDOW)
        if set_placement_cmd(hwnd, SW_SHOWNORMAL):
            return
    ShowWindowAsync(hwnd, SW_RESTORE)
    SetWindowPos(hwnd, None, 0, 0, 0, 0,
                 SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
                 | SWP_SHOWWINDOW | SWP_ASYNCWINDOWPOS)


def restore_window(hwnd: int, ex_style: int, show_cmd: int) -> None:
    """恢复窗口:还原扩展样式,再按记录的命令显示。"""
    cmd = show_cmd
    if cmd in (SW_HIDE, SW_SHOWMINNOACTIVE, SW_MINIMIZE):
        cmd = SW_SHOWNORMAL
    elif cmd == SW_SHOWMINIMIZED:
        cmd = SW_RESTORE  # 原来是最小化的,恢复成普通显示
    elif cmd in (SW_SHOWNOACTIVATE, SW_SHOWNA):
        cmd = SW_SHOWNOACTIVATE

    if not is_hung(hwnd):
        set_ex_style(hwnd, remove=WS_EX_TOOLWINDOW,
                     add=ex_style & WS_EX_TOOLWINDOW)
        # SetWindowPlacement 直接改状态:被最小化的全屏游戏对
        # ShowWindow(SW_SHOWNORMAL) 可能毫无反应(实测永劫无间)。
        if set_placement_cmd(hwnd, cmd):
            return
    # 目标未响应 / 改状态失败:只能投递异步请求,绝不在这里等
    ShowWindowAsync(hwnd, cmd)
    SetWindowPos(hwnd, None, 0, 0, 0, 0,
                 SWP_NOMOVE | SWP_NOSIZE | SWP_NOZORDER | SWP_NOACTIVATE
                 | SWP_SHOWWINDOW | SWP_ASYNCWINDOWPOS)


def is_hidden(hwnd: int) -> bool:
    return not IsWindowVisible(hwnd)
