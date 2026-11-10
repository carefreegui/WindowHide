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
from dataclasses import dataclass, field

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
    WH_KEYBOARD_LL, WM_KEYDOWN, WM_KEYUP, WM_SYSKEYDOWN, WM_SYSKEYUP,
    kb_vk_of,
)

WM_APP = 0x8000
# 发给热键消息窗口的内部指令:录入新热键时临时注销 / 事后恢复。
# 为什么非要走消息:RegisterHotKey 注册的组合是**系统层截获**的 ——
# 键盘驱动直接把它变成 WM_HOTKEY 投给注册窗口,焦点窗口连 WM_KEYDOWN
# 都收不到。用户在录入框里按一个已经设过的组合,结果键进不了框,却把
# 隐藏/恢复真的执行了一遍(还是拿着主界面勾选的那批窗口)。所以录入期间
# 必须先注销,录完再注册回来。
WM_APP_PAUSE = WM_APP + 1
WM_APP_RESUME = WM_APP + 2
MSG_STRING_SIZE = 256

# 低级鼠标钩子出意外的次数。到了阈值就把回调变成"只转发"——见 mouse_proc
# 里的注释:这条链上宁可功能废掉,也不能让整台机器的输入受影响。
_hook_errors = 0
HOOK_ERROR_LIMIT = 30


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

# 符号键。VK_OEM_* 不是 ASCII —— 0xDB 是「[」而不是 0x5B,少了这张表
# 用户设了 Ctrl+[ 之后按钮会显示成一串十六进制,看着像没识别。
VK_NAMES.update({
    0xBA: ";", 0xBB: "=", 0xBC: ",", 0xBD: "-", 0xBE: ".",
    0xBF: "/", 0xC0: "`", 0xDB: "[", 0xDC: "\\", 0xDD: "]", 0xDE: "'",
})

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
    # 新按钮开始录入时广播,让还在等按键的旧按钮自己退出。
    # 不广播的话:用户点 A 再点 B,A 没收到 focusOut 就一直挂在"请按键",
    # 而全局账本只认最后那个人 —— 界面显示和实际状态就对不上了
    # (用户原话:"有时候能点,有时候点不了")。
    #
    # 参数类型必须是 **object** 而不是 int:传的是 `id(按钮)`,64 位下
    # 轻松超过 2^31,而 PySide6 的 `Signal(int)` 是 C++ 的 32 位 int ——
    # 溢出后整条信号链报 SystemError,槽函数静默不执行(调试时非常难看出
    # 是"类型不匹配"还是"逻辑没写对")。
    owner_changed = Signal(object)   # 新 owner 的 id(按钮的 id(self))


capture_bus = _CaptureBus()

# ---------------------------------------------------------------------------
# 捕获态账本
#
# 曾经这里只有一个布尔 `_capturing`。它有两个致命问题,都是用户实机踩出来的:
#
# 1. **多按钮互相踩**:设置页上有 6 个录入按钮(切换/隐藏/恢复 × 键盘/侧键)。
#    用户点 A 再点 B,A 未必收到 focusOut,于是 A 的 `_end_capture()` 不会执行,
#    而 B 的 `set_capturing(False)` 又会把 A 的捕获状态一起清掉 —— 表现为
#    "有时候能点有时候点不了"。改成登记表:谁进谁登记,谁退谁销自己那一条,
#    最后一个退出的人才真正关掉标志。
#
# 2. **对话框一关就永远卡住**:用户录入到一半按 Esc / 点取消,对话框走
#    `QDialog::reject()` 直接关掉,按钮的 `_end_capture()` 根本没机会跑,
#    标志就永久停在 True。之后所有侧键热键全部失效(钩子以为还在录入,
#    只发信号不触发动作)。所以这里再加一条**超时自愈**:超过
#    `CAPTURE_MAX_SECONDS` 没人来销账,就当它没发生过。
_capturing = threading.Event()        # 快路径:钩子回调直接读这个
_capture_owners: set[int] = set()     # 谁在等按键(按钮用 id(self))
_capture_lock = threading.Lock()
_capture_deadline = 0.0               # monotonic 时间戳
CAPTURE_MAX_SECONDS = 120.0           # 忘了退出就自动失效,不用重启程序

_ANON_OWNER = 0                       # 没有 owner 的兼容调用(set_capturing)

