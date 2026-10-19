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


def _install_excepthook() -> None:
    """全局异常兜底:写日志,别让异常静默消失。

    PySide6 里槽函数抛出的异常会被 Qt 吞掉(打包成无控制台运行后连
    提示都没有),用户只会看到"点了没反应"。装了钩子至少日志里留痕,
    反馈 bug 时能拿到现场。
    """
    import threading
    import traceback

    from .log import write

    def _hook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb)).strip()
        write("未捕获异常:\n" + text)
        sys.__excepthook__(exc_type, exc, tb)

    def _thread_hook(args):
        text = "".join(traceback.format_exception(
            args.exc_type, args.exc_value, args.exc_traceback)).strip()
        write(f"线程 {getattr(args.thread, 'name', '?')} 未捕获异常:\n" + text)

    sys.excepthook = _hook
    threading.excepthook = _thread_hook


def main() -> int:
    _install_excepthook()
    _fix_qt_locale()
    app = QApplication(sys.argv)
    app.setQuitOnLastWindowClosed(False)
    app.setApplicationName(APP_DISPLAY_NAME)

    if _already_running() and "--elevated" not in sys.argv:
        # --elevated 是"以管理员身份重启"带过来的:此刻旧实例正在退出,
        # 互斥体还没释放,不能因为看到旧实例就把新进程退掉。
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
