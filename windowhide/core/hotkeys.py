"""hotkeys.py - 全局热键(RegisterHotKey)。

独立线程里建消息窗口跑消息循环,收到 WM_HOTKEY 后通过 Qt 信号
转发到主线程。改快捷键 = 重启线程,成本可忽略。
"""
from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes

from PySide6.QtCore import QObject, Signal

from .win32 import (
    user32, kernel32,
    CreateWindowExW, DefWindowProcW, RegisterClassW, UnregisterClassW,
    GetMessageW, TranslateMessage, DispatchMessageW, RegisterHotKey,
    UnregisterHotKey, PostQuitMessage, DestroyWindow, GetModuleHandleW,
    WM_HOTKEY, WM_QUIT, WM_DESTROY, HWND_MESSAGE,
)

WM_APP = 0x8000
MSG_STRING_SIZE = 256


class WNDCLASS(ctypes.Structure):
    _fields_ = [
        ("style", wintypes.UINT),
        ("lpfnWndProc", ctypes.c_void_p),
        ("cbClsExtra", ctypes.c_int),
        ("cbWndExtra", ctypes.c_int),
        ("hInstance", wintypes.HINSTANCE),
        ("hIcon", wintypes.HICON),
        ("hCursor", wintypes.HANDLE),
        ("hbrBackground", wintypes.HBRUSH),
        ("lpszMenuName", wintypes.LPCWSTR),
        ("lpszClassName", wintypes.LPCWSTR),
    ]


class MSG(ctypes.Structure):
    _fields_ = [
        ("hwnd", wintypes.HWND),
        ("message", wintypes.UINT),
        ("wParam", wintypes.WPARAM),
        ("lParam", wintypes.LPARAM),
        ("time", wintypes.DWORD),
        ("pt", ctypes.c_long * 2),
    ]


WNDPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND,
                             wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM)

VK_NAMES = {
    0x08: "Backspace", 0x09: "Tab", 0x0D: "Enter", 0x1B: "Esc",
    0x20: "Space", 0x2D: "Insert", 0x2E: "Delete",
    0x70: "F1", 0x71: "F2", 0x72: "F3", 0x73: "F4", 0x74: "F5", 0x75: "F6",
    0x76: "F7", 0x77: "F8", 0x78: "F9", 0x79: "F10", 0x7A: "F11", 0x7B: "F12",
    0x25: "Left", 0x26: "Up", 0x27: "Right", 0x28: "Down",
    0x60: "Num0", 0x61: "Num1", 0x62: "Num2", 0x63: "Num3", 0x64: "Num4",
    0x65: "Num5", 0x66: "Num6", 0x67: "Num7", 0x68: "Num8", 0x69: "Num9",
    0x6A: "Num*", 0x6B: "Num+", 0x6D: "Num-", 0x6E: "Num.", 0x6F: "Num/",
}
for _i in range(0x30, 0x3A):
    VK_NAMES[_i] = chr(_i)
for _i in range(0x41, 0x5B):
    VK_NAMES[_i] = chr(_i)
for _i in range(0x30 + 0x30, 0x3A + 0x30):
    VK_NAMES[_i] = f"Numpad{_i - 0x30}"

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000


def hotkey_label(mods: int, vk: int) -> str:
    """格式化为 'Ctrl + Alt + H'。"""
    if not vk:
        return "未设置"
    parts = []
    if mods & MOD_CONTROL:
        parts.append("Ctrl")
    if mods & MOD_ALT:
        parts.append("Alt")
    if mods & MOD_SHIFT:
        parts.append("Shift")
    if mods & MOD_WIN:
        parts.append("Win")
    parts.append(VK_NAMES.get(vk, f"0x{vk:02X}"))
    return " + ".join(parts)


