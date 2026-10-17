"""app.py - 应用入口:QLocale 修复、单实例、装配各模块。"""
from __future__ import annotations

import ctypes
import sys
from ctypes import wintypes

from PySide6.QtCore import QLocale
from PySide6.QtWidgets import QApplication, QMessageBox

from . import APP_DISPLAY_NAME
from .config import Config
from .core.hide import HideController
from .core.hotkeys import HotkeyManager
from .ui.main_window import SHOW_ME_MSG, MainWindow
from .ui.tray import SystemTray

MUTEX_NAME = "WindowHide_SingleInstance_Mutex"


def _fix_qt_locale() -> None:
    """Qt 6.11 在中文环境下默认 hanidec 数字系统,会导致 SpinBox 等
    控件里的数字显示成苏州码子。必须在 QApplication 构造前调用。"""
    loc = QLocale("zh-Hans-CN-u-nu-latn")
    QLocale.setDefault(loc)


def _already_running() -> bool:
    kernel32 = ctypes.windll.kernel32
    mutex = kernel32.CreateMutexW(None, False, MUTEX_NAME)
    if not mutex:
        return True
    return kernel32.GetLastError() == 183  # ERROR_ALREADY_EXISTS


def _wake_existing() -> None:
    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, "窗口隐藏工具")
    if not hwnd:
        return
    msg = user32.RegisterWindowMessageW(SHOW_ME_MSG)
    if msg:
        user32.PostMessageW(hwnd, msg, 0, 0)


def main() -> int:
    _fix_qt_locale()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_DISPLAY_NAME)

    if _already_running():
        _wake_existing()
        return 0

    config = Config.load()

    controller = HideController(config)
    hotkeys = HotkeyManager()
    tray = SystemTray()
    tray.show()

    window = MainWindow(config, controller, hotkeys, tray)
    window.register_show_me()
    window._apply_hotkeys()
    window.show()

    if config.autostart:
        config.apply_autostart()

    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