# 录入期间要临时注销全局热键,由 HotkeyManager 启动时登记这两个回调。
# 放在模块级而不是让 hotkey_edit 直接持有 manager:界面层不该知道热键线程
# 的存在,它只需要说"我进录入态了 / 我退了"。
_pause_cb = None
_resume_cb = None


def set_capture_hooks(pause_cb, resume_cb) -> None:
    """由 HotkeyManager 在启动时登记(见 app.py)。"""
    global _pause_cb, _resume_cb
    _pause_cb, _resume_cb = pause_cb, resume_cb


def _notify_capture_state(capturing: bool) -> None:
    """第一个 owner 进来 → 暂停热键;最后一个 owner 走掉 → 恢复。"""
    cb = _pause_cb if capturing else _resume_cb
    if cb is None:
        return
    try:
        cb()
    except Exception:
        # 这里绝不能把异常抛给界面:暂停失败最多是"按键没录进去",
        # 抛出去就是设置窗口打不开了。
        pass


def _capture_active() -> bool:
    """钩子回调里调用的版本:绝不拿锁、绝不阻塞。

    过期判断放在这里而不是单独开个定时器:平时 `_capturing` 是 False,
    第一行就返回了;只有真在录入时才多一次 `time.monotonic()` 比较。
    """
    if not _capturing.is_set():
        return False
    if time.monotonic() > _capture_deadline:
        # 过期。这里不能拿锁(钩子回调阻塞 = 全系统输入卡顿),所以只清
        # 快路径标志,账本留给下一次 begin/end 顺手清。
        _capturing.clear()
        return False
    return True


def begin_capture(owner: int) -> None:
    """登记"我在等按键"。第一个进来的人负责暂停全局热键。"""
    global _capture_deadline
    with _capture_lock:
        first = not _capture_owners
        _capture_owners.add(owner)
        _capture_deadline = time.monotonic() + CAPTURE_MAX_SECONDS
        _capturing.set()
    if first:
        _notify_capture_state(True)


def end_capture(owner: int) -> None:
    """销自己那一条;最后一个走掉的人负责把热键恢复回去。"""
    with _capture_lock:
        _capture_owners.discard(owner)
        last = not _capture_owners
        if last:
            _capturing.clear()
    if last:
        _notify_capture_state(False)


def reset_capture() -> None:
    """无条件清空。设置对话框关闭时必须调这个 —— 那是唯一能保证
    "不管里面发生过多乱的事,出门时一定是干净的"的地方。"""
    with _capture_lock:
        had = bool(_capture_owners)
        _capture_owners.clear()
        _capturing.clear()
    if had:
        _notify_capture_state(False)


def set_capturing(on: bool) -> None:
    """兼容旧接口(测试与外部调用)。没有 owner,一律走空账。"""
    if on:
        begin_capture(_ANON_OWNER)
    else:
        reset_capture()


def is_capturing() -> bool:
    return _capture_active()


def capture_owners() -> int:
    """当前有几个按钮在等按键(诊断用)。"""
    with _capture_lock:
        return len(_capture_owners)



@dataclass
class HotkeyPlan:
    """一份配置最终要注册成什么。纯数据,由 `plan_hotkeys()` 产出。"""
    specs: dict[str, tuple[int, int]] = field(default_factory=dict)    # 独占(RegisterHotKey)
    shadow: dict[str, tuple[int, int]] = field(default_factory=dict)   # 旁听(键盘钩子)
    mouse: dict[str, int] = field(default_factory=dict)
    merged: list[str] = field(default_factory=list)   # 哪些键因"两格相同"合并成了切换
    unsafe: list[str] = field(default_factory=list)   # 历史字段,现已不填(见下)


