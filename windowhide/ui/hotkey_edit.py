"""hotkey_edit.py - 全局快捷键捕获按钮。

点击进入捕获态,下一次按键(必须带至少一个修饰键)即为新快捷键。
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QPushButton

from ..config import HotkeySpec, MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN
from ..core.hotkeys import hotkey_label

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
    captured = Signal(object)   # HotkeySpec

    def __init__(self, spec: HotkeySpec, parent=None):
        super().__init__(parent)
        self._spec = spec
        self._capturing = False
        self._refresh_text()
        self.clicked.connect(self._start_capture)

    def spec(self) -> HotkeySpec:
        return self._spec

    def _refresh_text(self) -> None:
        self.setText(hotkey_label(self._spec.mods, self._spec.vk))

    def _start_capture(self) -> None:
        self._capturing = True
        self.setText("请按下组合键… (Esc 取消)")
        self.setFocus()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if not self._capturing:
            super().keyPressEvent(event)
            return
        if event.key() == Qt.Key_Escape:
            self._capturing = False
            self._refresh_text()
            return
        spec = qt_event_to_spec(event)
        if spec:
            self._capturing = False
            self._spec = spec
            self._refresh_text()
            self.captured.emit(spec)
        # 非法组合(无修饰键)不退出捕获,继续等

    def focusOutEvent(self, event) -> None:
        if self._capturing:
            self._capturing = False
            self._refresh_text()
        super().focusOutEvent(event)
