"""tests/tray_click_test.py - 托盘点击切换链路回归测试。

背景:main_window 曾漏导入 QSystemTrayIcon,点击托盘图标时
_on_tray_activated 抛 NameError(PySide6 槽函数异常只进 stderr,
界面毫无反应),导致"点托盘图标切换隐藏/恢复"完全失效。
本测试在进程内搭建真实 MainWindow,向 tray.activated 发送
Trigger(左键点击)信号,验证隐藏 -> 恢复链路。
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtWidgets import QApplication, QSystemTrayIcon  # noqa: E402

from windowhide.config import Config  # noqa: E402
from windowhide.core import win32  # noqa: E402
from windowhide.core.hide import HideController  # noqa: E402
from windowhide.core.hotkeys import HotkeyManager  # noqa: E402
from windowhide.core.targets import TargetRule  # noqa: E402
from windowhide.ui.main_window import MainWindow  # noqa: E402
from windowhide.ui.tray import SystemTray  # noqa: E402

TRIGGER = QSystemTrayIcon.ActivationReason.Trigger  # 左键点击


def find_dummy():
    for w in win32.enum_top_level_windows():
        if w.visible and w.title.startswith("WH-Test-Target-"):
            return w
    return None


def main() -> int:
    import faulthandler
    faulthandler.dump_traceback_later(25, exit=True)  # 25s 未结束则打印各线程栈并退出

    app = QApplication(sys.argv)

    config = Config()
    config.targets = [TargetRule(process="python.exe",
                                 window_class="WHDummyAppWnd",
                                 title_regex="", label="测试目标窗口")]
    controller = HideController(config)
    hotkeys = HotkeyManager()
    tray = SystemTray()
    win = MainWindow(config, controller, hotkeys, tray)
    win.show()

    print("[1] 启动 dummy_app ...")
    dummy = subprocess.Popen([sys.executable, str(ROOT / "tests" / "dummy_app.py")],
                             creationflags=subprocess.CREATE_NO_WINDOW)
    target = None
    for _ in range(50):
        target = find_dummy()
        if target:
            break
        app.processEvents()
        time.sleep(0.2)
    if not target:
        print("FAIL  dummy 窗口未出现")
        return 1
    print(f"    dummy hwnd={target.hwnd}")

    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        print(("PASS  " if cond else "FAIL  ") + name)
        ok = ok and cond

    # 第一次"点击托盘" -> 应隐藏目标
    print("[2] 模拟托盘左键点击(应隐藏) ...")
    win.refresh()   # dummy 启动于窗口列表刷新之后,先刷新再点击
    tray.activated.emit(TRIGGER)
    app.processEvents()
    time.sleep(0.3)
    hidden = not win32.IsWindowVisible(target.hwnd)
    alive = win32.process_is_alive(target.pid)
    check("点击托盘后目标窗口不可见", hidden)
    check("目标进程仍存活(后台化而非关闭)", alive)

    # 再次点击 -> 应恢复
    print("[3] 再次点击托盘(应恢复) ...")
    tray.activated.emit(TRIGGER)
    app.processEvents()
    time.sleep(0.3)
    check("再次点击后目标窗口恢复可见", win32.IsWindowVisible(target.hwnd))

    # 关窗口时的"已最小化到托盘"提示:必须有独立开关
    # (用户反馈:每次关都被弹一下,想关掉)
    print("[4] 关闭提示的独立开关 ...")
    calls: list = []
    tray.showMessage = lambda *a, **k: calls.append(a)
    config.show_minimize_hint = False
    win.close()
    check("关掉后不再弹提示", not calls)
    config.show_minimize_hint = True
    win.show()
    win.close()
    check("开关打开时弹且只弹一次", len(calls) == 1)

    print("[5] 清理 ...")
    controller.restore_all(wait=True)
    hotkeys.stop()
    tray.hide()
    win.close()
    win32.PostMessageW(target.hwnd, 0x0010, 0, 0)  # WM_CLOSE
    time.sleep(0.3)
    if find_dummy():
        dummy.kill()
    try:
        dummy.wait(timeout=3)
    except subprocess.TimeoutExpired:
        dummy.kill()

    print()
    print("全部通过" if ok else "存在失败项")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