def plan_hotkeys(cfg) -> HotkeyPlan:
    """把配置算成三张表。纯函数,不碰全局状态,方便把规则钉死。

    **三张表分别对应三种触发机制**,这不是分类癖,而是被迫的:
    · `specs`  —— 带 Ctrl/Alt/Win,走 `RegisterHotKey`(系统层截获,零延迟)
    · `shadow` —— 纯 Shift / 单个功能键,走 `WH_KEYBOARD_LL` 旁听(不截获,
                  原程序照常收到 = "两个都触发")
    · `mouse`  —— 鼠标侧键,同样走钩子,而且要**吃掉**事件

    以前这里把 `shadow` 那一类直接判成 `unsafe` 丢掉。用户后来说得很清楚:
    「Shift+Enter 其实特别好操作,你反而多了三个键之后没那么顺畅,稍微占用
    一点也可以,同时触发也可以,提醒一下就行」。那就别拦,换机制。

    另一条老规则必须保留:**隐藏键和恢复键填成同一个时,它就该是切换键**
    (键盘 RegisterHotKey 对同一组合只允许注册一次,侧键字典会互相覆盖,
    用户看到的是"要么只能隐藏、要么只能恢复")。
    """
    plan = HotkeyPlan()

    def put(action: str, spec) -> None:
        """按机制分派一个键盘槽。没设置就什么都不做。"""
        if spec is None or not spec.valid or spec.is_mouse:
            return          # 侧键由调用方的 mouse 参数处理,这里只收键盘
        if spec.shadow:
            plan.shadow[action] = (spec.mods, spec.vk)
        else:
            plan.specs[action] = (spec.mods, spec.vk)

    # "隐藏本工具"(收进托盘)与模式无关,始终注册
    put("hide_self", cfg.hotkey_hide_self)

    if getattr(cfg, "hotkey_mode", "toggle") == "split":
        hk, rk = cfg.hotkey_hide, cfg.hotkey_restore
        hm, rm = cfg.hotkey_hide_mouse, cfg.hotkey_restore_mouse

        if hk.valid and hk == rk:
            put("toggle", hk)
            plan.merged.append("键盘")
        else:
            put("hide", hk)
            put("restore", rk)

        if hm.valid and hm == rm:
            plan.mouse["toggle"] = hm.vk
            plan.merged.append("侧键")
        else:
            if hm.valid:
                plan.mouse["hide"] = hm.vk
            if rm.valid:
                plan.mouse["restore"] = rm.vk
    else:
        put("toggle", cfg.hotkey_toggle)
        if cfg.hotkey_toggle_mouse.valid:
            plan.mouse["toggle"] = cfg.hotkey_toggle_mouse.vk

    return plan


def side_button_decision(wparam: int, vk: int, *, capturing: bool,
                         has_action: bool, paired: dict[int, bool]
                         ) -> tuple[bool, str]:
    """决定一个鼠标侧键事件该放行还是吃掉。纯函数,方便把时序钉死。

    返回 (是否吃掉, 副作用),副作用是 "" / "trigger" / "capture"。

    **`paired` 必须由调用方跨事件持有**,这是这个函数存在的全部理由:
    侧键的 DOWN 和 UP 是一对,处理方式必须一致。以前每个事件都重新判
    "现在是不是录入态" —— 用户在录入态按下(DOWN 放行),界面在 DOWN 的
    回调里就退出了录入态,手指抬起时钩子看到"不在录入态",就把这个 UP
    当成热键尾巴吃掉。Qt 永远收不到 release,按键状态 + 隐式 SetCapture
    一起卡死:鼠标点哪都没反应,只有本程序的窗口能点。
    """
    if wparam == WM_XBUTTONDOWN:
        if capturing:
            paired[vk] = False           # 放行了,抬起也得放
            return False, "capture"
        if has_action:
            paired[vk] = True            # 吃掉了,抬起也得吃
            return True, "trigger"
        paired.pop(vk, None)             # 这个键我们不管
        return False, ""
    # WM_XBUTTONUP:只认 DOWN 时记下的答案,**绝不重新判断**
    return (paired.pop(vk, None) is True), ""


# 物理修饰键 -> 配置里的位掩码。
#
# 低级键盘钩子报的是**具体键**:左 Shift 是 0xA0 而不是 0x10。不归一化的话
# 用户同时按 Shift+Enter 永远匹配不上配置里的 MOD_SHIFT。
MOD_VK_BITS = {
    0xA0: MOD_SHIFT, 0xA1: MOD_SHIFT, 0x10: MOD_SHIFT,
    0xA2: MOD_CONTROL, 0xA3: MOD_CONTROL, 0x11: MOD_CONTROL,
    0xA4: MOD_ALT, 0xA5: MOD_ALT, 0x12: MOD_ALT,
    0x5B: MOD_WIN, 0x5C: MOD_WIN,
}


