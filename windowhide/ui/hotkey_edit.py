"""hotkey_edit.py - 全局快捷键捕获按钮。

点击进入捕获态,下一次按键即为新快捷键:
- 键盘组合必须带至少一个修饰键(单键会被全局占用,危险);
- 鼠标侧键可以直接按 —— 它不需要也没有修饰符。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QPushButton

from ..config import (HotkeySpec, MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN,
                      VK_XBUTTON1, VK_XBUTTON2)
from ..core.hotkeys import capture_bus, hotkey_label, set_capturing

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


def qt_event_to_spec(event: QKeyEvent) -> HotkeySpec | None:
    """把 Qt 按键事件转成 (mods, vk);无修饰键返回 None。"""
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
    if key in (Qt.Key_Control, Qt.Key_Alt, Qt.Key_Shift, Qt.Key_Meta,
               Qt.Key_AltGr, Qt.Key_CapsLock, Qt.Key_NumLock):
        return None
    vk = _qtkey_to_vk(key)
    if not mods or not vk:
        return None
    return HotkeySpec(mods=mods, vk=vk)


def _qtkey_to_vk(key: int) -> int:
    if key in _KEY_NAMES and 0x30 <= key <= 0x5A:
        return key  # 0-9/A-Z 的 Qt 键值与 VK 一致
    # F1-F24
    if Qt.Key_F1 <= key <= Qt.Key_F24:
        return 0x70 + (key - Qt.Key_F1)
    special = {
        Qt.Key_Space: 0x20, Qt.Key_Backspace: 0x08, Qt.Key_Tab: 0x09,
        Qt.Key_Return: 0x0D, Qt.Key_Escape: 0x1B, Qt.Key_Insert: 0x2D,
        Qt.Key_Delete: 0x2E, Qt.Key_Home: 0x24, Qt.Key_End: 0x23,
        Qt.Key_PageUp: 0x21, Qt.Key_PageDown: 0x22,
        Qt.Key_Left: 0x25, Qt.Key_Up: 0x26, Qt.Key_Right: 0x27, Qt.Key_Down: 0x28,
    }
    return special.get(key, 0)


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
        if mouse_only:
            self.setToolTip("点一下,然后按鼠标侧键。\n按 Delete 可清空。")
        # 侧键走钩子转发,而不是等 Qt 的鼠标事件:侧键消息只投递给**光标下
        # 的窗口**,捕获时用户的手还在鼠标上、光标未必落在这个按钮里,Qt 就
        # 收不到(实测 QPushButton 完全收不到 BackButton)。
        capture_bus.captured.connect(self._on_mouse_key)

    def spec(self) -> HotkeySpec:
        return self._spec

    def _refresh_text(self) -> None:
        if not self._spec.vk:
            self.setText("未设置")
            return
        self.setText(hotkey_label(self._spec.mods, self._spec.vk))

    def _start_capture(self) -> None:
        self._capturing = True
        # 关键:让钩子在这段时间**放行**侧键。否则用户把侧键从 A 改成 B
        # 时,刚按下去就被自己的钩子吃掉,永远改不了。
        set_capturing(True)
        self.setText("请按侧键…(Delete 清空)" if self._mouse_only
                     else "请按键盘组合键…(Esc 取消)")
        self.setFocus()

    def _end_capture(self) -> None:
        """退出录入态。**必须**顺带把文本刷回去 —— 否则按钮会一直挂着
        "请按…"的提示,看着像卡住了。"""
        self._capturing = False
        set_capturing(False)
        self._refresh_text()

    def _accept(self, spec: HotkeySpec) -> None:
        self._spec = spec
        self._end_capture()          # 内部会按新值刷新文本
        self.captured.emit(spec)

    def _on_mouse_key(self, vk: int) -> None:
        # 键盘槽不认侧键:两格各管一种,误按侧键时不该把它覆盖成鼠标键
        if self._capturing and self._mouse_only:
            self._accept(HotkeySpec(mods=0, vk=vk))

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._capturing:
            super().keyPressEvent(event)
            return
        key = event.key()
        if key == Qt.Key_Escape:
            self._end_capture()
            return
        if self._mouse_only:
            if key in (Qt.Key_Delete, Qt.Key_Backspace):
                self._accept(HotkeySpec(mods=0, vk=0))   # 清空
            return          # 侧键槽里敲键盘不理会,继续等
        spec = qt_event_to_spec(event)
        if spec:
            self._accept(spec)
        # 非法组合(无修饰键)不退出捕获,继续等

    def mousePressEvent(self, event) -> None:
        # 兜底:光标正好停在按钮上时 Qt 自己也收得到侧键,不必等钩子转发。
        # 两边都来也没事 —— 第二次进来时 _capturing 已是 False,直接忽略。
        if self._capturing and self._mouse_only:
            vk = {Qt.BackButton: VK_XBUTTON1,
                  Qt.ForwardButton: VK_XBUTTON2}.get(event.button())
            if vk:
                event.accept()
                self._on_mouse_key(vk)
                return
        super().mousePressEvent(event)

    def focusOutEvent(self, event) -> None:
        if self._capturing:
            self._end_capture()
        super().focusOutEvent(event)
