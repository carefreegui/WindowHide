"""tests/checkbox_test.py - 勾选行为回归测试。

背景(用户实测反馈):"一勾选就自动隐藏,勾选还会被取消,没法累积选择"。
根因有三:
  1. 定时器每 1.5s 重建表格时把所有勾选清空(setRowCount(0)+Unchecked)
  2. 复选框上快速点两下会触发 itemDoubleClicked -> row_activated ->
     立刻隐藏该窗口
  3. "隐藏勾选"是默认按钮,搜索框回车也会触发
本测试在进程内搭建真实 MainWindow 验证修复:
  - 勾选跨定时器刷新保留
  - 双击复选框列不隐藏窗口
  - 双击其他列仍可切换隐藏(功能不丢)
  - 消失的窗口其勾选被清理
"""
from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from PySide6.QtCore import Qt  # noqa: E402
from PySide6.QtWidgets import QApplication  # noqa: E402

from windowhide.config import Config  # noqa: E402
from windowhide.core import win32  # noqa: E402
from windowhide.core.hide import HideController  # noqa: E402
from windowhide.core.hotkeys import HotkeyManager  # noqa: E402
from windowhide.core.targets import TargetRule  # noqa: E402
from windowhide.ui.main_window import MainWindow  # noqa: E402
from windowhide.ui.tray import SystemTray  # noqa: E402
from windowhide.ui.window_list import COL_CHECK, COL_TITLE  # noqa: E402


def find_dummy():
    for w in win32.enum_top_level_windows():
        if w.visible and w.title.startswith("WH-Test-Target-"):
            return w
    return None


def main() -> int:
    app = QApplication(sys.argv)

    config = Config()
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
    win.refresh()
    print(f"    dummy hwnd={target.hwnd}")

    table = win._table
    row = table._hwnds.index(target.hwnd)

    ok = True

    def check(name: str, cond: bool) -> None:
        nonlocal ok
        print(("PASS  " if cond else "FAIL  ") + name)
        ok = ok and cond

    # 勾选(等价于用户点击复选框)
    print("[2] 勾选 dummy,跨刷新保留 ...")
    table.item(row, COL_CHECK).setCheckState(Qt.Checked)
    check("勾选后 checked_hwnds 包含 dummy", target.hwnd in table.checked_hwnds())

    time.sleep(2.0)   # 跨过 1.5s 定时器刷新
    app.processEvents()
    check("2 秒后(经过定时器刷新)勾选仍在", target.hwnd in table.checked_hwnds())

    # 双击复选框列 -> 不应隐藏
    print("[3] 双击复选框列(应只切换勾选,不隐藏) ...")
    row = table._hwnds.index(target.hwnd)
    table._on_double_click(table.item(row, COL_CHECK))
    app.processEvents()
    time.sleep(0.3)
    check("双击复选框后窗口仍可见", bool(win32.IsWindowVisible(target.hwnd)))
    # 双击把勾选取消掉了(复选框自身行为),重新勾上
    table.item(row, COL_CHECK).setCheckState(Qt.Checked)
    check("重新勾选后仍在", target.hwnd in table.checked_hwnds())

    # 双击标题列 -> 应隐藏(功能保留)
    print("[4] 双击标题列(应隐藏该窗口,功能保留) ...")
    row = table._hwnds.index(target.hwnd)
    table._on_double_click(table.item(row, COL_TITLE))
    app.processEvents()
    time.sleep(0.3)
    check("双击标题后窗口被隐藏", not win32.IsWindowVisible(target.hwnd))
    check("隐藏后进程仍存活", win32.process_is_alive(target.pid))

    # 再次双击恢复
    print("[5] 再次双击恢复 ...")
    row = table._hwnds.index(target.hwnd)
    table._on_double_click(table.item(row, COL_TITLE))
    app.processEvents()
    time.sleep(0.3)
    check("再次双击后窗口恢复可见", bool(win32.IsWindowVisible(target.hwnd)))

    # 全选按钮切换行为(曾经的 bug:clicked 的 bool 参数恒为 False,
    # 点"全选"实际执行 set_all_checked(False) = 全部取消)
    print("[5.5] 全选切换 / 反选 ...")
    n_enabled = sum(
        1 for r in range(table.rowCount())
        if table.item(r, COL_CHECK)
        and (table.item(r, COL_CHECK).flags() & Qt.ItemIsEnabled))
    table.toggle_all_checked()
    check(f"第一次点全选=全部选中({n_enabled} 个可用行)",
          n_enabled > 0 and len(table.checked_hwnds()) == n_enabled)
    table.toggle_all_checked()
    check("第二次点全选=全部取消", table.checked_hwnds() == [])
    # 反选保留:只勾一个 -> 反选后它被取消、其余被选上
    table.item(table._hwnds.index(target.hwnd), COL_CHECK).setCheckState(Qt.Checked)
    table.invert_checked()
    check("反选后 dummy 被取消(其余被选上)",
          target.hwnd not in table.checked_hwnds()
          and len(table.checked_hwnds()) == n_enabled - 1)
    table.toggle_all_checked()   # 清空,避免影响后续
    table.toggle_all_checked()
    check("收尾:全不选", table.checked_hwnds() == [])

    # 窗口消失后勾选被清理
    print("[6] 窗口消失后勾选清理 ...")
    table.item(table._hwnds.index(target.hwnd), COL_CHECK).setCheckState(Qt.Checked)
    win32.PostMessageW(target.hwnd, 0x0010, 0, 0)  # WM_CLOSE
    time.sleep(0.5)
    app.processEvents()
    win.refresh()
    check("dummy 关闭后勾选被清理", target.hwnd not in table._checked)

    print("[7] 清理 ...")
    controller.restore_all()
    hotkeys.stop()
    tray.hide()
    win.close()
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