class HotkeyManager(QObject):
    triggered = Signal(str)   # action 名: "toggle" / "hide" / "restore" / "foreground"
    register_failed = Signal(str)   # 注册失败的 action 名(通常是被其它软件占用)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._specs: dict[str, tuple[int, int]] = {}   # action -> (mods, vk)
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._hwnd = 0
        self._hwnd_to_action: dict[int, str] = {}
        self._class_seq = 0

    # ------------------------------------------------------------ 公共接口
    def set_hotkeys(self, specs: dict[str, tuple[int, int]]) -> None:
        """设置热键表并重启注册线程。specs: action -> (mods, vk)。"""
        with self._lock:
            self._specs = dict(specs)
            self._stop_thread()
            if any(vk for _, vk in specs.values()):
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stop_thread()

    def registered_actions(self) -> list[str]:
        """已成功注册的热键 action 列表(诊断用)。"""
        return list(self._hwnd_to_action.values())

    def _stop_thread(self) -> None:
        t = self._thread
        self._thread = None
        if t and t.is_alive():
            hwnd = self._hwnd
            if hwnd:
                user32.PostMessageW(wintypes.HWND(hwnd), WM_QUIT, 0, 0)
            t.join(timeout=2.0)
            if t.is_alive():
                from ..log import write
                write("热键旧线程退出超时(不影响新热键注册)")

    # ------------------------------------------------------------ 工作线程
    def _run(self) -> None:
        with self._lock:
            specs = dict(self._specs)
            self._class_seq += 1
            # 类名必须唯一:同名类第二次 RegisterClassW 必然失败。
            # 曾经的 bug:改设置后热注册时旧线程还没退出完,新线程注册
            # 同名类失败 -> 静默 return -> 旧热键已注销、新热键没注册,
            # 热键整个死掉(用户表现为"快捷键没反应")。
            class_name = f"WHHotkeyMsgWnd.{self._class_seq}"

        hinstance = GetModuleHandleW(None)
        local_map: dict[int, str] = {}   # 本线程的热键 id -> action(闭包捕获)

        def wndproc(hwnd, umsg, wparam, lparam):
            if umsg == WM_HOTKEY:
                action = local_map.get(int(wparam))
                if action:
                    self.triggered.emit(action)
                return 0
            return DefWindowProcW(hwnd, umsg, wparam, lparam)

        proc = WNDPROC(wndproc)
        wc = WNDCLASS()
        wc.lpfnWndProc = ctypes.cast(proc, ctypes.c_void_p)
        wc.hInstance = hinstance
        wc.lpszClassName = class_name
        wanted = [a for a in specs if specs[a][1]]
        if not RegisterClassW(ctypes.byref(wc)):
            self._thread_failed(wanted, "窗口类注册失败")
            return
        hwnd = CreateWindowExW(0, class_name, None, 0, 0, 0, 0, 0,
                               wintypes.HWND(HWND_MESSAGE), None, hinstance, None)
        if not hwnd:
            self._thread_failed(wanted, "消息窗口创建失败")
            return
        self._hwnd = hwnd

        failed = []
        for i, (action, (mods, vk)) in enumerate(specs.items()):
            if not vk:
                continue
            hid = i + 1
            # 正常路径下 _stop_thread 已 join 等旧线程退干净(其 finally 会
            # UnregisterHotKey),不会冲突。但万一旧线程退出超时(2s)还没死,
            # 同一组合会被它占着导致注册失败 —— 重试几次给它让路。
            for attempt in range(6):
                if RegisterHotKey(hwnd, hid, mods | MOD_NOREPEAT, vk):
                    local_map[hid] = action
                    break
                if attempt < 5:
                    time.sleep(0.1)
            else:
                failed.append(action)
        self._hwnd_to_action = local_map

        from ..log import write
        write("热键注册: " + (", ".join(
            f"{a}={hotkey_label(*specs[a])}" for a in wanted) or "无"))
        if failed:
            write("热键注册失败(可能被其它软件占用): " + ",".join(failed))
            self.register_failed.emit(",".join(failed))

        try:
            msg = MSG()
            while True:
                r = GetMessageW(ctypes.byref(msg), None, 0, 0)
                if r == 0 or r == -1:
                    break
                TranslateMessage(ctypes.byref(msg))
                DispatchMessageW(ctypes.byref(msg))
        finally:
            # 退出清理。注意:不能持 self._lock —— _stop_thread 持锁 join
            # 本线程,这里抢锁会死锁。
            for hid in list(local_map):
                UnregisterHotKey(hwnd, hid)
            DestroyWindow(hwnd)
            if self._hwnd == hwnd:
                self._hwnd = 0
            if self._hwnd_to_action is local_map:
                self._hwnd_to_action = {}
            user32.UnregisterClassW(class_name, hinstance)

    def _thread_failed(self, wanted: list[str], reason: str) -> None:
        """线程起不来时绝不能静默 —— 写日志 + 发信号,让用户知道热键没注册上。"""
        from ..log import write
        write(f"热键线程启动失败({reason})")
        if wanted:
            self.register_failed.emit(",".join(wanted))
