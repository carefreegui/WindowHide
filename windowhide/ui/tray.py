"""tray.py - 系统托盘图标与菜单。"""
from __future__ import annotations

import ctypes

from PySide6.QtGui import QAction, QIcon
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from .icon import load_icon


class SystemTray(QSystemTrayIcon):
    def __init__(self, parent=None):
        super().__init__(parent)
        self._normal_icon = load_icon(hidden=False)
        self._hidden_icon = load_icon(hidden=True)
        self.setIcon(self._normal_icon)
        self.setToolTip("窗口隐藏工具")

        # 菜单顺序按"用得最多的在上面":切换 → 场景 → 唤回界面 → 退出。
        # 场景做成子菜单:场景数量会随用户增长,平铺会把菜单撑爆。
        self.menu = QMenu()
        self.act_toggle = QAction("隐藏目标 / 恢复全部", self.menu)
        self.profile_menu = QMenu("场景", self.menu)
        self.act_show = QAction("显示主界面", self.menu)
        self.act_hide_self = QAction("最小化到托盘", self.menu)
        # 目标程序以管理员权限运行时,普通权限的进程碰不了它的窗口(UIPI),
        # 只能提权重启本工具。已经提权就置灰。
        self.act_elevate = QAction("以管理员身份重启", self.menu)
        if ctypes.windll.shell32.IsUserAnAdmin():
            self.act_elevate.setText("已以管理员身份运行")
            self.act_elevate.setEnabled(False)
        self.act_quit = QAction("退出", self.menu)

        self.menu.addAction(self.act_toggle)
        self.menu.addMenu(self.profile_menu)
        self.menu.addSeparator()
        self.menu.addAction(self.act_show)
        self.menu.addAction(self.act_hide_self)
        self.menu.addSeparator()
        self.menu.addAction(self.act_elevate)
        self.menu.addSeparator()
        self.menu.addAction(self.act_quit)
        self.setContextMenu(self.menu)

    def set_hiding(self, hiding: bool) -> None:
        self.setIcon(self._hidden_icon if hiding else self._normal_icon)

    def set_state_text(self, text: str) -> None:
        self.setToolTip(f"窗口隐藏工具 - {text}")
