"""tray.py - 系统托盘图标与菜单。"""
from __future__ import annotations

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .icon import make_icon


class SystemTray(QSystemTrayIcon):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._normal_icon = make_icon(64, hidden=False)
        self._hidden_icon = make_icon(64, hidden=True)
        self.setIcon(self._normal_icon)
        self.setToolTip("窗口隐藏工具")

        self.menu = QMenu()
        self.act_toggle = QAction("隐藏目标 / 恢复全部", self.menu)
        self.act_hide_fg = QAction("隐藏当前前台窗口", self.menu)
        self.act_show = QAction("显示主界面", self.menu)
        self.act_quit = QAction("退出", self.menu)
        self.menu.addAction(self.act_toggle)
        self.menu.addAction(self.act_hide_fg)
        self.menu.addSeparator()
        self.menu.addAction(self.act_show)
        self.menu.addSeparator()
        self.menu.addAction(self.act_quit)
        self.setContextMenu(self.menu)

    def set_hiding(self, hiding: bool) -> None:
        self.setIcon(self._hidden_icon if hiding else self._normal_icon)

    def set_state_text(self, text: str) -> None:
        self.setToolTip(f"窗口隐藏工具 - {text}")
