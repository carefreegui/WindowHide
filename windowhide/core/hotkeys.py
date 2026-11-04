"""hotkeys.py - 全局热键(键盘 RegisterHotKey + 鼠标侧键低级钩子)。

独立线程里建消息窗口跑消息循环,收到 WM_HOTKEY 后通过 Qt 信号
转发到主线程。改快捷键 = 重启线程,成本可忽略。

**为什么要两套机制**:
`RegisterHotKey` 只认键盘虚拟键码,没有"鼠标侧键"这个东西。侧键只能用
`WH_MOUSE_LL` 低级钩子接管。两套机制同时挂在同一个线程上,因为低级钩子
要求安装它的线程**有消息循环** —— 这里正好有一个现成的。

侧键的取舍:用户既然把它设成热键,就说明想用它当老板键,所以匹配时
**吃掉事件**(不让浏览器前进/后退)。没设成热键的侧键一律放行,浏览器
照常用 —— 只有你主动配置的那一个会失效。
"""
from __future__ import annotations

import ctypes
import threading
import time
from ctypes import wintypes

from PySide6.QtCore import QObject, Signal

from ..config import MOUSE_VKS, VK_XBUTTON1, VK_XBUTTON2
from .win32 import (
    user32, kernel32,
    CreateWindowExW, DefWindowProcW, RegisterClassW, UnregisterClassW,
    GetMessageW, TranslateMessage, DispatchMessageW, RegisterHotKey,
    UnregisterHotKey, PostQuitMessage, DestroyWindow, GetModuleHandleW,
    WM_HOTKEY, WM_QUIT, WM_DESTROY, HWND_MESSAGE,
    SetWindowsHookExW, UnhookWindowsHookEx, CallNextHookEx, xbutton_of,
    WH_MOUSE_LL, HC_ACTION, WM_XBUTTONDOWN, WM_XBUTTONUP,
    XBUTTON1, XBUTTON2,
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

# 鼠标侧键。名字带括号是给用户看的:多数人不知道"侧键"在浏览器里
# 默认干的是前进/后退,不写出来他不会意识到设置后这个功能会失效。
VK_NAMES[VK_XBUTTON1] = "鼠标侧键 1(后退键)"
VK_NAMES[VK_XBUTTON2] = "鼠标侧键 2(前进键)"

MOD_ALT = 0x0001
MOD_CONTROL = 0x0002
MOD_SHIFT = 0x0004
MOD_WIN = 0x0008
MOD_NOREPEAT = 0x4000

LOWLEVELHOOKPROC = ctypes.WINFUNCTYPE(ctypes.c_ssize_t, ctypes.c_int,
                                      wintypes.WPARAM, wintypes.LPARAM)


class _CaptureBus(QObject):
    """录入侧键时的转发总线。

    为什么不让录入按钮直接收 Qt 的鼠标事件:实测过 —— `QPushButton`
    收不到 `BackButton`,因为侧键的 WM_XBUTTONDOWN 只发给**光标下的窗口**,
    捕获时用户手还在鼠标上,光标未必落在那个按钮里,事件就丢了。

    所以捕获也走同一个钩子:钩子此刻**不拦截**,把侧键编号通过这个总线
    转给界面。好处是顺带解决了"钩子把自己的输入吃掉"的死结 —— 否则
    用户永远没法把侧键从 A 改成 B。
    """
    captured = Signal(int)   # 侧键的伪 VK


capture_bus = _CaptureBus()

# 捕获态用 Event 而不是布尔:钩子在线程里读,界面在主线程写,得是原子的。
# 不加锁读取(Event.is_set 自带锁),也不会阻塞钩子回调。
_capturing = threading.Event()


def set_capturing(on: bool) -> None:
    """界面进入/退出"等待按键"状态时调用。"""
    if on:
        _capturing.set()
    else:
        _capturing.clear()


def is_capturing() -> bool:
    return _capturing.is_set()


def hotkey_label(mods: int, vk: int) -> str:
    """格式化为 'Ctrl + Alt + H' / '鼠标侧键 1(后退键)'。"""
    if not vk:
        return "未设置"
    if vk in MOUSE_VKS:
        # 侧键没有修饰符的概念,别拼出 "Ctrl + 鼠标侧键" 这种看不懂的东西
        return VK_NAMES.get(vk, "鼠标侧键")
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
        self._mouse: dict[str, int] = {}               # action -> 侧键伪 VK
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._hwnd = 0
        self._hwnd_to_action: dict[int, str] = {}
        self._class_seq = 0

    # ------------------------------------------------------------ 公共接口
    def set_hotkeys(self, specs: dict[str, tuple[int, int]],
                    mouse: dict[str, int] | None = None) -> None:
        """设置热键表并重启注册线程。

        specs: action -> (mods, vk),键盘组合键。
        mouse: action -> 侧键伪 VK(0 = 这个动作不配侧键)。

        两者是**并存**关系而不是二选一:同一个动作可以既挂键盘组合、又挂
        鼠标侧键,哪个在手边用哪个。这也正是老板键的使用姿势 —— 藏窗口
        往往是一瞬间的事,没时间去把另一只手挪过来。
        """
        with self._lock:
            self._specs = dict(specs)
            self._mouse = dict(mouse or {})
            self._stop_thread()
            if (any(vk for _, vk in specs.values())
                    or any(self._mouse.values())):
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
            mouse_cfg = dict(self._mouse)
            self._class_seq += 1
            # 类名必须唯一:同名类第二次 RegisterClassW 必然失败。
            # 曾经的 bug:改设置后热注册时旧线程还没退出完,新线程注册
            # 同名类失败 -> 静默 return -> 旧热键已注销、新热键没注册,
            # 热键整个死掉(用户表现为"快捷键没反应")。
            class_name = f"WHHotkeyMsgWnd.{self._class_seq}"

        hinstance = GetModuleHandleW(None)
        local_map: dict[int, str] = {}   # 本线程的热键 id -> action(闭包捕获)

        # 侧键单独一张表:它们不走 RegisterHotKey(那个接口不认识鼠标),
        # 由下面的低级钩子处理。两种来源都收:显式给的 mouse 参数,以及
        # 直接写在 specs 里的侧键(=某些调用方/测试的简写)。
        mouse_map: dict[int, str] = {}
        for a, (_m, vk) in specs.items():
            if vk in MOUSE_VKS:
                mouse_map[vk] = a
        for a, vk in mouse_cfg.items():
            if vk:
                mouse_map[vk] = a

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
            if not vk or vk in MOUSE_VKS:
                continue      # 侧键交给钩子,RegisterHotKey 不支持
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

        # ---- 鼠标侧键钩子 ----
        # 无条件安装:当下没配侧键也装 —— 用户随时可能进设置页录入,而线程
        # 不会为此重启。快路径只有两次整数比较,1000Hz 鼠标每秒过 1000 次
        # 也只是多几次函数返回,不值得为此加一套"按需装卸"的同步机制。
        hook_id = 0
        hook_proc = None

        def mouse_proc(ncode, wparam, lparam):
            # 快路径:不是侧键就立刻走人。装了钩子之后**每个**鼠标事件
            # (移动、滚轮、左右键)都会进这里,这一行必须最省。
            if ncode == HC_ACTION and (wparam == WM_XBUTTONDOWN
                                       or wparam == WM_XBUTTONUP):
                btn = xbutton_of(lparam)
                vk = (VK_XBUTTON1 if btn == XBUTTON1
                      else VK_XBUTTON2 if btn == XBUTTON2 else 0)
                if vk:
                    if is_capturing():
                        # 正在录入新热键:放行,只把"按下"转给界面。
                        # 不这样的话钩子会吃掉自己的输入 —— 侧键设成 A
                        # 之后就再也改不成 B 了。
                        if wparam == WM_XBUTTONDOWN:
                            capture_bus.captured.emit(vk)
                    else:
                        action = mouse_map.get(vk)
                        if action:
                            if wparam == WM_XBUTTONDOWN:
                                self.triggered.emit(action)
                            # 吃掉。DOWN 和 UP 都得拦:只拦 DOWN 会让浏览器
                            # 收到一个没有配对的孤儿 UP,个别程序会卡在
                            # "按住"状态。
                            return 1
            return CallNextHookEx(0, ncode, wparam, lparam)

        hook_err = 0
        try:
            hook_proc = LOWLEVELHOOKPROC(mouse_proc)
            hook_id = SetWindowsHookExW(
                WH_MOUSE_LL, ctypes.cast(hook_proc, ctypes.c_void_p),
                hinstance, 0)
            if not hook_id:
                hook_err = kernel32.GetLastError()
        except Exception:
            hook_id, hook_err = 0, -1

        write("热键注册: " + (", ".join(
            f"{a}={hotkey_label(*specs[a])}" for a in wanted) or "无"))
        if mouse_map:
            # 单独记一行:不然日志里只有键盘键,"侧键没反应"就无从查起
            write("鼠标侧键: " + ", ".join(
                f"{a}={hotkey_label(0, vk)}" for vk, a in mouse_map.items()))
        if not hook_id:
            write(f"鼠标侧键钩子安装失败(err={hook_err})")
            if mouse_map:
                # 配了侧键却装不上钩子 = 那几个热键根本不存在,必须报出来
                self.register_failed.emit(",".join(mouse_map.values()))
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
            if hook_id:
                UnhookWindowsHookEx(hook_id)
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
