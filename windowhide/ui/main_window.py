"""main_window.py - 主窗口。"""
from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QAction
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPushButton, QStatusBar, QVBoxLayout, QWidget,
)

from ..config import Config
from ..core import win32
from ..core.classify import TYPE_LABELS, classify_window
from ..core.hide import HideController
from ..core.hotkeys import HotkeyManager
from ..core.targets import TargetRule, resolve_target_hwnds
from ..core.win32 import WindowInfo
from .settings_dialog import SettingsDialog
from .tray import SystemTray
from .window_list import WindowTable

SHOW_ME_MSG = "WindowHideShowMeMessage"


class MainWindow(QMainWindow):
    def __init__(self, config: Config, controller: HideController,
                 hotkeys: HotkeyManager, tray: SystemTray):
        super().__init__()
        self._config = config
        self._controller = controller
        self._hotkeys = hotkeys
        self._tray = tray
        self._windows: list[WindowInfo] = []

        self.setWindowTitle("窗口隐藏工具")
        self.resize(980, 620)
        self._build_ui()
        self._wire()

        self._refresh_timer = QTimer(self)
        self._refresh_timer.setInterval(1500)
        self._refresh_timer.timeout.connect(self.refresh)
        self._refresh_timer.start()

        self.refresh()
        self._update_status()

    # ---------------------------------------------------------------- UI 搭建
    def _build_ui(self) -> None:
        central = QWidget()
        self.setCentralWidget(central)
        v = QVBoxLayout(central)
        v.setContentsMargins(12, 10, 12, 10)
        v.setSpacing(8)

        # ---- 顶部工具行 ----
        top = QHBoxLayout()
        self._search = QLineEdit()
        self._search.setPlaceholderText("搜索窗口标题 / 进程名…")
        self._search.setClearButtonEnabled(True)
        self._search.setMaximumWidth(260)
        top.addWidget(self._search)

        self._type_filter = QComboBox()
        self._type_filter.addItem("全部类型", "")
        for key, label in TYPE_LABELS.items():
            self._type_filter.addItem(label, key)
        top.addWidget(self._type_filter)

        top.addStretch(1)

        self._btn_refresh = QPushButton("刷新")
        self._btn_hide = QPushButton("隐藏勾选")
        self._btn_hide.setDefault(True)
        self._btn_restore = QPushButton("恢复全部")
        self._btn_toggle = QPushButton("切换")
        self._btn_save_target = QPushButton("保存为目标")
        self._btn_settings = QPushButton("设置")
        for b in (self._btn_refresh, self._btn_hide, self._btn_restore,
                  self._btn_toggle, self._btn_save_target, self._btn_settings):
            top.addWidget(b)
        v.addLayout(top)

        # ---- 窗口表格 ----
        self._table = WindowTable()
        v.addWidget(self._table, 1)

        # ---- 底部行 ----
        bottom = QHBoxLayout()
        self._btn_all = QPushButton("全选")
        self._btn_invert = QPushButton("反选")
        self._btn_fg = QPushButton("隐藏前台窗口")
        bottom.addWidget(self._btn_all)
        bottom.addWidget(self._btn_invert)
        bottom.addStretch(1)
        bottom.addWidget(self._btn_fg)
        v.addLayout(bottom)

        self.setStatusBar(QStatusBar())

    def _wire(self) -> None:
        self._btn_refresh.clicked.connect(self.refresh)
        self._btn_hide.clicked.connect(self._hide_checked)
        self._btn_restore.clicked.connect(self._restore_all)
        self._btn_toggle.clicked.connect(self._toggle_targets)
        self._btn_save_target.clicked.connect(self._save_targets)
        self._btn_settings.clicked.connect(self._open_settings)
        self._btn_all.clicked.connect(self._table.set_all_checked)
        self._btn_invert.clicked.connect(self._table.invert_checked)
        self._btn_fg.clicked.connect(self._hide_foreground)
        self._search.textChanged.connect(self._apply_filter)
        self._type_filter.currentIndexChanged.connect(self._apply_filter)
        self._table.row_activated.connect(self._toggle_hwnd)

        self._controller.state_changed.connect(self.refresh)
        self._controller.action_done.connect(self._on_action)
        self._hotkeys.triggered.connect(self._on_hotkey)
        self._hotkeys.register_failed.connect(self._on_hotkey_failed)

        self._tray.act_toggle.triggered.connect(self._toggle_targets)
        self._tray.act_hide_fg.triggered.connect(self._hide_foreground)
        self._tray.act_show.triggered.connect(self._show_window)
        self._tray.act_quit.triggered.connect(self._quit)
        self._tray.activated.connect(self._on_tray_activated)

    # ---------------------------------------------------------------- 数据
    def refresh(self) -> None:
        our_pid = win32.get_window_pid(int(self.winId()))
        hidden = self._controller.hidden_hwnds()
        try:
            windows = win32.enum_top_level_windows()
        except Exception:
            windows = []
        # 可见窗口 + 被本工具隐藏的窗口(后者要在列表里显示"已隐藏")
        self._windows = [w for w in windows
                         if w.pid != our_pid
                         and (w.visible or w.hwnd in hidden)
                         and w.title]
        for w in self._windows:
            w.process_name = w.process_name or win32.process_image_path(w.pid).rsplit("\\", 1)[-1]
            if not w.process_path:
                w.process_path = win32.process_image_path(w.pid)
        self._apply_filter()
        self._update_status()

    def _filtered_windows(self) -> list[WindowInfo]:
        text = self._search.text().strip().lower()
        tkey = self._type_filter.currentData()
        out = []
        for w in self._windows:
            if text and text not in (w.title or "").lower() \
                    and text not in (w.process_name or "").lower():
                continue
            if tkey and classify_window(w) != tkey:
                continue
            out.append(w)
        return out

    def _apply_filter(self) -> None:
        our_pid = win32.get_window_pid(int(self.winId()))
        self._table.refresh(self._filtered_windows(),
                            self._controller.hidden_hwnds(), our_pid)

    def _update_status(self) -> None:
        n_proc, n_win = self._controller.hidden_summary()
        if n_proc:
            self._tray.set_hiding(True)
            self._tray.set_state_text(f"已隐藏 {n_win} 个窗口 / {n_proc} 个程序")
        else:
            self._tray.set_hiding(False)
            self._tray.set_state_text("运行中")
        if n_proc:
            self.statusBar().showMessage(
                f"后台运行中:已隐藏 {n_win} 个窗口({n_proc} 个程序),程序未关闭")
        else:
            self.statusBar().showMessage("就绪:勾选窗口后点击「隐藏勾选」,或按全局快捷键")

    # ---------------------------------------------------------------- 动作
    def _current_targets(self) -> list[int]:
        checked = self._table.checked_hwnds()
        if checked:
            return checked
        selected = self._table.selected_hwnds()
        if selected:
            return selected
        # 没有勾选/选中:用已保存的规则(含被本工具隐藏的窗口)
        matched = resolve_target_hwnds(
            self._config.targets, self._windows,
            include_hidden=self._controller.hidden_hwnds())
        return [w.hwnd for w in matched]

    def _hide_checked(self) -> None:
        hwnds = self._table.checked_hwnds() or self._table.selected_hwnds()
        if not hwnds:
            QMessageBox.information(self, "提示", "请先勾选要隐藏的窗口。")
            return
        self._controller.hide_hwnds(hwnds)

    def _restore_all(self) -> None:
        if not self._controller.hidden_pids():
            QMessageBox.information(self, "提示", "当前没有已隐藏的窗口。")
            return
        self._controller.restore_all()

    def _toggle_targets(self) -> None:
        self._controller.toggle(self._current_targets())

    def _toggle_hwnd(self, hwnd: int) -> None:
        if self._controller.is_hwnd_hidden(hwnd):
            self._controller.restore_hwnd(hwnd)
        else:
            self._controller.hide_hwnds([hwnd])

    def _hide_foreground(self) -> None:
        self._controller.hide_foreground()

    def _save_targets(self) -> None:
        hwnds = self._table.checked_hwnds()
        if not hwnds:
            QMessageBox.information(self, "提示", "请先勾选窗口,再保存为目标规则。")
            return
        by_hwnd = {w.hwnd: w for w in self._windows}
        added = 0
        for hwnd in hwnds:
            w = by_hwnd.get(hwnd)
            if not w:
                continue
            rule = TargetRule.from_window(w)
            if rule not in self._config.targets:
                self._config.targets.append(rule)
                added += 1
        self._config.save()
        QMessageBox.information(self, "已保存", f"新增 {added} 条目标规则。")

    def _open_settings(self) -> None:
        dlg = SettingsDialog(self._config, self)
        if dlg.exec():
            dlg.apply_to(self._config)
            self._config.save()
            self._apply_hotkeys()
            self._config.apply_autostart()

    def _apply_hotkeys(self) -> None:
        c = self._config
        specs = {}
        if c.hotkey_mode == "split":
            specs["hide"] = (c.hotkey_hide.mods, c.hotkey_hide.vk)
            specs["restore"] = (c.hotkey_restore.mods, c.hotkey_restore.vk)
        else:
            specs["toggle"] = (c.hotkey_toggle.mods, c.hotkey_toggle.vk)
        specs["foreground"] = (c.hotkey_foreground.mods, c.hotkey_foreground.vk)
        self._hotkeys.set_hotkeys(specs)

    # ---------------------------------------------------------------- 信号
    def _on_hotkey(self, action: str) -> None:
        if action == "toggle":
            self._toggle_targets()
        elif action == "hide":
            hwnds = self._current_targets()
            if hwnds:
                self._controller.hide_hwnds(hwnds)
        elif action == "restore":
            self._controller.restore_all()
        elif action == "foreground":
            self._controller.hide_foreground()

    def _on_hotkey_failed(self, actions: str) -> None:
        self.statusBar().showMessage(
            f"警告:以下热键注册失败(可能被其它软件占用),请在设置中更换组合: {actions}")

    def _on_action(self, action: str, count: int) -> None:
        self.refresh()
        from ..log import write
        if action == "hidden":
            write(f"隐藏 {count} 个程序(转入后台)")
            if self._config.show_balloon:
                self._tray.showMessage("已隐藏", f"{count} 个程序已转入后台,任务栏与托盘图标已隐藏")
        elif action == "restored":
            write(f"恢复 {count} 个程序")
            if self._config.show_balloon:
                self._tray.showMessage("已恢复", f"{count} 个程序已恢复显示")

    def _on_tray_activated(self, reason) -> None:
        if reason == QSystemTrayIcon.Trigger:      # 左键点击 = 切换
            self._toggle_targets()
        elif reason == QSystemTrayIcon.DoubleClick:
            self._show_window()

    def _show_window(self) -> None:
        self.showNormal()
        self.raise_()
        self.activateWindow()

    def _quit(self) -> None:
        self._controller.restore_all()
        self._hotkeys.stop()
        self._tray.hide()
        from PySide6.QtWidgets import QApplication
        QApplication.quit()

    # ---------------------------------------------------------------- 事件
    def closeEvent(self, event) -> None:
        if self._config.minimize_to_tray:
            event.ignore()
            self.hide()
            self._tray.showMessage("窗口隐藏工具", "已最小化到托盘,点击图标可切换隐藏状态")
        else:
            self._quit()
            event.accept()

    def nativeEvent(self, eventType, message):
        # 单实例唤醒消息
        try:
            if eventType == "windows_generic_MSG":
                import ctypes
                msg = ctypes.wintypes.MSG.from_address(int(message))
                if msg.message == self._show_me_msg_id:
                    self._show_window()
                    return True, 0
        except Exception:
            pass
        return super().nativeEvent(eventType, message)

    def register_show_me(self) -> None:
        self._show_me_msg_id = win32.RegisterWindowMessageW(SHOW_ME_MSG)
