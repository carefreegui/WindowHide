"""hotkey_edit.py - 全局快捷键捕获按钮。

点击进入捕获态,下一次按键即为新快捷键。

这个文件里的每一条约束都是用真实事故换来的:
- **捕获态必须吃掉所有按键**。不 accept 的话事件会继续往上冒泡:Enter
  会点到对话框的默认按钮「保存」(半截热键被存进配置、窗口直接关掉),
  Esc 会 reject 掉整个设置窗口 —— 用户想录 Shift+Enter,结果 Enter 被当
  成了"确定",Shift+Enter 真就成了老板键。
- **Esc 只取消录入,不关对话框**。用户按两下 Esc 就退出设置,还以为
  "取消"能救回来,其实那一刻捕获态已经漏了一地。
- **纯 Shift 组合不再拒绝,只是提醒**。Shift+Enter 是聊天换行、Shift+字母
  是大写、Shift+数字是符号 —— 这类组合**不会**交给 RegisterHotKey(那是系统
  层截获,原程序就再也收不到),而是走键盘钩子旁听:照样换行,同时也触发
  本工具的动作。用户明确说过要的就是这个("shift 加 enter 特别好操作,
  同时触发也可以,提醒一下就行")。只有"单个字母/数字/空格"仍然拒收 ——
  那不是"随意",是每次打字都在触发。
- **鼠标侧键可以直接按**。它不需要也没有修饰符。侧键消息只投递给光标
  下的窗口,所以走钩子转发而不是 Qt 事件(见 core/hotkeys.py)。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QPushButton

from ..config import (HotkeySpec, MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN,
                      VK_XBUTTON1, VK_XBUTTON2)
from ..core.hotkeys import (begin_capture, capture_bus, end_capture,
                            hotkey_label)

# 键盘区域(非数字/字母)的显示名。数字和字母在下面按范围批量补。
_KEY_NAMES = {
    Qt.Key_Backspace: "Backspace", Qt.Key_Tab: "Tab", Qt.Key_Return: "Enter",
    Qt.Key_Escape: "Esc", Qt.Key_Space: "Space", Qt.Key_Insert: "Insert",
    Qt.Key_Delete: "Delete", Qt.Key_Home: "Home", Qt.Key_End: "End",
    Qt.Key_PageUp: "PageUp", Qt.Key_PageDown: "PageDown",
    Qt.Key_Left: "Left", Qt.Key_Right: "Right", Qt.Key_Up: "Up", Qt.Key_Down: "Down",
}
for _i in range(Qt.Key_F1, Qt.Key_F24 + 1):
    _KEY_NAMES[_i] = f"F{_i - Qt.Key_F1 + 1}"
for _i in range(Qt.Key_0, Qt.Key_9 + 1):
    _KEY_NAMES[_i] = chr(_i)
for _i in range(Qt.Key_A, Qt.Key_Z + 1):
    _KEY_NAMES[_i] = chr(_i)

# 纯修饰键:按下去只是"准备按组合",不是要设成热键
_MODIFIER_KEYS = frozenset({
    Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta,
    Qt.Key_AltGr, Qt.Key_CapsLock, Qt.Key_NumLock, Qt.Key_ScrollLock,
})

# OEM 键(Qt 键值 -> Windows 虚拟键码)。
#
# 这两个值域**不是**一回事:Qt 报的是字符的 Unicode-ish 键值(0x5B = '['),
# Windows 要的是 VK_OEM_*(0xDB = 左方括号)。之前没做这张表,用户按
# Ctrl+[ / Shift+{ / Ctrl+; 全都"没反应" —— 就是这个洞。
# 同一个物理键在 Shift 下 Qt 会给另一个键值(`Key_BraceLeft` 而不是
# `Key_BracketLeft`),所以每个物理键要收两条。
_OEM_VK = {
    Qt.Key_Semicolon: 0xBA, Qt.Key_Colon: 0xBA,        # ;:
    Qt.Key_Equal: 0xBB, Qt.Key_Plus: 0xBB,             # =+
    Qt.Key_Comma: 0xBC, Qt.Key_Less: 0xBC,             # ,<
    Qt.Key_Minus: 0xBD, Qt.Key_Underscore: 0xBD,       # -_
    Qt.Key_Period: 0xBE, Qt.Key_Greater: 0xBE,         # .>
    Qt.Key_Slash: 0xBF, Qt.Key_Question: 0xBF,         # /?
    Qt.Key_QuoteLeft: 0xC0, Qt.Key_AsciiTilde: 0xC0,   # `~
    Qt.Key_BracketLeft: 0xDB, Qt.Key_BraceLeft: 0xDB,  # [{
    Qt.Key_Backslash: 0xDC, Qt.Key_Bar: 0xDC,          # \|
    Qt.Key_BracketRight: 0xDD, Qt.Key_BraceRight: 0xDD,  # ]}
    Qt.Key_Apostrophe: 0xDE, Qt.Key_QuoteDbl: 0xDE,    # '"
}


def qt_event_to_spec(event: QKeyEvent) -> HotkeySpec | None:
    """把 Qt 按键事件转成 (mods, vk);按修饰键/映射不到返回 None。

    注意这里**不**过滤"纯 Shift"——那是调用方的事,因为要给出不同的提示
    ("不合法"和"这个组合太危险"对用户是两件事)。
    """
    mods = 0
    qm = event.modifiers()
    if qm & Qt.ControlModifier:
        mods |= MOD_CONTROL
    if qm & Qt.AltModifier:
        mods |= MOD_ALT
    if qm & Qt.ShiftModifier:
        mods |= MOD_SHIFT
    if qm & Qt.MetaModifier:
        mods |= MOD_WIN
    key = event.key()
    if key in _MODIFIER_KEYS:
        return None
    vk = _qtkey_to_vk(key)
    if not vk:
        return None
    if not mods and not _bare_key_ok(vk):
        # 单个字母/数字/空格/Enter 当全局热键 = 每次打字都藏窗口,
        # 那不是"随意",是没法用。继续等用户补一个修饰键。
        return None
    return HotkeySpec(mods=mods, vk=vk)


def _bare_key_ok(vk: int) -> bool:
    """不带任何修饰键时,哪些键可以单独当热键。

    只放行"平时没有输入语义"的键:功能键区 F1-F24,以及 Pause /
    ScrollLock / Insert。这类键单独按下去不会往任何输入框里塞字符,
    也就不会和正常打字打架。
    """
    if 0x70 <= vk <= 0x87:          # F1 - F24
        return True
    return vk in (0x13, 0x91, 0x2D)  # Pause / ScrollLock / Insert


def _qtkey_to_vk(key: int) -> int:
    if key in _KEY_NAMES and 0x30 <= key <= 0x5A:
        return key  # 0-9/A-Z 的 Qt 键值与 VK 一致
    # F1-F24
    if Qt.Key_F1 <= key <= Qt.Key_F24:
        return 0x70 + (key - Qt.Key_F1)
    special = {
        Qt.Key_Space: 0x20, Qt.Key_Backspace: 0x08, Qt.Key_Tab: 0x09,
        Qt.Key_Return: 0x0D, Qt.Key_Enter: 0x0D, Qt.Key_Escape: 0x1B,
        Qt.Key_Insert: 0x2D, Qt.Key_Delete: 0x2E,
        Qt.Key_Home: 0x24, Qt.Key_End: 0x23,
        Qt.Key_PageUp: 0x21, Qt.Key_PageDown: 0x22,
        Qt.Key_Left: 0x25, Qt.Key_Up: 0x26, Qt.Key_Right: 0x27, Qt.Key_Down: 0x28,
    }
    if key in special:
        return special[key]
    return _OEM_VK.get(key, 0)


class HotkeyCaptureButton(QPushButton):
    """点一下进入录入,下一次按键即为新快捷键。

    `mouse_only=True` 时只收鼠标侧键(用于"附加侧键"那一格),键盘按下
    不理会;Delete 键清空(侧键槽必须能清空,否则设上了就撤不掉)。
    """
    captured = Signal(object)   # HotkeySpec

    def __init__(self, spec: HotkeySpec, mouse_only: bool = False, parent=None):
        super().__init__(parent)
        self._spec = spec
        self._mouse_only = mouse_only
        self._capturing = False
        self._refresh_text()
        self.clicked.connect(self._start_capture)
        tooltip = ("点一下,然后按鼠标侧键。\n按 Delete 可清空,按 Esc 取消。"
                   if mouse_only else
                   "点一下进入录入,然后按键盘组合键(必须带 Ctrl / Alt / Win)。\n"
                   "按 Esc 取消录入 —— 不会关掉设置窗口。\n"
                   "手在键盘上时用它;想在鼠标上也能触发,就给右边那一格也设个侧键。")
        self.setToolTip(tooltip)
        # 侧键走钩子转发,而不是等 Qt 的鼠标事件:侧键消息只投递给**光标下
        # 的窗口**,捕获时用户的手还在鼠标上、光标未必落在这个按钮里,Qt 就
        # 收不到(实测 QPushButton 完全收不到 BackButton)。
        capture_bus.captured.connect(self._on_mouse_key)
        # 别的按钮开始录入时,自己先退出去 —— 同一时刻只该有一个人在等按键
        capture_bus.owner_changed.connect(self._on_other_capture)

    def spec(self) -> HotkeySpec:
        return self._spec

    def is_capturing(self) -> bool:
        return self._capturing

    # ---------------------------------------------------------------- 内部
    def _idle_text(self) -> str:
        if self._mouse_only:
            return "点此设置侧键"
        return "点此设置快捷键"

    def _refresh_text(self) -> None:
        if not self._spec.vk:
            self.setText("未设置" if not self._mouse_only else "未设置侧键")
            return
        self.setText(hotkey_label(self._spec.mods, self._spec.vk))

    def _start_capture(self) -> None:
        if self._capturing:
            # 已经在这格的录入态里:再点一下 = 取消。
            # 曾经这里直接 return,用户点了没反应,就以为"这个按钮坏了、
            # 点不了" —— 而实际上他是在等一个永远不会来的响应。
            self._end_capture()
            return
        self._capturing = True
        # 登记到账本。用 id(self) 而不是 "捕获态" 一个全局布尔:设置页有
        # 6 个这种按钮,布尔只有一个格子,谁最后写谁算 —— 用户点 A 再点 B
        # 就会看到两个按钮都显示"请按键",而实际只有一个生效。
        begin_capture(id(self))
        capture_bus.owner_changed.emit(id(self))
        self.setText("请按侧键…(Esc 取消)" if self._mouse_only
                     else "请按键盘组合键…(Esc 取消)")
        # 必须显式拿焦点:没焦点的话 keyPressEvent 根本不来,用户按的键
        # 会被对话框当成快捷键处理(Enter 直接点掉「保存」)
        self.setFocus(Qt.OtherFocusReason)

    def _end_capture(self, keep_text: bool = False) -> None:
        """退出录入态。**必须**顺带把文本刷回去 —— 否则按钮会一直挂着
        "请按…"的提示,看着像卡住了。"""
        self._capturing = False
        end_capture(id(self))
        if not keep_text:
            self._refresh_text()

    def _accept(self, spec: HotkeySpec) -> None:
        self._spec = spec
        self._end_capture()          # 内部会按新值刷新文本
        self.captured.emit(spec)

    def _on_other_capture(self, owner: int) -> None:
        if owner != id(self) and self._capturing:
            self._end_capture()

    def _on_mouse_key(self, vk: int) -> None:
        # 键盘槽不认侧键:两格各管一种,误按侧键时不该把它覆盖成鼠标键
        if self._capturing and self._mouse_only:
            self._accept(HotkeySpec(mods=0, vk=vk))

    # ---------------------------------------------------------------- 事件
    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._capturing:
            super().keyPressEvent(event)
            return

        # 🔴 第一件事:吃掉事件。不 accept 的话它会继续冒泡到 QDialog ——
        # Enter 触发默认按钮「保存」(半截热键被写进配置,窗口还关了),
        # Esc 触发 reject(整个设置消失)。这两条都真实发生过。
        event.accept()
        key = event.key()

        if key == Qt.Key_Escape:
            self._end_capture()      # 只取消这一格的录入,窗口留着
            return
        if key in _MODIFIER_KEYS:
            return                   # 光按 Shift/Ctrl,继续等
        if self._mouse_only:
            if key in (Qt.Key_Delete, Qt.Key_Backspace):
                self._accept(HotkeySpec(mods=0, vk=0))   # 清空
            return                   # 侧键槽里敲键盘不理会,继续等

        spec = qt_event_to_spec(event)
        if spec is None:
            # 映射不到的键(多媒体键之类),或者"单个字母/数字"这种真不能用
            # 的组合。静默继续等,不要弹提示吓人
            return
        # 纯 Shift / 单个功能键:照收。它会被安排走"旁听"模式(原功能照常 +
        # 同时触发),设置页会实时提示这件事 —— 不拒绝,只是说清楚。
        self._accept(spec)

    def mousePressEvent(self, event) -> None:
        # 兜底:光标正好停在按钮上时 Qt 自己也收得到侧键,不必等钩子转发。
        # 两边都来也没事 —— 第二次进来时 _capturing 已是 False,直接忽略。
        if self._capturing and self._mouse_only:
            vk = {Qt.BackButton: VK_XBUTTON1,
                  Qt.ForwardButton: VK_XBUTTON2}.get(event.button())
            if vk:
                # 先让 QPushButton 走完自己的流程:它内部要维护 pressed 状态
                # 和鼠标的隐式捕获。跳过 super 会让 Qt 认为"没按下",release
                # 时状态对不上,极端情况下留下一个系统级捕获 —— 那正是
                # "鼠标点什么都没反应、键盘正常"的成因,不能赌。
                super().mousePressEvent(event)
                self._on_mouse_key(vk)
                return
        super().mousePressEvent(event)

    def focusOutEvent(self, event) -> None:
        if self._capturing:
            self._end_capture()
        super().focusOutEvent(event)