def shadow_step(wparam: int, vk: int, mods_down: int,
                table: dict[tuple[int, int], str],
                fired: set[tuple[int, int]]) -> tuple[int, str]:
    """旁听热键的状态机,走一步。返回 (新的修饰键状态, 要执行的动作或 "")。

    纯函数,把三个最容易错的点钉死:
    1. **修饰键归一化**(见 MOD_VK_BITS);
    2. **按住不放不能连续触发** —— 按住会连续来 WM_KEYDOWN,`fired` 记住
       已经触发过的组合,任何键抬起时清空;
    3. **修饰键状态一变,旧组合作废** —— 否则"按住 Shift → 松开 Shift →
       再按 Enter"会把残留的 (SHIFT, Enter) 当成还按着 Shift。

    `table` 的 key 是 (mods 位掩码, vk),值为动作名。**永远不吞事件** ——
    调用方拿到动作后照样 CallNextHookEx,这正是"两个都触发"的实现方式。
    """
    bit = MOD_VK_BITS.get(vk)
    down = wparam in (WM_KEYDOWN, WM_SYSKEYDOWN)
    if bit:
        if down:
            mods_down |= bit
        else:
            mods_down &= ~bit
            fired.clear()
        return mods_down, ""
    if down:
        key = (mods_down, vk)
        if key in fired:
            return mods_down, ""      # 自动重复,不是新的一次按下
        fired.add(key)
        return mods_down, table.get(key, "")
    fired.clear()                     # 任何其它键抬起 = 一次组合结束
    return mods_down, ""


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
        self._shadow: dict[tuple[int, int], str] = {}  # (mods, vk) -> action,旁听
        self._mouse: dict[str, int] = {}               # action -> 侧键伪 VK
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._hwnd = 0
        self._hwnd_to_action: dict[int, str] = {}
        self._class_seq = 0
        self._paused = False
        self._pause_ack = threading.Event()

    # ------------------------------------------------------------ 公共接口
    def set_hotkeys(self, specs: dict[str, tuple[int, int]],
                    mouse: dict[str, int] | None = None,
                    shadow: dict[str, tuple[int, int]] | None = None) -> None:
        """设置热键表并重启注册线程。

        specs: action -> (mods, vk),走 `RegisterHotKey`(**独占**)。
        mouse: action -> 侧键伪 VK(0 = 这个动作不配侧键)。
        shadow: action -> (mods, vk),走键盘钩子**旁听** —— 原程序照常收到
                这个键,本工具也同时触发。纯 Shift 组合和单个功能键走这里。

        三者是**并存**关系而不是互斥:同一个动作可以既挂键盘组合、又挂
        鼠标侧键,哪个在手边用哪个。这也正是老板键的使用姿势 —— 藏窗口
        往往是一瞬间的事,没时间去把另一只手挪过来。
        """
        with self._lock:
            self._specs = dict(specs)
            self._mouse = dict(mouse or {})
            # 旁听表反转成 (mods, vk) -> action:钩子回调里每一步都要查,
            # 现算就是每按一个键多一次字典构造。
            self._shadow = {(m, v): a for a, (m, v) in (shadow or {}).items()}
            # 应用新配置 = 这些热键该生效,不管之前是不是因为录入被暂停过
            self._paused = False
            self._stop_thread()
            if (any(vk for _, vk in specs.values())
                    or any(self._mouse.values()) or self._shadow):
                self._thread = threading.Thread(target=self._run, daemon=True)
                self._thread.start()

    def stop(self) -> None:
        with self._lock:
            self._stop_thread()

    # ------------------------------------------------------------ 录入期暂停
    def pause(self) -> None:
        """临时注销所有全局键盘热键。侧键钩子**保留**(录入侧键还得靠它)。

        🔴 为什么必须这么做:`RegisterHotKey` 注册的组合是**系统层截获**的 ——
        键盘驱动直接把它变成 WM_HOTKEY 投给注册窗口,焦点窗口连 WM_KEYDOWN
        都收不到。用户在录入框里按一个已经设过的组合时:
          · 键进不了输入框 → 想改成"和另一个一样"根本录不进去;
          · 却真的触发了一次隐藏 —— 用的还是主界面勾着的那批窗口。
        用户的感受就是"我在设置里按键,窗口居然真被藏了"。

        用户为此想出的"偏方"是"先别在主界面勾选任何东西",这说明问题已经
        严重到影响正常使用了。
        """
        with self._lock:
            if self._paused:
                return
            hwnd = self._hwnd
            self._paused = True
        if not hwnd:
            return
        self._pause_ack.clear()
        user32.PostMessageW(wintypes.HWND(hwnd), WM_APP_PAUSE, 0, 0)
        # 异步投递 + 短超时:热键线程收到后只做几次 UnregisterHotKey,微秒级。
        # 0.2 秒是给"线程刚起、消息循环还没转起来"留的余量。绝不 SendMessage
        # —— 那是跨线程同步调用,对方卡住会把界面一起冻死。
        if not self._pause_ack.wait(0.2):
            from ..log import write
            write("热键暂停超时(线程未响应),录入时可能仍会误触发全局热键")

    def resume(self) -> None:
        """把 pause() 注销掉的热键重新注册回来。"""
        with self._lock:
            if not self._paused:
                return
            hwnd = self._hwnd
            self._paused = False
        if not hwnd:
            return
        self._pause_ack.clear()
        user32.PostMessageW(wintypes.HWND(hwnd), WM_APP_RESUME, 0, 0)
        self._pause_ack.wait(0.2)

    def is_paused(self) -> bool:
        """热键现在是不是因为"正在录入"被临时注销了。"""
        with self._lock:
            return self._paused

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
            shadow_cfg = dict(self._shadow)
            mouse_cfg = dict(self._mouse)
            self._class_seq += 1
            # 类名必须唯一:同名类第二次 RegisterClassW 必然失败。
            # 曾经的 bug:改设置后热注册时旧线程还没退出完,新线程注册
            # 同名类失败 -> 静默 return -> 旧热键已注销、新热键没注册,
            # 热键整个死掉(用户表现为"快捷键没反应")。
            class_name = f"WHHotkeyMsgWnd.{self._class_seq}"

        hinstance = GetModuleHandleW(None)
        local_map: dict[int, str] = {}   # 本线程的热键 id -> action(闭包捕获)
        # 注册成功的 (hid, mods, vk)。暂停后要照这张表重新注册,
        # 所以必须留着原始参数 —— local_map 只有 action 名。
        reg_specs: list[tuple[int, int, int]] = []

        # 侧键单独一张表:它们不走 RegisterHotKey(那个接口不认识鼠标),
        # 由下面的低级钩子处理。两种来源都收:显式给的 mouse 参数,以及
        # 直接写在 specs 里的侧键(=某些调用方/测试的简写)。
        #
        # 一个侧键只能对应一个动作(字典会覆盖),所以同一个侧键被两个动作
        # 抢的时候必须留下痕迹 —— 否则用户看到的是"恢复键能用了但隐藏键
        # 没了",完全无从排查。上层已经把"隐藏/恢复同键"合并成 toggle,
        # 走到这里还冲突就说明是真撞了(比如"隐藏本工具"和"切换"同侧键)。
        mouse_map: dict[int, str] = {}
        mouse_conflicts: list[str] = []
        for a, (_m, vk) in specs.items():
            if vk in MOUSE_VKS:
                if vk in mouse_map and mouse_map[vk] != a:
                    mouse_conflicts.append(f"{hotkey_label(0, vk)}:{mouse_map[vk]}/{a}")
                mouse_map[vk] = a
        for a, vk in mouse_cfg.items():
            if vk:
                if vk in mouse_map and mouse_map[vk] != a:
                    mouse_conflicts.append(f"{hotkey_label(0, vk)}:{mouse_map[vk]}/{a}")
                mouse_map[vk] = a

        def wndproc(hwnd, umsg, wparam, lparam):
            if umsg == WM_HOTKEY:
                action = local_map.get(int(wparam))
                if action:
                    self.triggered.emit(action)
                return 0
            if umsg == WM_APP_PAUSE:
                # 录入新热键:全部注销。侧键钩子**不动** —— 录入侧键还得靠它,
                # 而钩子在捕获态下本来就走"放行 + 转发"分支,不会触发动作。
                for hid, _mods, _vk in reg_specs:
                    UnregisterHotKey(hwnd, hid)
                self._pause_ack.set()
                return 0
            if umsg == WM_APP_RESUME:
                refailed = []
                for hid, mods, vk in reg_specs:
                    if not RegisterHotKey(hwnd, hid, mods | MOD_NOREPEAT, vk):
                        refailed.append(local_map.get(hid, str(hid)))
                self._pause_ack.set()
                if refailed:
                    # 暂停期间被别人抢走的情况极少,但不能静默
                    self.register_failed.emit(",".join(refailed))
                return 0
            return DefWindowProcW(hwnd, umsg, wparam, lparam)

        proc = WNDPROC(wndproc)
        wc = WNDCLASS()
        wc.lpfnWndProc = ctypes.cast(proc, ctypes.c_void_p)
        wc.hInstance = hinstance
        wc.lpszClassName = class_name
        wanted_reg = [a for a in specs if specs[a][1]]
        # 启动失败要报"所有本该生效"的动作,旁听的也算
        wanted = wanted_reg + list(shadow_cfg.values())
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
        registered: dict[tuple[int, int], str] = {}   # 已注册的组合 -> 先占它的动作
        for i, (action, (mods, vk)) in enumerate(specs.items()):
            if not vk or vk in MOUSE_VKS:
                continue      # 侧键交给钩子,RegisterHotKey 不支持
            if (mods, vk) in registered:
                # 同一个窗口不能用同一个组合注册两次。上层已经把"隐藏/恢复
                # 同键"合并成 toggle,走到这里说明真冲突(比如"隐藏本工具"
                # 和"切换"撞了)。与其重试 5 次白等半秒,不如立刻报出去。
                failed.append(action)
                continue
            hid = i + 1
            # 正常路径下 _stop_thread 已 join 等旧线程退干净(其 finally 会
            # UnregisterHotKey),不会冲突。但万一旧线程退出超时(2s)还没死,
            # 同一组合会被它占着导致注册失败 —— 重试几次给它让路。
            for attempt in range(6):
                if RegisterHotKey(hwnd, hid, mods | MOD_NOREPEAT, vk):
                    local_map[hid] = action
                    registered[(mods, vk)] = action
                    reg_specs.append((hid, mods, vk))
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

        # 侧键的按下/抬起**必须成对处理**。
        #
        # 🔴 这里踩过最严重的一次:用户实机稳定复现"设置完侧键之后鼠标点哪
        # 都没反应,只有本工具的窗口能点"。
        #
        # 机制:在录入态按下侧键,DOWN 被放行 → 界面在 DOWN 里就退出了录入态
        # → 手指抬起时钩子重新判 `_capture_active()` 已经是 False → 走"热键"
        # 分支 → 这个 UP 被 `return 1` 吃掉。Qt 永远收不到 BackButton 的
        # release,按键状态和隐式 SetCapture 一起卡死 —— 所有鼠标事件都被
        # 强行发给本线程的窗口,点桌面/任务管理器全无反应。
        #
        # 修复只有一条路:DOWN 怎么处理,UP 就怎么处理。用一张表记着。
        down_action: dict[int, bool] = {}    # vk -> 这个 DOWN 是否被吃掉

        def mouse_proc(ncode, wparam, lparam):
            global _hook_errors
            # 🔴 这个函数挂在系统**输入链**上。它慢 1ms,整台机器的鼠标就慢
            # 1ms;它抛异常,后果不是"这个功能不好用",而是用户觉得鼠标坏了
            # —— 用户实机踩过:设置里乱按一通之后,鼠标点桌面上任何东西都
            # 没反应,只剩 Win+D 还能用(键盘不走这条链)。
            #
            # 所以:整段 try/except,任何意外都当"放行";另外计数,坏到一定
            # 次数就**自我阉割**(只转发、什么都不做)——侧键功能废掉,但系统
            # 一定保住。这个取舍没有第二种选择。
            try:
                if _hook_errors < HOOK_ERROR_LIMIT and ncode == HC_ACTION and (
                        wparam == WM_XBUTTONDOWN or wparam == WM_XBUTTONUP):
                    btn = xbutton_of(lparam)
                    vk = (VK_XBUTTON1 if btn == XBUTTON1
                          else VK_XBUTTON2 if btn == XBUTTON2 else 0)
                    if vk:
                        # 捕获态只在 DOWN 时问一次(UP 不该重新判断,见函数注释)
                        capture_now = (wparam == WM_XBUTTONDOWN
                                       and _capture_active())
                        eat, effect = side_button_decision(
                            wparam, vk, capturing=capture_now,
                            has_action=bool(mouse_map.get(vk)),
                            paired=down_action)
                        if effect == "capture":
                            capture_bus.captured.emit(vk)
                        elif effect == "trigger":
                            self.triggered.emit(mouse_map[vk])
                        if eat:
                            return 1
            except Exception as exc:            # noqa: BLE001 - 宁可吞掉
                _hook_errors += 1
                if _hook_errors <= 3:
                    try:
                        write(f"鼠标钩子回调异常({_hook_errors}/{HOOK_ERROR_LIMIT}),"
                              f"已按放行处理: {exc!r}")
                    except Exception:
                        pass
                elif _hook_errors == HOOK_ERROR_LIMIT:
                    try:
                        write("鼠标钩子异常过多,已自我停用(放行所有鼠标事件)。"
                              "侧键热键将不再响应,其它功能不受影响。")
                    except Exception:
                        pass
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

        # ---- 键盘"旁听"钩子 ----
        #
        # 只给 shadow 里那几组装。它们被明确排除在 RegisterHotKey 之外:
        # 系统层截获会让原程序收不到这个键(Shift+Enter 不再换行),
        # 而用户要的是"两个都触发"。钩子正好能只旁听 —— 看完了照样
        # CallNextHookEx 放行给下面的程序。
        #
        # 这个钩子挂在**全系统的键盘输入通路上**,所以回调里只做几件极便宜
        # 的事:整数比较、一次字典查找、发一个跨线程信号。绝不碰文件/锁。
        kbd_hook = 0
        kbd_proc = None
        if shadow_cfg:
            mods_down = 0
            fired: set[tuple[int, int]] = set()

            def kbd_proc_fn(ncode, wparam, lparam):
                global _hook_errors
                try:
                    if (_hook_errors < HOOK_ERROR_LIMIT
                            and ncode == HC_ACTION
                            and wparam in (WM_KEYDOWN, WM_KEYUP,
                                           WM_SYSKEYDOWN, WM_SYSKEYUP)):
                        nonlocal mods_down
                        mods_down, action = shadow_step(
                            wparam, kb_vk_of(lparam), mods_down,
                            shadow_cfg, fired)
                        # 录入态里只更新状态、不触发 —— 否则在设置页按
                        # Shift+Enter 想改键,反而先把窗口藏了一遍。
                        if action and not _capture_active():
                            self.triggered.emit(action)
                except Exception as exc:            # noqa: BLE001
                    _hook_errors += 1
                    if _hook_errors <= 3:
                        try:
                            write(f"键盘钩子回调异常({_hook_errors}/"
                                  f"{HOOK_ERROR_LIMIT}),已按放行处理: {exc!r}")
                        except Exception:
                            pass
                # ⚠️ 永远放行。这里 `return 1` 就等于回到"独占热键",
                # 用户按 Shift+Enter 换行就没了 —— 那正是要避免的事。
                return CallNextHookEx(0, ncode, wparam, lparam)

            try:
                kbd_proc = LOWLEVELHOOKPROC(kbd_proc_fn)
                kbd_hook = SetWindowsHookExW(
                    WH_KEYBOARD_LL, ctypes.cast(kbd_proc, ctypes.c_void_p),
                    hinstance, 0)
                if not kbd_hook:
                    write(f"键盘旁听钩子安装失败(err={kernel32.GetLastError()}),"
                          f"这些组合将不生效: "
                          + ", ".join(f"{a}={hotkey_label(*s)}"
                                      for (m, v), a in shadow_cfg.items()
                                      for s in [(m, v)]))
                    self.register_failed.emit(",".join(shadow_cfg.values()))
            except Exception:
                kbd_hook = 0

        write("热键注册: " + (", ".join(
            f"{a}={hotkey_label(*specs[a])}" for a in wanted_reg) or "无"))
        if shadow_cfg:
            # 单独记一行:这一行存在 = 这些键原程序照常收到,本工具同时触发
            write("键盘旁听(和原功能同时生效): " + ", ".join(
                f"{a}={hotkey_label(m, v)}" for (m, v), a in shadow_cfg.items()))
        if mouse_map:
            # 单独记一行:不然日志里只有键盘键,"侧键没反应"就无从查起
            write("鼠标侧键: " + ", ".join(
                f"{a}={hotkey_label(0, vk)}" for vk, a in mouse_map.items()))
        if mouse_conflicts:
            write("侧键被多个动作抢用(后配置的生效): " + ", ".join(mouse_conflicts))
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
            if kbd_hook:
                UnhookWindowsHookEx(kbd_hook)
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
